"""Read, select and check: parse_xml, parse_xml_stream, select_xpath, check_xml."""

from __future__ import annotations

import json
from typing import Any

from lxml import etree

from . import convert, errors, limits, safe
from .errors import ExtensionError
from .job import Job, compile_xpath, matcher, run_xpath

SAMPLE_SIZE = 5


def _record(element: Any, options: convert.JsonOptions, flat: bool, counter: Any) -> Any:
    value = convert.element_to_json(element, options, counter)
    if not isinstance(value, dict):
        value = {convert.display_name(element, element.tag, options): value}
    return convert.flatten(value) if flat else value


def _comment_warning(job: Job, counter: convert.Counter) -> None:
    if counter.skipped_comments:
        job.warn(f"{counter.skipped_comments} comments or processing instructions were left out")


def parse_xml(job: Job) -> dict[str, Any]:
    tree = job.tree()
    options = convert.options_from(job.values)
    xpath = compile_xpath(job.values["record_xpath"], job.namespaces(), "Record XPath")
    selected = run_xpath(xpath, tree, "Record XPath")
    if not isinstance(selected, list):
        raise ExtensionError(errors.INVALID_XPATH, "Record XPath must select elements")
    elements = [node for node in selected if isinstance(node, etree._Element)]
    if len(elements) != len(selected):
        job.warn("Record XPath selected nodes that are not elements; they were left out")
    limit = job.values["max_records"]
    counter = convert.Counter()
    records = []
    for element in elements[:limit]:
        records.append(_record(element, options, job.values["flatten"], counter))
        if len(records) % 1000 == 0:
            job.check_time()
    _comment_warning(job, counter)
    root = tree.getroot()
    return {
        "records": records,
        "count": len(records),
        "total": len(elements),
        "truncated": len(elements) > len(records),
        "root_element": convert.display_name(root, root.tag, options),
    }


def parse_xml_stream(job: Job) -> dict[str, Any]:
    source = job.source("xml")
    options = convert.options_from(job.values)
    matches = matcher(job.values["record_element"], job.namespaces())
    limit = job.values.get("max_records")
    info = safe.StreamInfo()
    counter = convert.Counter()
    sample: list[Any] = []
    count = 0
    truncated = False
    with source.open() as stream, open(job.out_path, "w", encoding="utf-8") as out:
        for element in safe.iter_records(
            stream, matches, info, what=source.label, check_time=job.check_time
        ):
            if limit and count >= limit:
                truncated = True
                break
            record = _record(element, options, job.values["flatten"], counter)
            out.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            out.write("\n")
            count += 1
            if len(sample) < SAMPLE_SIZE:
                sample.append(record)
    if count == 0:
        job.warn("No record element was found; the file is empty")
    _comment_warning(job, counter)
    root_tag = info.root_tag or ""
    with source.open() as stream:
        head = stream.read(200)
    return {
        "record_count": count,
        "sample": sample,
        "root_element": convert.local_name(root_tag) if root_tag else None,
        "encoding": _encoding_of(head),
        "truncated": truncated,
    }


def _encoding_of(head: bytes) -> str:
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "UTF-16"
    return safe.declared_encoding(head) or "UTF-8"


def _xpath_value(node: Any, mode: str, options: convert.JsonOptions) -> Any:
    if isinstance(node, etree._Element):
        if mode == "nodes":
            return etree.tostring(node, encoding="unicode", with_tail=False)
        if mode == "records":
            return {
                convert.display_name(node, node.tag, options): convert.element_to_json(
                    node, options
                )
            }
        return node.xpath("string()")
    if isinstance(node, (etree._Comment, etree._ProcessingInstruction)):
        return (
            node.text
            if mode != "nodes"
            else etree.tostring(node, encoding="unicode", with_tail=False)
        )
    if isinstance(node, bytes):
        return node.decode("utf-8", "replace")
    return str(node) if not isinstance(node, (bool, float, int)) else node


def select_xpath(job: Job) -> dict[str, Any]:
    tree = job.tree()
    options = convert.options_from(job.values)
    variables = job.values.get("variables") or {}
    if not isinstance(variables, dict) or len(variables) > 100:
        raise errors.invalid("variables must be an object of name → value")
    checked: dict[str, Any] = {}
    for name, value in variables.items():
        if not isinstance(name, str) or not convert.NCNAME.match(name):
            raise errors.invalid(f"variables: {errors.safe_text(name, 40)!r} is not a valid name")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            value = str(value) if value is not None else ""
        checked[name] = value
    xpath = compile_xpath(job.values["xpath"], job.namespaces())
    result = run_xpath(xpath, tree, "XPath", **checked)
    mode = job.values["return"]
    limit = job.values["max_results"]
    if isinstance(result, list):
        values = [_xpath_value(node, mode, options) for node in result[:limit]]
        return {
            "result_type": "nodeset",
            "values": values,
            "first": values[0] if values else None,
            "count": len(result),
            "truncated": len(result) > limit,
        }
    if isinstance(result, bool):
        kind = "boolean"
    elif isinstance(result, float):
        kind = "number"
        result = int(result) if result.is_integer() else result
    else:
        kind = "string"
        result = str(result)
    return {
        "result_type": kind,
        "values": [result],
        "first": result,
        "count": 1,
        "truncated": False,
    }


def check_xml(job: Job) -> dict[str, Any]:
    source = job.source("xml")
    result: dict[str, Any] = {
        "well_formed": False,
        "errors": [],
        "root_element": None,
        "root_namespace": None,
        "declared_encoding": None,
        "xml_version": None,
        "element_count": None,
        "max_depth": None,
    }
    try:
        data = source.read(limits.MAX_DOCUMENT_BYTES)
        head = data[:200]
        result["declared_encoding"] = safe.declared_encoding(head)
        result["xml_version"] = safe.declared_version(head)
        tree = safe.parse_document(data, what=source.label, from_text=source.from_text)
    except ExtensionError as exc:
        if exc.code not in {errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.LIMIT_EXCEEDED}:
            raise
        line, column = _line_column(exc.message)
        result["errors"] = [
            {"line": line, "column": column, "code": exc.code, "message": exc.message}
        ]
        return result
    root = tree.getroot()
    count, depth = safe.check_shape(root, source.label)
    result.update(
        well_formed=True,
        root_element=convert.local_name(root.tag),
        root_namespace=root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else None,
        element_count=count,
        max_depth=depth,
    )
    return result


def _line_column(message: str) -> tuple[int | None, int | None]:
    marker = " at line "
    if marker not in message:
        return None, None
    tail = message.split(marker, 1)[1]
    try:
        line_text, rest = tail.split(", column ", 1)
        column_text = rest.split(":", 1)[0]
        return int(line_text), int(column_text)
    except ValueError:
        return None, None


__all__ = ["check_xml", "parse_xml", "parse_xml_stream", "select_xpath"]
