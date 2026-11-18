"""Closed dispatcher for XML Tools.

``specs.OPERATION_IDS`` is the whole routable surface. An operation id that is
not in it is refused before any input is read.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flowsteward_extension_sdk import runtime_context_from_payload

from runtime import errors
from runtime.errors import error_response
from runtime.operations import handle_runtime
from runtime.specs import OPERATION_IDS


def dispatch_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    """Route one runtime invocation, or refuse it."""
    try:
        runtime_context_from_payload(payload)
    except ValueError as exc:
        return error_response(errors.INVALID_HOST_ENVELOPE, str(exc))
    if _operation_id(payload) not in OPERATION_IDS:
        return error_response(
            errors.UNSUPPORTED_OPERATION, "That operation is not part of this extension"
        )
    return handle_runtime(payload)


def _operation_id(payload: Any) -> str:
    if not isinstance(payload, Mapping) or payload.get("mode") != "action":
        return ""
    action = payload.get("action")
    if not isinstance(action, Mapping):
        return ""
    value = action.get("action_id") or action.get("operation_id")
    return value.strip() if isinstance(value, str) else ""


__all__ = ["OPERATION_IDS", "dispatch_runtime"]
