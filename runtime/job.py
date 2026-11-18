"""One step's work: its checked inputs, its documents and where its result goes."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from lxml import etree

from . import convert, errors, limits, safe
from .errors import ExtensionError
from .files import Source


@dataclass
class Job:
    values: dict[str, Any]
    sources: dict[str, Any]
    out_path: str
    deadline: float = float("inf")
    warnings: list[str] = field(default_factory=list)
    clock: Callable[[], float] = time.monotonic

    def check_time(self) -> None:
        if self.clock() >= self.deadline:
            raise ExtensionError(errors.TIMEOUT, "The step ran out of time")

    def source(self, key: str) -> Source:
        value = self.sources.get(key)
        if not isinstance(value, Source):
            raise errors.invalid(f"{key} is required")
        return value

    def optional_source(self, key: str) -> Source | None:
        value = self.sources.get(key)
        return value if isinstance(value, Source) else None

    def tree(self, key: str = "xml", **options: Any) -> Any:
        source = self.source(key)
        data = source.read(limits.MAX_DOCUMENT_BYTES)
        return safe.parse_document(data, what=source.label, from_text=source.from_text, **options)

    def encoding(self, key: str, tree: Any) -> str:
        """The document's encoding: as declared, else as read (UTF-8 for typed text)."""
        source = self.source(key)
        with source.open() as stream:
            head = stream.read(200)
        if head.startswith((b"\xff\xfe", b"\xfe\xff")):
            return "UTF-16"
        return safe.declared_encoding(head) or tree.docinfo.encoding or "UTF-8"

    def namespaces(self) -> dict[str, str]:
        mapping = convert.check_namespaces(self.values.get("namespaces"))
        if None in mapping:
            raise errors.invalid(
                "namespaces: XPath 1.0 has no default namespace; give the namespace a prefix"
            )
        return {str(k): v for k, v in mapping.items()}

    def warn(self, message: str) -> None:
        if len(self.warnings) < 50:
            self.warnings.append(errors.safe_text(message, 300))


def write_xml(
    node: Any,
    path: str,
    *,
    encoding: str = "UTF-8",
    declaration: bool = True,
    pretty: bool = False,
) -> None:
    """Serialize ``node`` (an element or a tree) to ``path``.

    The declaration is written with double quotes, as most feed readers expect.
    """
    utf16 = encoding.upper().startswith("UTF-16")
    try:
        data = etree.tostring(
            node, encoding=encoding, xml_declaration=declaration and utf16, pretty_print=pretty
        )
        if declaration and not utf16:
            data = f'<?xml version="1.0" encoding="{encoding}"?>\n'.encode("ascii") + data
    except (ValueError, LookupError) as exc:
        raise ExtensionError(
            errors.INVALID_STRUCTURE, f"The XML could not be written: {errors.safe_text(exc, 160)}"
        ) from None
    with open(path, "wb") as handle:
        handle.write(data)


def compile_xpath(
    expression: Any, namespaces: Mapping[str, str], what: str = "XPath", *, nodes: bool = False
) -> Any:
    """An XPath 1.0 expression without extension functions.

    ``nodes``: attribute and text results keep their link to their element (for Edit).
    """
    if not isinstance(expression, str) or not expression.strip():
        raise errors.invalid(f"{what} is required")
    try:
        return etree.XPath(
            expression, namespaces=dict(namespaces), regexp=False, smart_strings=nodes
        )
    except etree.XPathSyntaxError as exc:
        raise ExtensionError(
            errors.INVALID_XPATH, f"{what} is not valid XPath 1.0: {errors.safe_text(exc, 160)}"
        ) from None


def run_xpath(compiled: Any, node: Any, what: str = "XPath", **variables: Any) -> Any:
    try:
        return compiled(node, **variables)
    except etree.XPathEvalError as exc:
        raise ExtensionError(
            errors.INVALID_XPATH,
            f"{what} could not be evaluated: {errors.safe_text(exc, 160)}",
        ) from None


def load_json(source: Source) -> Any:
    data = source.read(limits.MAX_DOCUMENT_BYTES)
    try:
        return json.loads(data.decode("utf-8-sig"), parse_float=Decimal)
    except (UnicodeDecodeError, ValueError) as exc:
        line = getattr(exc, "lineno", None)
        column = getattr(exc, "colno", None)
        where = f" at line {line}, column {column}" if line else ""
        raise ExtensionError(
            errors.INVALID_JSON, f"{source.label} is not valid JSON{where}"
        ) from None


def matcher(record_element: Any, namespaces: Mapping[str, str]) -> Callable[[Any], bool]:
    """A test for the record element: ``name`` (any namespace), ``p:name`` or ``{uri}name``."""
    if not isinstance(record_element, str) or not record_element.strip():
        raise errors.invalid("Record element is required")
    name = record_element.strip()
    if name.startswith("{"):
        uri, _, local = name[1:].partition("}")
        convert.check_name(local, "Record element")
        clark = f"{{{uri}}}{local}"
        return lambda element: element.tag == clark
    convert.check_name(name, "Record element")
    if ":" in name:
        prefix, local = name.split(":", 1)
        if prefix not in namespaces:
            raise ExtensionError(
                errors.INVALID_NAME,
                f"Record element: the prefix {prefix!r} is not in Namespace prefixes",
            )
        clark = f"{{{namespaces[prefix]}}}{local}"
        return lambda element: element.tag == clark
    suffix = "}" + name
    return lambda element: (
        isinstance(element.tag, str) and (element.tag == name or element.tag.endswith(suffix))
    )


__all__ = ["Job", "compile_xpath", "load_json", "matcher", "run_xpath", "write_xml"]
