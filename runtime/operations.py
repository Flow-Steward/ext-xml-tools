"""Run one workflow step: check its inputs, read its documents, do the work, save the result."""

from __future__ import annotations

import codecs
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from flowsteward_extension_sdk import stream_artifact_bytes, write_artifact_stream

from . import budget, convert, errors, files, limits, ops_build, sandbox, specs
from .errors import ExtensionError, error_response, invalid, ok_response

_ALLOWED_ACTION_KEYS = frozenset(
    {"action_id", "operation_id", "page_id", "component_id", "context", "input", "target"}
)


def handle_runtime(
    payload: Mapping[str, Any],
    *,
    streamer: files.ArtifactStreamer = stream_artifact_bytes,
    writer: files.ArtifactWriter = write_artifact_stream,
    clock: Callable[[], float] = time.monotonic,
    step_seconds: float = limits.STEP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """One invocation through the closed registry. Nothing outside the step changes."""
    started = clock()
    workdir = tempfile.mkdtemp(prefix="fs-xml-")
    try:
        operation_id, raw_input = _envelope(payload)
        operation = specs.BY_ID.get(operation_id)
        if operation is None:
            raise ExtensionError(
                errors.UNSUPPORTED_OPERATION, "That operation is not part of this extension"
            )
        operation_input = _as_input(raw_input)
        values = check_fields(operation, operation_input)
        end = started + step_seconds - limits.STEP_MARGIN_SECONDS
        sources = read_documents(
            operation, operation_input, values, payload, workdir, streamer, end, clock
        )
        out_path = os.path.join(workdir, "result.out")
        reserve = limits.UPLOAD_RESERVE_SECONDS if operation.file_output else 0.0
        seconds = end - reserve - clock()
        task = {
            "operation_id": operation_id,
            "values": values,
            "sources": {key: _described(value) for key, value in sources.items()},
            "out_path": out_path,
        }
        outcome = sandbox.run_worker(task, seconds=seconds, workdir=workdir)
        result = dict(outcome["result"])
        warnings = list(outcome["warnings"])
        encoding = result.pop("_encoding", None)
        if operation.file_output is not None:
            _attach_file(operation, payload, result, warnings, out_path, encoding, writer)
        for output in operation.outputs:
            result.setdefault(output.name, None)
        result["warnings"] = warnings
        result = convert.plain(result)
        _fit(operation, result)
        return ok_response(result)
    except ExtensionError as exc:
        return error_response(exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 — a safe trace, never the data
        where = sandbox.where(exc)
        print(f"flowsteward.xml internal error: {where}", file=sys.stderr)
        return error_response(
            errors.INTERNAL_ERROR, f"The operation could not be completed ({where})"
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _described(source: Any) -> Any:
    if isinstance(source, list):
        return [_described(item) for item in source]
    return {"label": source.label, "path": source.path, "from_text": source.from_text}


def _envelope(payload: Mapping[str, Any]) -> tuple[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("mode") != "action":
        raise invalid("Request mode must be action")
    action = payload.get("action")
    if not isinstance(action, Mapping) or set(action) - _ALLOWED_ACTION_KEYS:
        raise invalid("The action envelope is invalid")
    operation_id = action.get("action_id") or action.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise invalid("An operation id is required")
    return operation_id.strip(), action.get("input")


def _as_input(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise invalid("The operation input must be an object")
    return {str(k): v for k, v in raw.items() if v is not None and v != ""}


# ---------------------------------------------------------------------------
# Inputs


def _structured(value: Any, field: specs.Field, kind: type) -> Any:
    if isinstance(value, str) and kind is not str:
        try:
            value = json.loads(value, parse_float=Decimal)
        except ValueError:
            raise invalid(f"{field.title} must be JSON") from None
    if not isinstance(value, kind):
        raise invalid(f"{field.title} must be {'a list' if kind is list else 'an object'}")
    return value


def check_field(field: specs.Field, value: Any) -> Any:
    if field.value_type == "text":
        if not isinstance(value, str):
            raise invalid(f"{field.title} must be text")
        if field.max_length is not None and len(value) > field.max_length:
            raise invalid(f"{field.title} is longer than {field.max_length} characters")
        if field.options and value not in field.options:
            raise invalid(f"{field.title} must be one of: {', '.join(field.options)}")
        return value
    if field.value_type == "integer":
        if isinstance(value, Decimal) and value == value.to_integral_value():
            value = int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        if isinstance(value, bool) or not isinstance(value, int):
            raise invalid(f"{field.title} must be a whole number")
        if field.minimum is not None and value < field.minimum:
            raise invalid(f"{field.title} must be at least {field.minimum}")
        if field.maximum is not None and value > field.maximum:
            raise invalid(f"{field.title} must be at most {field.maximum}")
        return value
    if field.value_type == "boolean":
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        if not isinstance(value, bool):
            raise invalid(f"{field.title} must be true or false")
        return value
    if field.value_type == "object":
        return _structured(value, field, dict)
    if field.value_type == "array":
        return _structured(value, field, list)
    return value


def check_fields(operation: specs.Operation, operation_input: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in operation.fields:
        if field.name in operation_input:
            values[field.name] = check_field(field, operation_input[field.name])
        elif field.required:
            raise invalid(f"{field.title} is required")
        else:
            values[field.name] = field.default
    return values


def read_documents(
    operation: specs.Operation,
    operation_input: Mapping[str, Any],
    values: dict[str, Any],
    payload: Mapping[str, Any],
    workdir: str,
    streamer: files.ArtifactStreamer,
    end: float,
    clock: Callable[[], float],
) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for number, document in enumerate(operation.documents):
        text = operation_input.get(document.text_field) if document.text_field else None
        given_file = operation_input.get(document.file_field)
        if document.many:
            sources[document.key] = _many(
                document, text, given_file, payload, workdir, streamer, number
            )
            continue
        if text is not None and given_file is not None:
            raise invalid(f"{document.label}: give the text or the file, not both")
        if text is None and given_file is None:
            if document.required:
                raise invalid(f"{document.label} is required: give the text or a file")
            continue
        if given_file is not None:
            if clock() >= end - limits.UPLOAD_RESERVE_SECONDS:
                raise ExtensionError(errors.TIMEOUT, "The step ran out of time")
            sources[document.key] = files.download(
                payload,
                given_file,
                binding_key=document.file_field,
                label=document.label,
                target_path=os.path.join(workdir, f"in-{number}"),
                max_bytes=document.max_bytes,
                streamer=streamer,
            )
            continue
        if document.kind in {"json", "records"} and not isinstance(text, str):
            values[document.key] = text
            continue
        if not isinstance(text, str):
            raise invalid(f"{document.label} must be text")
        data = _text_bytes(text)
        if len(data) > document.max_bytes:
            raise ExtensionError(
                errors.LIMIT_EXCEEDED,
                f"{document.label} is larger than {document.max_bytes // limits.MiB} MiB",
            )
        sources[document.key] = _text_file(document.label, data, workdir, f"text-{number}")
    return sources


def _text_file(label: str, data: bytes, workdir: str, name: str) -> files.Source:
    path = os.path.join(workdir, name)
    with open(path, "wb") as handle:
        handle.write(data)
    return files.Source(label=label, path=path, from_text=True)


def _many(
    document: specs.Document,
    texts: Any,
    given_files: Any,
    payload: Mapping[str, Any],
    workdir: str,
    streamer: files.ArtifactStreamer,
    number: int,
) -> list[files.Source]:
    if texts is not None and not isinstance(texts, list):
        raise invalid(f"{document.text_field} must be a list of texts")
    if given_files is not None and not isinstance(given_files, list):
        given_files = [given_files]
    found: list[files.Source] = []
    for index, value in enumerate(given_files or []):
        found.append(
            files.download(
                payload,
                value,
                binding_key=document.file_field,
                label=f"Document {len(found) + 1}",
                target_path=os.path.join(workdir, f"in-{number}-{index}"),
                max_bytes=document.max_bytes,
                streamer=streamer,
            )
        )
    for index, value in enumerate(texts or []):
        if not isinstance(value, str):
            raise invalid(f"{document.text_field} must be a list of texts")
        found.append(
            _text_file(
                f"Document {len(found) + 1}", _text_bytes(value), workdir, f"text-{number}-{index}"
            )
        )
    return found


def _text_bytes(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise ExtensionError(
            errors.INVALID_XML_CHARACTER, "The text has a character that is not valid Unicode"
        ) from None


# ---------------------------------------------------------------------------
# Result


def _attach_file(
    operation: specs.Operation,
    payload: Mapping[str, Any],
    result: dict[str, Any],
    warnings: list[str],
    out_path: str,
    encoding: Any,
    writer: files.ArtifactWriter,
) -> None:
    spec = operation.file_output
    if spec is None:
        return
    if not os.path.exists(out_path):
        with open(out_path, "wb"):
            pass
    size = os.path.getsize(out_path)
    result["file"] = files.save(
        payload,
        out_path,
        binding_key="result_file",
        content_type=spec.content_type,
        writer=writer,
    )
    result["size_bytes"] = size
    result["sha256"] = files.sha256_file(out_path)
    if not spec.inline_field:
        return
    result[spec.inline_field] = None
    if size > limits.MAX_INLINE_BYTES:
        warnings.append(
            f"The result is larger than {limits.MAX_INLINE_BYTES // 1024} KiB; "
            "it is only in the file"
        )
        return
    with open(out_path, "rb") as handle:
        data = handle.read()
    if spec.inline_json:
        result[spec.inline_field] = json.loads(data.decode("utf-8"), parse_float=Decimal)
        return
    codec = ops_build.PYTHON_CODECS.get(str(encoding or "UTF-8"), str(encoding or "utf-8"))
    try:
        codecs.lookup(codec)
        result[spec.inline_field] = data.decode(codec)
    except (LookupError, UnicodeDecodeError):
        with contextlib.suppress(UnicodeDecodeError):
            result[spec.inline_field] = data.decode("utf-8")


def _fit(operation: specs.Operation, result: dict[str, Any]) -> None:
    if budget.fits(result):
        return
    spec = operation.file_output
    if spec is not None and spec.inline_field and result.get(spec.inline_field) is not None:
        result[spec.inline_field] = None
        result["warnings"].append(
            "The result is too large for the step output; it is only in the file"
        )
        if budget.fits(result):
            return
    raise budget.too_large()


__all__ = ["check_field", "check_fields", "handle_runtime", "read_documents"]
