"""Stable, safe error vocabulary for XML Tools.

Every failure maps to exactly one of these codes. A message never quotes more of
the document than a short fragment: parse errors give the line and column.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

INVALID_PAYLOAD = "invalid_payload"
INVALID_HOST_ENVELOPE = "invalid_host_envelope"
UNSUPPORTED_OPERATION = "unsupported_operation"
INVALID_XML = "invalid_xml"
DTD_FORBIDDEN = "dtd_forbidden"
LIMIT_EXCEEDED = "limit_exceeded"
TIMEOUT = "timeout"
INVALID_XPATH = "invalid_xpath"
NO_MATCH = "no_match"
INVALID_SCHEMA = "invalid_schema"
SCHEMA_IMPORT_REFUSED = "schema_import_refused"
INVALID_STYLESHEET = "invalid_stylesheet"
TRANSFORM_FAILED = "transform_failed"
INVALID_JSON = "invalid_json"
INVALID_CSV = "invalid_csv"
INVALID_XLSX = "invalid_xlsx"
INVALID_STRUCTURE = "invalid_structure"
INVALID_XML_CHARACTER = "invalid_xml_character"
INVALID_NAME = "invalid_name"
RESPONSE_TOO_LARGE = "response_too_large"
ARTIFACT_INPUT_UNAVAILABLE = "artifact_input_unavailable"
ARTIFACT_OUTPUT_UNAVAILABLE = "artifact_output_unavailable"
INTERNAL_ERROR = "internal_error"

#: Codes every step can return, in the order the contracts list them.
COMMON_ERROR_CODES: tuple[str, ...] = (
    INVALID_PAYLOAD,
    INVALID_HOST_ENVELOPE,
    UNSUPPORTED_OPERATION,
    LIMIT_EXCEEDED,
    TIMEOUT,
    RESPONSE_TOO_LARGE,
    INTERNAL_ERROR,
)

SAFE_ERROR_CODES: frozenset[str] = frozenset(
    {
        INVALID_PAYLOAD,
        INVALID_HOST_ENVELOPE,
        UNSUPPORTED_OPERATION,
        INVALID_XML,
        DTD_FORBIDDEN,
        LIMIT_EXCEEDED,
        TIMEOUT,
        INVALID_XPATH,
        NO_MATCH,
        INVALID_SCHEMA,
        SCHEMA_IMPORT_REFUSED,
        INVALID_STYLESHEET,
        TRANSFORM_FAILED,
        INVALID_JSON,
        INVALID_CSV,
        INVALID_XLSX,
        INVALID_STRUCTURE,
        INVALID_XML_CHARACTER,
        INVALID_NAME,
        RESPONSE_TOO_LARGE,
        ARTIFACT_INPUT_UNAVAILABLE,
        ARTIFACT_OUTPUT_UNAVAILABLE,
        INTERNAL_ERROR,
    }
)

_MAX_MESSAGE = 600
#: The longest piece of a word (a name, a value) a message may quote.
MAX_FRAGMENT = 40
_LONG_TOKEN = re.compile(r"\S{%d,}" % (MAX_FRAGMENT + 1))


def safe_text(value: object, limit: int = 300) -> str:
    """Printable, single-line, bounded text; long words cut to a short fragment."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = " ".join(text.split())
    text = _LONG_TOKEN.sub(lambda m: m.group(0)[:MAX_FRAGMENT] + "…", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ExtensionError(Exception):
    """A failure already reduced to a safe code and a safe message."""

    def __init__(self, code: str, message: str) -> None:
        safe_code = code if code in SAFE_ERROR_CODES else INTERNAL_ERROR
        bounded = safe_text(message, _MAX_MESSAGE)
        super().__init__(bounded)
        self.code = safe_code
        self.message = bounded


def invalid(message: str) -> ExtensionError:
    return ExtensionError(INVALID_PAYLOAD, message)


def error_response(code: str, message: str) -> dict[str, object]:
    """The failing runtime envelope for one safe error. Nothing outside changed."""
    safe_code = code if code in SAFE_ERROR_CODES else INTERNAL_ERROR
    bounded = safe_text(message, _MAX_MESSAGE)
    return {
        "ok": False,
        "result": {},
        "error_code": safe_code,
        "error": bounded,
        "errors": [{"code": safe_code, "message": bounded}],
        "definitely_no_external_effect": True,
        "external_effect_status": "failed",
    }


def ok_response(result: Mapping[str, object]) -> dict[str, object]:
    return {"ok": True, "result": dict(result), "error_code": None, "error": None, "errors": []}


__all__ = [
    "COMMON_ERROR_CODES",
    "SAFE_ERROR_CODES",
    "ExtensionError",
    "error_response",
    "invalid",
    "ok_response",
    "safe_text",
]
