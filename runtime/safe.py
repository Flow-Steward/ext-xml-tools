"""The one way this extension reads XML: a locked-down lxml parser.

- No DTD at all: a document with ``<!DOCTYPE`` is refused, so no entity is ever
  defined, expanded or fetched (billion laughs, quadratic blowup, XXE).
- No network, no XInclude (``xinclude()`` is never called), no huge-tree mode.
- Limits on size, nesting depth and the number of elements.
- Parse errors give the line and column and at most a short fragment.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO, Any

from lxml import etree

from . import errors, limits
from .errors import ExtensionError

#: Every parser option that matters for safety, set explicitly.
SAFE_PARSER_OPTIONS: dict[str, Any] = {
    "resolve_entities": False,
    "no_network": True,
    "load_dtd": False,
    "dtd_validation": False,
    "attribute_defaults": False,
    "huge_tree": False,
    "collect_ids": False,
    "recover": False,
}

_DECLARATION = re.compile(
    rb"^(?:\xef\xbb\xbf)?<\?xml\s[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._:-]{1,40})[\"']"
)
_VERSION = re.compile(rb"^(?:\xef\xbb\xbf)?<\?xml\s[^>]*?version\s*=\s*[\"']([0-9.]{1,5})[\"']")


class _NoLoading(etree.Resolver):
    """Answers every load (a DTD, a schema, a stylesheet) with nothing."""

    def resolve(self, system_url: str, public_id: str, context: Any) -> Any:
        return self.resolve_string("", context)


def make_parser(
    *,
    encoding: str | None = None,
    remove_comments: bool = False,
    remove_blank_text: bool = False,
    resolver: etree.Resolver | None = None,
) -> etree.XMLParser:
    """The safe parser. Its one resolver answers every load: with nothing by default, or
    only from a given set (a schema's files)."""
    parser = etree.XMLParser(
        encoding=encoding,
        remove_comments=remove_comments,
        remove_blank_text=remove_blank_text,
        strip_cdata=False,
        **SAFE_PARSER_OPTIONS,
    )
    parser.resolvers.add(resolver if resolver is not None else _NoLoading())
    return parser


def syntax_message(exc: BaseException, what: str = "The XML") -> str:
    """``The XML is not well-formed at line 3, column 7: <reason>``."""
    position = getattr(exc, "position", None)
    line, column = (position if isinstance(position, tuple) else (None, None))[:2]
    reason = getattr(exc, "msg", None) or str(exc)
    reason = re.sub(r",? line \d+, column \d+\s*$", "", str(reason))
    where = f" at line {line}, column {column}" if line else ""
    return f"{what} is not well-formed{where}: {errors.safe_text(reason, 160)}"


def error_position(exc: BaseException) -> tuple[int | None, int | None]:
    position = getattr(exc, "position", None)
    if isinstance(position, tuple) and len(position) >= 2:
        return int(position[0]), int(position[1])
    return None, None


def declared_encoding(head: bytes) -> str | None:
    match = _DECLARATION.match(head[:200])
    return match.group(1).decode("ascii") if match else None


def declared_version(head: bytes) -> str | None:
    match = _VERSION.match(head[:200])
    return match.group(1).decode("ascii") if match else None


def _refuse_dtd(tree: Any, what: str) -> None:
    info = tree.docinfo
    if info.doctype or info.internalDTD is not None or info.externalDTD is not None:
        raise ExtensionError(
            errors.DTD_FORBIDDEN,
            f"{what} has a DOCTYPE declaration; DTDs and entities are not allowed",
        )


def check_shape(
    root: Any, what: str = "The XML", *, max_elements: int = limits.MAX_ELEMENTS
) -> tuple[int, int]:
    """Element count and depth of a parsed tree, within the limits."""
    depth = deepest = count = 0
    for event, node in etree.iterwalk(root, events=("start", "end")):
        if event == "start":
            depth += 1
            count += 1
            deepest = max(deepest, depth)
            if depth > limits.MAX_DEPTH:
                raise ExtensionError(
                    errors.LIMIT_EXCEEDED,
                    f"{what} nests elements deeper than {limits.MAX_DEPTH} levels",
                )
            if count > max_elements:
                raise ExtensionError(
                    errors.LIMIT_EXCEEDED, f"{what} has more than {max_elements} elements"
                )
        else:
            depth -= 1
    return count, deepest


def parse_document(
    data: bytes,
    *,
    what: str = "The XML",
    from_text: bool = False,
    remove_comments: bool = False,
    remove_blank_text: bool = False,
    max_bytes: int = limits.MAX_DOCUMENT_BYTES,
) -> Any:
    """Parse a whole document safely; its ElementTree.

    ``from_text``: the bytes are UTF-8 text typed into the step, whatever its
    declaration says.
    """
    if len(data) > max_bytes:
        raise ExtensionError(
            errors.LIMIT_EXCEEDED, f"{what} is larger than {max_bytes // limits.MiB} MiB"
        )
    if not data.strip():
        raise ExtensionError(errors.INVALID_XML, f"{what} is empty")
    parser = make_parser(
        encoding="utf-8" if from_text else None,
        remove_comments=remove_comments,
        remove_blank_text=remove_blank_text,
    )
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        if b"<!DOCTYPE" in data[:65536] or b"<\x00!\x00D\x00O\x00C\x00" in data[:131072]:
            raise ExtensionError(
                errors.DTD_FORBIDDEN,
                f"{what} has a DOCTYPE declaration; DTDs and entities are not allowed",
            ) from None
        raise ExtensionError(errors.INVALID_XML, syntax_message(exc, what)) from None
    except (ValueError, LookupError) as exc:
        raise ExtensionError(
            errors.INVALID_XML, f"{what} cannot be read: {errors.safe_text(exc, 160)}"
        ) from None
    tree = root.getroottree()
    _refuse_dtd(tree, what)
    check_shape(root, what)
    return tree


def text_bytes(text: str) -> bytes:
    """Text typed into a step, as UTF-8 bytes (lone surrogates refused)."""
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise ExtensionError(
            errors.INVALID_XML_CHARACTER, "The text has a character that is not valid Unicode"
        ) from None


@dataclass
class StreamInfo:
    root_tag: str | None = None
    root_attrib: dict[str, str] | None = None
    root_nsmap: dict[str | None, str] | None = None
    encoding: str | None = None
    elements: int = 0


def iter_records(
    stream: IO[bytes],
    matches: Any,
    info: StreamInfo,
    *,
    what: str = "The XML",
    check_time: Any = None,
    keep_header: bool = False,
) -> Iterator[Any]:
    """Elements for which ``matches(element)`` is true, complete, outermost only.

    Memory stays bounded: each record is cleared after the caller has used it,
    and so are the elements around records. With ``keep_header`` the elements
    before the first record stay (up to a bound) so the caller can copy them.
    """
    context = etree.iterparse(
        stream,
        events=("start", "end"),
        strip_cdata=False,
        remove_comments=False,
        **SAFE_PARSER_OPTIONS,
    )
    seen_record = False
    depth = 0
    record_depth = 0
    record_elements = 0
    try:
        for event, element in context:
            if event == "start":
                depth += 1
                info.elements += 1
                if depth == 1:
                    tree = element.getroottree()
                    _refuse_dtd(tree, what)
                    info.root_tag = element.tag
                    info.root_attrib = dict(element.attrib)
                    info.root_nsmap = dict(element.nsmap)
                    info.encoding = tree.docinfo.encoding
                if depth > limits.MAX_DEPTH:
                    raise ExtensionError(
                        errors.LIMIT_EXCEEDED,
                        f"{what} nests elements deeper than {limits.MAX_DEPTH} levels",
                    )
                if record_depth:
                    record_elements += 1
                    if record_elements > limits.MAX_RECORD_ELEMENTS:
                        raise ExtensionError(
                            errors.LIMIT_EXCEEDED,
                            f"One record has more than {limits.MAX_RECORD_ELEMENTS} elements",
                        )
                elif depth > 1 and matches(element):
                    record_depth = depth
                    record_elements = 1
                if check_time is not None and info.elements % 4096 == 0:
                    check_time()
                continue
            if record_depth and depth == record_depth:
                record_depth = 0
                seen_record = True
                yield element
                _release(element)
            elif not record_depth and depth > 1 and not _keeping(keep_header, seen_record, info):
                _release(element)
            depth -= 1
    except etree.XMLSyntaxError as exc:
        raise ExtensionError(errors.INVALID_XML, syntax_message(exc, what)) from None
    except (ValueError, LookupError) as exc:
        raise ExtensionError(
            errors.INVALID_XML, f"{what} cannot be read: {errors.safe_text(exc, 160)}"
        ) from None
    if info.root_tag is None:
        raise ExtensionError(errors.INVALID_XML, f"{what} is empty")


#: Elements before the first record kept for the caller to copy (Split).
MAX_HEADER_ELEMENTS = 10_000


def _keeping(keep_header: bool, seen_record: bool, info: StreamInfo) -> bool:
    return keep_header and not seen_record and info.elements <= MAX_HEADER_ELEMENTS


def _release(element: Any) -> None:
    """Drop a finished element and the finished siblings before it."""
    element.clear(keep_tail=True)
    parent = element.getparent()
    if parent is None:
        return
    previous = element.getprevious()
    while previous is not None:
        parent.remove(previous)
        previous = element.getprevious()


def open_bytes(data: bytes) -> IO[bytes]:
    return io.BytesIO(data)


__all__ = [
    "SAFE_PARSER_OPTIONS",
    "StreamInfo",
    "check_shape",
    "declared_encoding",
    "declared_version",
    "error_position",
    "iter_records",
    "make_parser",
    "parse_document",
    "syntax_message",
    "text_bytes",
]
