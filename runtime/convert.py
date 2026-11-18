"""XML elements to JSON values and back, by one documented convention.

- An element without attributes and children is its text (``null`` when empty).
- Otherwise it is an object: attributes as ``"@name"``, text as ``"#text"``,
  child elements by name; a name that repeats becomes a list.
- Text between child elements (mixed content) keeps its order in ``"#mixed"``:
  a list of texts and one-key objects.
- Namespaces: ``keep`` writes names as the document's ``prefix:local`` and the
  declarations as ``"@xmlns"`` / ``"@xmlns:p"``; ``strip`` keeps local names only.
- Comments and processing instructions are left out.

Building XML reads the same shape; ``"#cdata"`` writes its text as CDATA.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from lxml import etree

from . import errors
from .errors import ExtensionError

#: Characters XML 1.0 does not allow anywhere in a document.
_INVALID_XML_CHAR = re.compile("[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]")
_NAME_START = r"A-Z_a-zÀ-ÖØ-öø-˿Ͱ-ͽͿ-῿‌-‍⁰-↏Ⰰ-⿯、-퟿豈-﷏ﷰ-�"  # noqa: E501
_NAME_CHAR = _NAME_START + r"\-.0-9·̀-ͯ‿-⁀"
NCNAME = re.compile(f"^[{_NAME_START}][{_NAME_CHAR}]*$")


@dataclass
class JsonOptions:
    attribute_prefix: str = "@"
    text_key: str = "#text"
    namespaces: str = "keep"
    force_list: frozenset[str] = field(default_factory=frozenset)


@dataclass
class Counter:
    skipped_comments: int = 0


def options_from(operation_input: Mapping[str, Any]) -> JsonOptions:
    prefix = operation_input.get("attribute_prefix")
    prefix = "@" if prefix is None else prefix
    text_key = operation_input.get("text_key") or "#text"
    namespaces = operation_input.get("namespace_mode") or "keep"
    force = operation_input.get("force_list") or []
    if not isinstance(prefix, str) or len(prefix) > 5:
        raise errors.invalid("attribute_prefix must be a short text, for example @")
    if not isinstance(text_key, str) or not text_key or len(text_key) > 20:
        raise errors.invalid("text_key must be a short text, for example #text")
    if namespaces not in {"keep", "strip"}:
        raise errors.invalid("namespace_mode must be keep or strip")
    if not isinstance(force, list) or not all(isinstance(n, str) for n in force):
        raise errors.invalid("force_list must be a list of element names")
    return JsonOptions(prefix, text_key, namespaces, frozenset(force))


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def display_name(node: Any, tag: str, options: JsonOptions) -> str:
    """``prefix:local`` as the document writes it (or the local name)."""
    local = local_name(tag)
    if options.namespaces == "strip" or not tag.startswith("{"):
        return local
    prefix = getattr(node, "prefix", None)
    if prefix is None:
        uri = tag[1:].split("}", 1)[0]
        for key, value in (node.nsmap or {}).items():
            if value == uri and key:
                prefix = key
                break
    return f"{prefix}:{local}" if prefix else local


def _attribute_name(element: Any, name: str, options: JsonOptions) -> str:
    if not name.startswith("{"):
        return name
    uri, local = name[1:].split("}", 1)
    if options.namespaces == "strip":
        return local
    if uri == "http://www.w3.org/XML/1998/namespace":
        return f"xml:{local}"
    for key, value in (element.nsmap or {}).items():
        if value == uri and key:
            return f"{key}:{local}"
    return local


def _declared_here(element: Any) -> dict[str | None, str]:
    parent = element.getparent()
    inherited = parent.nsmap if parent is not None else {}
    return {k: v for k, v in element.nsmap.items() if inherited.get(k) != v}


def _is_element(node: Any) -> bool:
    return isinstance(node.tag, str)


def element_to_json(element: Any, options: JsonOptions, counter: Counter | None = None) -> Any:
    """The JSON value of one element (its name is the caller's key)."""
    counter = counter if counter is not None else Counter()
    result: dict[str, Any] = {}
    p = options.attribute_prefix
    if options.namespaces == "keep":
        for prefix, uri in _declared_here(element).items():
            result[f"{p}xmlns:{prefix}" if prefix else f"{p}xmlns"] = uri
    for name, value in element.attrib.items():
        result[p + _attribute_name(element, name, options)] = value
    children = [child for child in element if _is_element(child)]
    counter.skipped_comments += sum(1 for child in element if not _is_element(child))
    text = element.text or ""
    tails = [child.tail or "" for child in element]
    mixed = bool(children) and any(part.strip() for part in [text, *tails])
    if mixed:
        sequence: list[Any] = []
        if text:
            sequence.append(text)
        for child in element:
            if _is_element(child):
                name = display_name(child, child.tag, options)
                sequence.append({name: element_to_json(child, options, counter)})
            if child.tail:
                sequence.append(child.tail)
        result["#mixed"] = sequence
        return result
    for child in children:
        name = display_name(child, child.tag, options)
        value = element_to_json(child, options, counter)
        if name in result:
            existing = result[name]
            if isinstance(existing, list) and name in _lists(result):
                existing.append(value)
            else:
                result[name] = [existing, value]
                _lists(result).add(name)
        elif name in options.force_list:
            result[name] = [value]
            _lists(result).add(name)
        else:
            result[name] = value
    result.pop(_LISTS, None)
    if not children:
        if not result:
            return text if text else None
        if text:
            result[options.text_key] = text
    elif text.strip():
        result[options.text_key] = text
    return result


_LISTS = "\x00lists"


def _lists(result: dict[str, Any]) -> set[str]:
    marker = result.get(_LISTS)
    if not isinstance(marker, set):
        marker = set()
        result[_LISTS] = marker
    return marker


def document_to_json(root: Any, options: JsonOptions, counter: Counter | None = None) -> Any:
    name = display_name(root, root.tag, options)
    return {name: element_to_json(root, options, counter)}


def flatten(value: Any, prefix: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    """Nested objects and lists as ``a.b`` / ``a.0`` keys with scalar values."""
    out = {} if out is None else out
    if isinstance(value, Mapping):
        if not value and prefix:
            out[prefix] = None
        for key, item in value.items():
            flatten(item, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(value, list):
        if not value and prefix:
            out[prefix] = None
        for index, item in enumerate(value):
            flatten(item, f"{prefix}.{index}" if prefix else str(index), out)
    else:
        out[prefix or "value"] = value
    return out


# ---------------------------------------------------------------------------
# Building


def check_text(value: str, where: str) -> str:
    """``value`` when every character is allowed in XML 1.0."""
    match = _INVALID_XML_CHAR.search(value)
    if match:
        raise ExtensionError(
            errors.INVALID_XML_CHARACTER,
            f"{where} has the character U+{ord(match.group(0)):04X}, which XML 1.0 does not allow",
        )
    return value


def scalar_text(value: Any, where: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, str):
        return check_text(value, where)
    raise ExtensionError(errors.INVALID_STRUCTURE, f"{where} must be a text, number or boolean")


def check_name(name: str, where: str) -> str:
    """A valid XML name, optionally ``prefix:local``."""
    if not isinstance(name, str):
        raise ExtensionError(errors.INVALID_NAME, f"{where}: a name must be text")
    parts = name.split(":")
    if len(name) > 200 or len(parts) > 2:
        raise ExtensionError(
            errors.INVALID_NAME, f"{where}: {errors.safe_text(name, 60)!r} is not a valid XML name"
        )
    if not all(NCNAME.match(part) for part in parts):
        raise ExtensionError(
            errors.INVALID_NAME, f"{where}: {errors.safe_text(name, 60)!r} is not a valid XML name"
        )
    return name


class Namespaces:
    """Prefix → URI in scope while building."""

    def __init__(self, declared: Mapping[str | None, str] | None = None) -> None:
        self.map: dict[str | None, str] = dict(declared or {})

    def child(self, extra: Mapping[str | None, str]) -> Namespaces:
        merged = dict(self.map)
        merged.update(extra)
        return Namespaces(merged)

    def tag(self, name: str, where: str, *, attribute: bool = False) -> str:
        check_name(name, where)
        if ":" not in name:
            if attribute or None not in self.map:
                return name
            return f"{{{self.map[None]}}}{name}"
        prefix, local = name.split(":", 1)
        if prefix == "xml":
            return f"{{http://www.w3.org/XML/1998/namespace}}{local}"
        uri = self.map.get(prefix)
        if not uri:
            raise ExtensionError(
                errors.INVALID_NAME, f"{where}: the prefix {prefix!r} has no namespace declared"
            )
        return f"{{{uri}}}{local}"


def check_namespaces(raw: Any, where: str = "namespaces") -> dict[str | None, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or len(raw) > 100:
        raise errors.invalid(f"{where} must be an object of prefix → namespace URI")
    result: dict[str | None, str] = {}
    for prefix, uri in raw.items():
        if not isinstance(uri, str) or not uri or len(uri) > 1000:
            raise errors.invalid(f"{where}: each namespace URI must be a non-empty text")
        check_text(uri, where)
        if prefix in {"", None}:
            result[None] = uri
            continue
        if (
            not isinstance(prefix, str)
            or not NCNAME.match(prefix)
            or prefix.lower().startswith("xml")
        ):
            raise errors.invalid(f"{where}: {errors.safe_text(prefix, 40)!r} is not a valid prefix")
        result[prefix] = uri
    return result


def json_to_element(
    name: str,
    value: Any,
    *,
    parent: Any,
    scope: Namespaces,
    options: JsonOptions,
    where: str,
) -> Any:
    """Append the element(s) ``name`` built from ``value`` to ``parent`` (or make a root)."""
    if isinstance(value, list):
        made = None
        for index, item in enumerate(value):
            if isinstance(item, list):
                raise ExtensionError(
                    errors.INVALID_STRUCTURE, f"{where}[{index}] is a list inside a list"
                )
            made = json_to_element(
                name, item, parent=parent, scope=scope, options=options, where=f"{where}[{index}]"
            )
        return made
    declared: dict[str | None, str] = {}
    p = options.attribute_prefix
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == f"{p}xmlns" or (isinstance(key, str) and key.startswith(f"{p}xmlns:")):
                prefix = None if key == f"{p}xmlns" else key.split(":", 1)[1]
                declared.update(check_namespaces({prefix or "": item}, where))
    local_scope = scope.child(declared)
    tag = local_scope.tag(name, where)
    nsmap = {k: v for k, v in declared.items()}
    element = (
        etree.SubElement(parent, tag, nsmap=nsmap or None)
        if parent is not None
        else etree.Element(tag, nsmap=nsmap or None)
    )
    if value is None:
        return element
    if not isinstance(value, Mapping):
        element.text = scalar_text(value, where)
        return element
    for key, item in value.items():
        if not isinstance(key, str):
            raise ExtensionError(errors.INVALID_STRUCTURE, f"{where} has a key that is not text")
        if key == f"{p}xmlns" or key.startswith(f"{p}xmlns:"):
            continue
        if p and key.startswith(p):
            attribute = key[len(p) :]
            element.set(
                local_scope.tag(attribute, f"{where}.{key}", attribute=True),
                "" if item is None else scalar_text(item, f"{where}.{key}"),
            )
        elif key == options.text_key:
            _append_text(element, "" if item is None else scalar_text(item, f"{where}.{key}"))
        elif key == "#cdata":
            text = "" if item is None else scalar_text(item, f"{where}.{key}")
            if "]]>" in text or len(element):
                _append_text(element, text)
            else:
                element.text = etree.CDATA(text)
        elif key == "#mixed":
            if not isinstance(item, list):
                raise ExtensionError(errors.INVALID_STRUCTURE, f"{where}.#mixed must be a list")
            for index, part in enumerate(item):
                at = f"{where}.#mixed[{index}]"
                if isinstance(part, Mapping) and len(part) == 1:
                    child_name, child_value = next(iter(part.items()))
                    json_to_element(
                        child_name,
                        child_value,
                        parent=element,
                        scope=local_scope,
                        options=options,
                        where=at,
                    )
                elif part is None:
                    continue
                else:
                    _append_text(element, scalar_text(part, at))
        else:
            json_to_element(
                key,
                item,
                parent=element,
                scope=local_scope,
                options=options,
                where=f"{where}.{key}",
            )
    return element


def _append_text(element: Any, text: str) -> None:
    if not text:
        return
    if len(element):
        last = element[-1]
        last.tail = (last.tail or "") + text
    else:
        element.text = (element.text or "") + text


def plain(value: Any) -> Any:
    """JSON-safe output: Decimal as text, NaN and infinities as text."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def sequence_of(value: Any, what: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise errors.invalid(f"{what} must be a list")
    return value


__all__ = [
    "Counter",
    "JsonOptions",
    "Namespaces",
    "check_name",
    "check_namespaces",
    "check_text",
    "document_to_json",
    "element_to_json",
    "flatten",
    "json_to_element",
    "local_name",
    "options_from",
    "plain",
    "scalar_text",
]
