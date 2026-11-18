"""An in-memory artifact plane, a request builder and helpers for the tests."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from runtime.operations import handle_runtime

BUNDLE = Path(__file__).resolve().parents[1]


class Plane:
    """Input files by artifact id, and the files the step saved."""

    def __init__(self) -> None:
        self.inputs: dict[str, bytes] = {}
        self.saved: list[dict[str, Any]] = []

    def streamer(
        self, payload: Any, *, artifact_id: str, binding_key: str, timeout_seconds: float
    ) -> Iterable[bytes]:
        data = self.inputs[artifact_id]
        for start in range(0, len(data), 65536):
            yield data[start : start + 65536]

    def writer(self, payload: Any, chunks: Iterable[bytes], **kw: Any) -> dict[str, Any]:
        body = b"".join(chunks)
        self.saved.append({"body": body, **kw})
        number = len(self.saved)
        return {"artifact_handle": f"artifact:out-{number}", "artifact_id": f"out-{number}"}

    @property
    def body(self) -> bytes:
        return self.saved[-1]["body"]


def _entries(value: Any) -> list[tuple[Any, ...]]:
    return list(value) if isinstance(value, list) else [value]


def payload(
    operation_id: str,
    inputs: dict[str, Any] | None = None,
    *,
    files: dict[str, Any] | None = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    """A host request.

    ``files``: input name → (artifact id, bytes[, file name]) or a list of them; the input
    gets the handle (a list of handles for a list).
    """
    action_input = dict(inputs or {})
    descriptors = []
    for name, value in (files or {}).items():
        handles = []
        for entry in _entries(value):
            artifact_id = entry[0]
            handle = f"artifact:{artifact_id}"
            handles.append(handle)
            descriptor = {
                "artifact_id": artifact_id,
                "artifact_handle": handle,
                "binding_key": name,
            }
            if len(entry) > 2:
                descriptor["filename"] = entry[2]
            descriptors.append(descriptor)
        existing = action_input.get(name)
        if isinstance(existing, list):
            existing.extend(handles)
        elif isinstance(value, list) or name.endswith("_files"):
            action_input[name] = handles
        else:
            action_input[name] = handles[0]
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "runtime_context": {
            "group_id": None,
            "for_each_id": None,
            "step_id": "s",
            "step_execution_id": "5c1d2e3f-4a5b-4c6d-8e7f-000000000001",
            "test_mode": test_mode,
        },
        "action": {"action_id": operation_id, "input": action_input},
        "artifacts": {
            "inputs": descriptors,
            "outputs": [
                {
                    "artifact_id": "out",
                    "artifact_handle": "artifact:out",
                    "binding_key": "result_file",
                }
            ],
        },
    }


def run(
    operation_id: str,
    inputs: dict[str, Any] | None = None,
    *,
    files: dict[str, Any] | None = None,
    test_mode: bool = False,
    plane: Plane | None = None,
    step_seconds: float | None = None,
) -> tuple[dict[str, Any], Plane]:
    plane = plane or Plane()
    for value in (files or {}).values():
        for entry in _entries(value):
            plane.inputs[entry[0]] = entry[1]
    request = payload(operation_id, inputs, files=files, test_mode=test_mode)
    kwargs: dict[str, Any] = {"streamer": plane.streamer, "writer": plane.writer}
    if step_seconds is not None:
        kwargs["step_seconds"] = step_seconds
    response = handle_runtime(request, **kwargs)
    json.dumps(response)  # every response is plain JSON
    return response, plane


def ok(operation_id: str, inputs: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    response, _ = run(operation_id, inputs, **kw)
    assert response["ok"] is True, response
    return response["result"]


def ok_with_file(
    operation_id: str, inputs: dict[str, Any] | None = None, **kw: Any
) -> tuple[dict[str, Any], bytes]:
    response, plane = run(operation_id, inputs, **kw)
    assert response["ok"] is True, response
    return response["result"], plane.body


def failed(operation_id: str, inputs: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    response, _ = run(operation_id, inputs, **kw)
    assert response["ok"] is False, response
    assert response["definitely_no_external_effect"] is True
    return response
