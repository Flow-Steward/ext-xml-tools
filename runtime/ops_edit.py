"""Change documents: edit_xml, merge_xml, split_xml."""

from __future__ import annotations

import copy
import zipfile
from collections.abc import Mapping
from typing import Any

from lxml import etree

from . import convert, errors, limits, safe
from .errors import ExtensionError
from .job import Job, compile_xpath, run_xpath, write_xml

ACTIONS = (
    "set_text",
    "set_attribute",
    "remove_attribute",
    "insert",
    "replace",
    "rename",
    "remove",
)
POSITIONS = ("append", "prepend", "before", "after")
_CHANGE_KEYS = frozenset({"action", "xpath", "name", "value", "xml", "position"})


def _encoding_of(tree: Any) -> str:
    return tree.docinfo.encoding or "UTF-8"


def _fragment(text: Any, namespaces: Mapping[str, str], where: str) -> list[Any]:
    """The elements of an XML fragment, parsed safely, prefixes from Namespace prefixes."""
    if not isinstance(text, str) or not text.strip():
        raise ExtensionError(errors.INVALID_STRUCTURE, f"{where}: xml is required")
    declarations = "".join(
        f' xmlns:{prefix}="{_quoted(uri)}"' for prefix, uri in namespaces.items()
    )
    wrapped = f"<fs-fragment{declarations}>{text}</fs-fragment>".encode()
    try:
        root = safe.parse_document(wrapped, what=f"{where}: the XML fragment", from_text=True)
    except ExtensionError as exc:
        raise ExtensionError(errors.INVALID_STRUCTURE, exc.message) from None
    holder = root.getroot()
    if (holder.text or "").strip() or any((child.tail or "").strip() for child in holder):
        raise ExtensionError(
            errors.INVALID_STRUCTURE, f"{where}: the fragment must be elements only, not text"
        )
    elements = [child for child in holder if isinstance(child.tag, str)]
    if not elements:
        raise ExtensionError(errors.INVALID_STRUCTURE, f"{where}: the fragment has no element")
    return elements


def _quoted(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


def _elements(nodes: Any, where: str) -> list[Any]:
    if not isinstance(nodes, list):
        raise ExtensionError(errors.INVALID_XPATH, f"{where}: the XPath must select nodes")
    return [node for node in nodes if isinstance(node, etree._Element)]


def _apply(tree: Any, change: Mapping[str, Any], namespaces: Mapping[str, str], where: str) -> int:
    action = change.get("action")
    if action not in ACTIONS:
        raise ExtensionError(
            errors.INVALID_STRUCTURE, f"{where}: action must be one of {', '.join(ACTIONS)}"
        )
    unknown = set(change) - _CHANGE_KEYS
    if unknown:
        raise ExtensionError(
            errors.INVALID_STRUCTURE, f"{where} has unknown keys: {', '.join(sorted(unknown))}"
        )
    xpath = compile_xpath(change.get("xpath"), namespaces, f"{where}: xpath", nodes=True)
    found = run_xpath(xpath, tree, f"{where}: xpath")
    if not isinstance(found, list):
        raise ExtensionError(errors.INVALID_XPATH, f"{where}: the XPath must select nodes")
    scope = convert.Namespaces({k: v for k, v in namespaces.items()})
    root = tree.getroot()
    if action == "remove":
        count = 0
        for node in found:
            if isinstance(node, etree._Element):
                if node is root:
                    raise ExtensionError(
                        errors.INVALID_STRUCTURE, f"{where}: the root element cannot be removed"
                    )
                _remove_keep_tail(node)
                count += 1
            elif getattr(node, "is_attribute", False):
                owner = node.getparent()
                if owner is not None and node.attrname in owner.attrib:
                    del owner.attrib[node.attrname]
                    count += 1
        return count
    elements = _elements(found, where)
    if action in {"set_text", "set_attribute"}:
        value = change.get("value")
        value = "" if value is None else convert.scalar_text(value, f"{where}: value")
    if action == "set_text":
        for element in elements:
            for child in list(element):
                element.remove(child)
            element.text = value
        return len(elements)
    if action in {"set_attribute", "remove_attribute"}:
        name = change.get("name")
        if not isinstance(name, str):
            raise ExtensionError(errors.INVALID_STRUCTURE, f"{where}: name is required")
        tag = scope.tag(name, f"{where}: name", attribute=True)
        count = 0
        for element in elements:
            if action == "set_attribute":
                element.set(tag, value)
                count += 1
            elif tag in element.attrib:
                del element.attrib[tag]
                count += 1
        return count
    if action == "rename":
        name = change.get("name")
        if not isinstance(name, str):
            raise ExtensionError(errors.INVALID_STRUCTURE, f"{where}: name is required")
        tag = scope.tag(name, f"{where}: name")
        for element in elements:
            element.tag = tag
        return len(elements)
    position = change.get("position", "append") if action == "insert" else "replace"
    if action == "insert" and position not in POSITIONS:
        raise ExtensionError(
            errors.INVALID_STRUCTURE, f"{where}: position must be one of {', '.join(POSITIONS)}"
        )
    fragment = _fragment(change.get("xml"), namespaces, where)
    for element in elements:
        if position in {"before", "after", "replace"} and element is root:
            if position == "replace" and len(fragment) == 1:
                pass
            else:
                raise ExtensionError(
                    errors.INVALID_STRUCTURE,
                    f"{where}: there can be only one root element",
                )
        copies = [copy.deepcopy(node) for node in fragment]
        for node in copies:
            node.tail = None
        if position == "append":
            element.extend(copies)
        elif position == "prepend":
            for offset, node in enumerate(copies):
                element.insert(offset, node)
        elif position == "before":
            for node in copies:
                element.addprevious(node)
        elif position == "after":
            for node in reversed(copies):
                element.addnext(node)
        else:
            if element is root:
                tree._setroot(copies[0])
                root = copies[0]
                continue
            for node in copies:
                element.addprevious(node)
            _remove_keep_tail(element)
    return len(elements)


def _remove_keep_tail(element: Any) -> None:
    parent = element.getparent()
    tail = element.tail
    if tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(element)


def edit_xml(job: Job) -> dict[str, Any]:
    tree = job.tree()
    changes = job.values["changes"]
    if not isinstance(changes, list) or not changes:
        raise errors.invalid("changes must be a non-empty list")
    if len(changes) > limits.MAX_EDIT_CHANGES:
        raise ExtensionError(
            errors.LIMIT_EXCEEDED, f"At most {limits.MAX_EDIT_CHANGES} changes per step"
        )
    namespaces = job.namespaces()
    applied = []
    for index, change in enumerate(changes):
        where = f"Change {index + 1}"
        if not isinstance(change, Mapping):
            raise ExtensionError(errors.INVALID_STRUCTURE, f"{where} must be an object")
        matched = _apply(tree, change, namespaces, where)
        if matched == 0:
            if job.values["require_match"]:
                raise ExtensionError(
                    errors.NO_MATCH,
                    f"{where} ({change.get('action')}) selected nothing; nothing was written",
                )
            job.warn(f"{where} selected nothing")
        applied.append({"index": index + 1, "action": change.get("action"), "matched": matched})
        job.check_time()
    safe.check_shape(tree.getroot(), "The result")
    encoding = job.encoding("xml", tree)
    write_xml(tree, job.out_path, encoding=encoding, declaration=True)
    return {"applied": applied, "_encoding": encoding}


def merge_xml(job: Job) -> dict[str, Any]:
    sources = job.sources.get("xml") or []
    if len(sources) < 2:
        raise errors.invalid("Give at least two documents to merge")
    if len(sources) > limits.MAX_MERGE_DOCUMENTS:
        raise ExtensionError(
            errors.LIMIT_EXCEEDED, f"At most {limits.MAX_MERGE_DOCUMENTS} documents per step"
        )
    namespaces = job.namespaces()
    xpath = compile_xpath(job.values["record_xpath"], namespaces, "Record XPath")
    base: Any = None
    anchor: Any = None
    parent: Any = None
    total = 0
    budget = limits.MAX_DOCUMENT_BYTES * 2
    for number, source in enumerate(sources, 1):
        budget -= source.size
        if budget < 0:
            raise ExtensionError(
                errors.LIMIT_EXCEEDED,
                f"The documents together are larger than {2 * limits.MAX_DOCUMENT_BYTES // limits.MiB} MiB",
            )
        tree = safe.parse_document(
            source.read(), what=f"Document {number}", from_text=source.from_text
        )
        records = _elements(run_xpath(xpath, tree, "Record XPath"), "Record XPath")
        if base is None:
            base = tree
            if records:
                anchor = records[-1]
                parent = anchor.getparent()
            else:
                parent = tree.getroot()
                job.warn("The first document has no records; the others' records go under its root")
            total += len(records)
            continue
        if not records:
            job.warn(f"Document {number} has no records")
        for record in records:
            if record.getparent() is None:
                raise ExtensionError(
                    errors.INVALID_XPATH, "Record XPath must select elements below the root"
                )
            moved = copy.deepcopy(record)
            moved.tail = anchor.tail if anchor is not None else None
            if anchor is not None:
                anchor.addnext(moved)
            else:
                parent.append(moved)
            anchor = moved
            total += 1
        job.check_time()
    safe.check_shape(base.getroot(), "The result")
    first = sources[0]
    with first.open() as stream:
        encoding = safe.declared_encoding(stream.read(200)) or _encoding_of(base)
    write_xml(base, job.out_path, encoding=encoding, declaration=True)
    return {"document_count": len(sources), "record_count": total, "_encoding": encoding}


def _shell(element: Any) -> Any:
    """A copy of ``element`` without its children (tag, attributes, namespaces)."""
    shell = etree.Element(element.tag, attrib=dict(element.attrib), nsmap=element.nsmap)
    shell.text = element.text if element.text and element.text.strip() else None
    return shell


def split_xml(job: Job) -> dict[str, Any]:
    from .job import matcher

    source = job.source("xml")
    namespaces = job.namespaces()
    matches = matcher(job.values["record_element"], namespaces)
    size = job.values["records_per_part"]
    copy_header = job.values["copy_header"]
    info = safe.StreamInfo()
    chain: list[Any] | None = None
    header: list[Any] = []
    current: list[Any] = []
    parts: list[dict[str, Any]] = []
    total = 0
    with (
        source.open() as stream,
        zipfile.ZipFile(job.out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive,
    ):

        def flush() -> None:
            if not current:
                return
            if len(parts) >= limits.MAX_PARTS:
                raise ExtensionError(
                    errors.LIMIT_EXCEEDED,
                    f"The split would make more than {limits.MAX_PARTS} parts; "
                    "raise Records per part",
                )
            if chain is None:
                return
            top = _shell(chain[0])
            holder = top
            for ancestor in chain[1:]:
                holder = etree.SubElement(holder, ancestor.tag, attrib=dict(ancestor.attrib))
            for node in header:
                holder.append(copy.deepcopy(node))
            holder.extend(current)
            name = f"part-{len(parts) + 1:05d}.xml"
            body = etree.tostring(top, encoding="UTF-8", xml_declaration=False)
            archive.writestr(name, b'<?xml version="1.0" encoding="UTF-8"?>\n' + body)
            parts.append({"name": name, "records": len(current)})
            current.clear()

        for element in safe.iter_records(
            stream,
            matches,
            info,
            what=source.label,
            check_time=job.check_time,
            keep_header=copy_header,
        ):
            if chain is None:
                chain = list(reversed(list(element.iterancestors())))
                if copy_header:
                    header = [
                        copy.deepcopy(node)
                        for node in element.itersiblings(preceding=True)
                        if isinstance(node.tag, str) and not matches(node)
                    ][::-1]
            record = copy.deepcopy(element)
            record.tail = None
            current.append(record)
            total += 1
            if len(current) >= size:
                flush()
        flush()
    if total == 0:
        job.warn("No record element was found; the ZIP file is empty")
    return {"part_count": len(parts), "record_count": total, "parts": parts[:1000]}


__all__ = ["ACTIONS", "edit_xml", "merge_xml", "split_xml"]
