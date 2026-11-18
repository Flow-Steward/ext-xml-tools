"""Keep one step's result inside what the host accepts.

The host scans every string of an extension response for reflected secrets and
fails closed past fixed budgets (core ``secret_reflection``: 16,384 candidate
strings — dict keys and string values — 65,536 traversed nodes, 8 M compared
code points), and ``main.py`` caps the serialized response at 4.5 MiB. This module measures a
result the same way, with a margin for the envelope.
"""

from __future__ import annotations

import json
from typing import Any

from . import errors
from .errors import ExtensionError

MAX_CANDIDATE_STRINGS = 12_000
MAX_NODES = 48_000
MAX_CODEPOINTS = 5_000_000
MAX_SERIALIZED_BYTES = 3_500_000


def measure(value: Any) -> tuple[int, int, int, int]:
    """(candidate strings, nodes, string code points, serialized bytes) of ``value``."""
    candidates = nodes = codepoints = 0
    stack: list[Any] = [value]
    while stack:
        node = stack.pop()
        nodes += 1
        if isinstance(node, str):
            candidates += 1
            codepoints += len(node)
        elif isinstance(node, dict):
            for key, nested in node.items():
                nodes += 1
                if isinstance(key, str):
                    candidates += 1
                    codepoints += len(key)
                stack.append(nested)
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    size = len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    )
    return candidates, nodes, codepoints, size


def _within(totals: tuple[int, int, int, int], scale: float = 1.0) -> bool:
    candidates, nodes, codepoints, size = totals
    return (
        candidates <= MAX_CANDIDATE_STRINGS * scale
        and nodes <= MAX_NODES * scale
        and codepoints <= MAX_CODEPOINTS * scale
        and size <= MAX_SERIALIZED_BYTES * scale
    )


def too_large() -> ExtensionError:
    return ExtensionError(
        errors.RESPONSE_TOO_LARGE,
        "This result is too large for one step output. Select less, or use the step "
        "that writes a file (for example Parse large XML to JSONL file).",
    )


def fits(result: Any) -> bool:
    return _within(measure(result))


def ensure_fits(result: Any) -> None:
    if not fits(result):
        raise too_large()


__all__ = ["ensure_fits", "fits", "measure", "too_large"]
