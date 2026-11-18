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


def payload(
    operation_id: str,
    inputs: dict[str, Any] | None = None,
    *,
    files: dict[str, tuple[str, bytes]] | None = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    """A host request. ``files``: input name → (artifact id, bytes); the input gets the handle."""
    action_input = dict(inputs or {})
    descriptors = []
    for name, (artifact_id, _data) in (files or {}).items():
        handle = f"artifact:{artifact_id}"
        existing = action_input.get(name)
        if isinstance(existing, list):
            existing.append(handle)
        elif name.endswith("_files"):
            action_input[name] = [handle]
        else:
            action_input[name] = handle
        descriptors.append(
            {"artifact_id": artifact_id, "artifact_handle": handle, "binding_key": name}
        )
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
    files: dict[str, tuple[str, bytes]] | None = None,
    test_mode: bool = False,
    plane: Plane | None = None,
    step_seconds: float | None = None,
) -> tuple[dict[str, Any], Plane]:
    plane = plane or Plane()
    for artifact_id, data in (files or {}).values():
        plane.inputs[artifact_id] = data
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
