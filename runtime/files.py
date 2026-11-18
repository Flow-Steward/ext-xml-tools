"""Inputs given as text or as a workflow file, and the result file a step saves."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import IO, Any

from . import errors, limits
from .errors import ExtensionError

ArtifactStreamer = Callable[..., Iterable[bytes]]
ArtifactWriter = Callable[..., Mapping[str, Any]]

_CHUNK = 1024 * 1024


@dataclass
class Source:
    """One document: typed text (UTF-8) or a downloaded file."""

    label: str
    data: bytes | None = None
    path: str | None = None
    from_text: bool = False

    @property
    def size(self) -> int:
        if self.data is not None:
            return len(self.data)
        return os.path.getsize(self.path) if self.path else 0

    def read(self, max_bytes: int = limits.MAX_DOCUMENT_BYTES) -> bytes:
        if self.size > max_bytes:
            raise ExtensionError(
                errors.LIMIT_EXCEEDED,
                f"{self.label} is larger than {max_bytes // limits.MiB} MiB",
            )
        if self.data is not None:
            return self.data
        with open(self.path or "", "rb") as handle:
            return handle.read()

    def open(self) -> IO[bytes]:
        if self.data is not None:
            return io.BytesIO(self.data)
        return open(self.path or "", "rb")


def handle_of(value: Any) -> str:
    """The artifact handle a step input holds (a handle text or a file record)."""
    if isinstance(value, Mapping):
        value = value.get("artifact_handle") or value.get("artifact_id") or value.get("handle")
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise errors.invalid("A file input must be a workflow file (an artifact handle)")
    return value.strip()


def _bare(handle: str) -> str:
    return handle.split(":", 1)[1] if handle.startswith("artifact:") else handle


def find_descriptor(payload: Mapping[str, Any], handle: str, binding_key: str) -> Mapping[str, Any]:
    """The host's read grant for ``handle``."""
    collection = payload.get("artifacts")
    rows = collection.get("inputs") if isinstance(collection, Mapping) else None
    descriptors = [row for row in rows or [] if isinstance(row, Mapping)]
    bare = _bare(handle)
    for row in descriptors:
        row_handle = str(row.get("artifact_handle") or "").strip()
        row_id = str(row.get("artifact_id") or "").strip()
        if handle in {row_handle, row_id} or bare in {_bare(row_handle), row_id}:
            return row
    same_key = [row for row in descriptors if str(row.get("binding_key") or "") == binding_key]
    if len(same_key) == 1:
        return same_key[0]
    raise ExtensionError(
        errors.ARTIFACT_INPUT_UNAVAILABLE,
        "The workflow file given to this step is not available to it",
    )


def download(
    payload: Mapping[str, Any],
    value: Any,
    *,
    binding_key: str,
    label: str,
    target_path: str,
    max_bytes: int,
    streamer: ArtifactStreamer,
) -> Source:
    """Copy one input file to ``target_path`` (bounded); its Source."""
    handle = handle_of(value)
    descriptor = find_descriptor(payload, handle, binding_key)
    artifact_id = str(descriptor.get("artifact_id") or _bare(handle))
    written = 0
    try:
        with open(target_path, "wb") as handle_out:
            for chunk in streamer(
                dict(payload),
                artifact_id=artifact_id,
                binding_key=binding_key,
                timeout_seconds=limits.DOWNLOAD_TIMEOUT_SECONDS,
            ):
                written += len(chunk)
                if written > max_bytes:
                    raise ExtensionError(
                        errors.LIMIT_EXCEEDED,
                        f"{label} is larger than {max_bytes // limits.MiB} MiB",
                    )
                handle_out.write(chunk)
    except ExtensionError:
        with contextlib.suppress(OSError):
            os.unlink(target_path)
        raise
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(target_path)
        raise ExtensionError(
            errors.ARTIFACT_INPUT_UNAVAILABLE, f"{label} could not be read from the workflow file"
        ) from None
    return Source(label=label, path=target_path)


def file_chunks(path: str) -> Iterator[bytes]:
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            yield chunk


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    for chunk in file_chunks(path):
        digest.update(chunk)
    return digest.hexdigest()


def save(
    payload: Mapping[str, Any],
    path: str,
    *,
    binding_key: str,
    content_type: str,
    writer: ArtifactWriter,
) -> tuple[str, str]:
    """Save the result file as a workflow file; its handle and artifact id."""
    try:
        written = writer(
            dict(payload),
            file_chunks(path),
            binding_key=binding_key,
            content_type=content_type,
            timeout_seconds=limits.UPLOAD_RESERVE_SECONDS,
        )
    except ExtensionError:
        raise
    except Exception:
        raise ExtensionError(
            errors.ARTIFACT_OUTPUT_UNAVAILABLE, "The result file could not be saved"
        ) from None
    handle = written.get("artifact_handle") if isinstance(written, Mapping) else None
    if not isinstance(handle, str) or not handle.strip():
        raise ExtensionError(
            errors.ARTIFACT_OUTPUT_UNAVAILABLE, "The result file could not be saved"
        )
    artifact_id = written.get("artifact_id") if isinstance(written, Mapping) else None
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        artifact_id = _bare(handle.strip())
    return handle.strip(), artifact_id.strip()


__all__ = ["Source", "download", "file_chunks", "find_descriptor", "handle_of", "save"]
