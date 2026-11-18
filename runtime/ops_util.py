"""Check against a schema, transform, format, canonicalize and compare."""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

from . import convert, errors, limits, safe, xsd_set
from .errors import ExtensionError
from .job import Job, write_xml

XSD = "http://www.w3.org/2001/XMLSchema"
XSLT = "http://www.w3.org/1999/XSL/Transform"
XML_NS = "http://www.w3.org/XML/1998/namespace"

#: XSLT may not read or write files, create directories or reach the network.
NO_ACCESS = etree.XSLTAccessControl(
    read_file=False, write_file=False, create_dir=False, read_network=False, write_network=False
)


def _log_entries(log: Any, limit: int) -> list[dict[str, Any]]:
    entries = []
    for entry in list(log)[:limit]:
        entries.append(
            {
                "line": entry.line or None,
                "column": entry.column or None,
                "message": errors.safe_text(entry.message, 300),
            }
        )
    return entries


# ---------------------------------------------------------------------------
# XSD


def validate_xsd(job: Job) -> dict[str, Any]:
    main = job.source("schema")
    parts = job.sources.get("schema_parts") or []
    names = job.values.get("schema_file_names")
    main_name, files = xsd_set.collect(main, job.values.get("schema_name"), parts, names)
    schema = xsd_set.compile_schema(main_name, files, main_from_text=main.from_text)
    try:
        tree = job.tree()
    except ExtensionError as exc:
        if exc.code not in {errors.INVALID_XML}:
            raise
        return {
            "valid": False,
            "error_count": 1,
            "errors": [{"line": None, "column": None, "message": exc.message}],
        }
    valid = schema.validate(tree)
    log = schema.error_log
    return {
        "valid": bool(valid),
        "error_count": len(log),
        "errors": _log_entries(log, job.values["max_errors"]),
    }


# ---------------------------------------------------------------------------
# XSLT


def transform_xslt(job: Job) -> dict[str, Any]:
    style_source = job.source("stylesheet")
    try:
        style_tree = safe.parse_document(
            style_source.read(limits.MAX_AUXILIARY_BYTES),
            what=style_source.label,
            from_text=style_source.from_text,
            max_bytes=limits.MAX_AUXILIARY_BYTES,
        )
    except ExtensionError as exc:
        if exc.code == errors.INVALID_XML:
            raise ExtensionError(errors.INVALID_STYLESHEET, exc.message) from None
        raise
    for tag in ("import", "include"):
        for node in style_tree.iter(f"{{{XSLT}}}{tag}"):
            raise ExtensionError(
                errors.INVALID_STYLESHEET,
                f"The stylesheet has xsl:{tag} (line {node.sourceline}); other files are never "
                "loaded. Put everything into one stylesheet.",
            )
    try:
        transform = etree.XSLT(style_tree, access_control=NO_ACCESS, extensions=None, regexp=False)
    except etree.XSLTParseError as exc:
        entries = _log_entries(exc.error_log, 1)
        detail = entries[0]["message"] if entries else errors.safe_text(exc, 200)
        raise ExtensionError(
            errors.INVALID_STYLESHEET, f"The stylesheet is not valid: {detail}"
        ) from None
    parameters = job.values.get("parameters") or {}
    if not isinstance(parameters, dict) or len(parameters) > 100:
        raise errors.invalid("parameters must be an object of name → text")
    arguments = {}
    for name, value in parameters.items():
        if not isinstance(name, str) or not convert.NCNAME.match(name):
            raise errors.invalid(f"parameters: {errors.safe_text(name, 40)!r} is not a valid name")
        text = convert.scalar_text("" if value is None else value, f"parameters.{name}")
        arguments[name] = etree.XSLT.strparam(text)
    tree = job.tree()
    try:
        result = transform(tree, **arguments)
    except etree.XSLTApplyError as exc:
        entries = _log_entries(exc.error_log, 3)
        detail = "; ".join(e["message"] for e in entries) or errors.safe_text(exc, 200)
        raise ExtensionError(
            errors.TRANSFORM_FAILED, f"The transformation failed: {detail}"
        ) from None
    messages = [
        errors.safe_text(entry.message, 300)
        for entry in transform.error_log
        if entry.type_name != "ERR_OK" or entry.message
    ][:50]
    output = bytes(result) if result is not None else b""
    with open(job.out_path, "wb") as out:
        out.write(output)
    declared = style_tree.getroot().findall(f"{{{XSLT}}}output")
    encoding = next((d.get("encoding") for d in reversed(declared) if d.get("encoding")), None)
    method = output_method(declared, result)
    filename, content_type = OUTPUT_FILES[method]
    facts: dict[str, Any] = {
        "messages": messages,
        "_encoding": encoding or "UTF-8",
        "output_method": method,
        "file_name": filename,
        "content_type": content_type,
    }
    if method != "xml":
        facts["_presentation"] = {"filename": filename, "content_type": content_type}
    return facts


#: The file for each xsl:output method. The upload is always application/xml (one type
#: per output in the policy); html and text are renamed and retyped by a presentation
#: claim (ADR 0045), whose allowed types exclude text/html, so HTML is shown as text.
OUTPUT_FILES: dict[str, tuple[str, str]] = {
    "xml": ("result.xml", "application/xml"),
    "html": ("result.html", "text/plain"),
    "text": ("result.txt", "text/plain"),
}


def output_method(declared: list[Any], result: Any) -> str:
    """xsl:output method, or the XSLT 1.0 default: html when the result's root is html."""
    for node in reversed(declared):
        method = (node.get("method") or "").strip()
        if method:
            local = method.rsplit(":", 1)[-1].lower()
            return local if local in OUTPUT_FILES else "xml"
    root = result.getroot() if result is not None else None
    if root is not None and isinstance(root.tag, str) and root.tag.lower() == "html":
        return "html"
    return "xml"


# ---------------------------------------------------------------------------
# Format and canonicalize


def _preserved(element: Any) -> bool:
    for node in element.iterancestors():
        value = node.get(f"{{{XML_NS}}}space")
        if value is not None:
            return value == "preserve"
    return element.get(f"{{{XML_NS}}}space") == "preserve"


def format_xml(job: Job) -> dict[str, Any]:
    remove_comments = job.values["remove_comments"]
    tree = job.tree(remove_comments=remove_comments)
    encoding = job.values.get("encoding") or job.encoding("xml", tree)
    root = tree.getroot()
    if job.values["mode"] == "minify":
        for element in root.iter():
            if _preserved(element):
                continue
            if len(element) and element.text is not None and not element.text.strip():
                element.text = None
            for child in element:
                if child.tail is not None and not child.tail.strip():
                    child.tail = None
        write_xml(tree, job.out_path, encoding=encoding, declaration=job.values["xml_declaration"])
    else:
        etree.indent(tree, space=" " * job.values["indent"])
        write_xml(tree, job.out_path, encoding=encoding, declaration=job.values["xml_declaration"])
    return {"_encoding": encoding}


def canonicalize_xml(job: Job) -> dict[str, Any]:
    tree = job.tree()
    method = job.values["method"]
    with_comments = job.values["with_comments"]
    prefixes = job.values.get("inclusive_prefixes") or []
    if not isinstance(prefixes, list) or not all(isinstance(p, str) for p in prefixes):
        raise errors.invalid("inclusive_prefixes must be a list of prefixes")
    if prefixes and method != "c14n_exclusive":
        job.warn("Inclusive prefixes apply only to c14n_exclusive and were ignored")
    if method == "c14n2":
        data = etree.tostring(tree, method="c14n2", with_comments=with_comments)
    else:
        data = etree.tostring(
            tree,
            method="c14n",
            exclusive=method == "c14n_exclusive",
            with_comments=with_comments,
            inclusive_ns_prefixes=prefixes if method == "c14n_exclusive" and prefixes else None,
        )
    with open(job.out_path, "wb") as out:
        out.write(data)
    return {"_encoding": "UTF-8"}


# ---------------------------------------------------------------------------
# Compare

_SPACES = re.compile(r"\s+")


class _Differ:
    def __init__(self, job: Job, left: Any, right: Any) -> None:
        self.job = job
        self.left_tree = left
        self.right_tree = right
        self.whitespace = job.values["ignore_whitespace"]
        self.comments = job.values["ignore_comments"]
        self.prefixes = job.values["ignore_prefixes"]
        self.limit = job.values["max_differences"]
        self.found: list[dict[str, Any]] = []
        self.count = 0
        self.steps = 0

    def add(self, path: str, kind: str, left: Any, right: Any) -> None:
        self.count += 1
        if len(self.found) < self.limit:
            self.found.append(
                {
                    "path": path,
                    "kind": kind,
                    "first": None if left is None else errors.safe_text(left, 200),
                    "second": None if right is None else errors.safe_text(right, 200),
                }
            )

    def text(self, value: str | None) -> str:
        value = value or ""
        return _SPACES.sub(" ", value).strip() if self.whitespace else value

    def name(self, node: Any) -> str:
        if self.prefixes or not node.prefix:
            return node.tag
        return f"{node.prefix}:{convert.local_name(node.tag)}"

    def children(self, element: Any) -> list[Any]:
        nodes = []
        for child in element:
            if isinstance(child.tag, str):
                nodes.append(child)
            elif not self.comments:
                nodes.append(child)
        return nodes

    def path(self, node: Any, tree: Any) -> str:
        return str(tree.getpath(node))

    def compare(self, left: Any, right: Any) -> None:
        stack: list[tuple[Any, Any]] = [(left, right)]
        while stack:
            a, b = stack.pop()
            self.steps += 1
            if self.steps % 4096 == 0:
                self.job.check_time()
            here = self.path(a, self.left_tree)
            if isinstance(a.tag, str) != isinstance(b.tag, str) or (
                not isinstance(a.tag, str) and (a.text or "") != (b.text or "")
            ):
                self.add(here, "node", _describe(a), _describe(b))
                continue
            if not isinstance(a.tag, str):
                continue
            if self.name(a) != self.name(b):
                self.add(here, "element_name", _qualified(a), _qualified(b))
                continue
            self.attributes(a, b, here)
            if self.text(a.text) != self.text(b.text):
                self.add(here, "text", a.text, b.text)
            left_children = self.children(a)
            right_children = self.children(b)
            for index in range(max(len(left_children), len(right_children))):
                if index >= len(right_children):
                    node = left_children[index]
                    self.add(
                        self.path(node, self.left_tree), "missing_in_second", _describe(node), None
                    )
                    continue
                if index >= len(left_children):
                    node = right_children[index]
                    self.add(
                        self.path(node, self.right_tree), "missing_in_first", None, _describe(node)
                    )
                    continue
                l_child, r_child = left_children[index], right_children[index]
                if self.text(l_child.tail) != self.text(r_child.tail):
                    self.add(
                        self.path(l_child, self.left_tree), "tail_text", l_child.tail, r_child.tail
                    )
                stack.append((l_child, r_child))

    def attributes(self, a: Any, b: Any, here: str) -> None:
        left = dict(a.attrib)
        right = dict(b.attrib)
        for key in sorted(set(left) | set(right)):
            shown = f"{here}/@{convert.local_name(key)}"
            if key not in right:
                self.add(shown, "attribute_missing_in_second", left[key], None)
            elif key not in left:
                self.add(shown, "attribute_missing_in_first", None, right[key])
            elif left[key] != right[key]:
                self.add(shown, "attribute_value", left[key], right[key])


def _qualified(node: Any) -> str:
    return f"{node.prefix}:{convert.local_name(node.tag)}" if node.prefix else node.tag


def _describe(node: Any) -> str:
    if isinstance(node.tag, str):
        return f"<{_qualified(node)}>"
    return f"comment: {node.text or ''}"


def compare_xml(job: Job) -> dict[str, Any]:
    comments = job.values["ignore_comments"]
    left = job.tree("left", remove_comments=comments)
    right = job.tree("right", remove_comments=comments)
    differ = _Differ(job, left, right)
    differ.compare(left.getroot(), right.getroot())
    return {
        "equal": differ.count == 0,
        "difference_count": differ.count,
        "differences": differ.found,
        "truncated": differ.count > len(differ.found),
    }


__all__ = [
    "NO_ACCESS",
    "canonicalize_xml",
    "compare_xml",
    "format_xml",
    "transform_xslt",
    "validate_xsd",
]
