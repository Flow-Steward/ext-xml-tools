"""Build and convert to XML: build_xml, json_to_xml, csv_to_xml, xlsx_to_xml, xml_to_json."""

from __future__ import annotations

import codecs
import csv
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from lxml import etree

from . import convert, errors, limits, xlsx
from .errors import ExtensionError
from .job import Job, load_json, write_xml

#: Python codec for each encoding the steps offer.
PYTHON_CODECS: dict[str, str] = {
    "UTF-8": "utf-8",
    "UTF-16": "utf-16",
    "ISO-8859-1": "iso-8859-1",
    "ISO-8859-2": "iso-8859-2",
    "ISO-8859-13": "iso-8859-13",
    "ISO-8859-15": "iso-8859-15",
    "windows-1250": "cp1250",
    "windows-1251": "cp1251",
    "windows-1252": "cp1252",
    "windows-1257": "cp1257",
    "US-ASCII": "ascii",
}

_PATH_PART = re.compile(r"^[^.]+$")


def _prolog(job: Job) -> dict[str, Any]:
    return {
        "encoding": job.values.get("encoding") or "UTF-8",
        "declaration": job.values.get("xml_declaration", True),
        "pretty": job.values.get("pretty", True),
    }


def _finish(job: Job, root: Any, result: dict[str, Any]) -> dict[str, Any]:
    prolog = _prolog(job)
    write_xml(root, job.out_path, **prolog)
    result["_encoding"] = prolog["encoding"]
    return result


# ---------------------------------------------------------------------------
# build_xml


def _records(job: Job) -> list[Any]:
    source = job.optional_source("records")
    if source is None:
        records = job.values.get("records")
    else:
        data = source.read(limits.MAX_DOCUMENT_BYTES)
        stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")
        if stripped.startswith(b"["):
            records = load_json(source)
        else:
            records = []
            for number, line in enumerate(data.decode("utf-8-sig", "strict").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line, parse_float=Decimal))
                except ValueError:
                    raise ExtensionError(
                        errors.INVALID_JSON, f"{source.label}: line {number} is not valid JSON"
                    ) from None
    if not isinstance(records, list):
        raise ExtensionError(errors.INVALID_JSON, "The records must be a JSON list of objects")
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ExtensionError(errors.INVALID_STRUCTURE, f"Record {index + 1} is not an object")
    return records


def _lookup(context: Any, path: str) -> Any:
    """``a.b.0`` inside ``context``; ``.`` is the context itself; missing → None."""
    if path == ".":
        return context
    current = context
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


_FIELD_KEYS = frozenset(
    {
        "element",
        "attribute",
        "text",
        "value",
        "const",
        "cdata",
        "required",
        "skip_empty",
        "each",
        "fields",
    }
)


def _check_fields(fields: Any, where: str, depth: int = 0) -> list[Mapping[str, Any]]:
    if not isinstance(fields, list):
        raise ExtensionError(errors.INVALID_STRUCTURE, f"{where} must be a list")
    if depth > 20:
        raise ExtensionError(errors.INVALID_STRUCTURE, f"{where} nests deeper than 20 levels")
    for index, spec in enumerate(fields):
        at = f"{where}[{index}]"
        if not isinstance(spec, Mapping):
            raise ExtensionError(errors.INVALID_STRUCTURE, f"{at} must be an object")
        unknown = set(spec) - _FIELD_KEYS
        if unknown:
            raise ExtensionError(
                errors.INVALID_STRUCTURE, f"{at} has unknown keys: {', '.join(sorted(unknown))}"
            )
        kinds = [k for k in ("element", "attribute", "text") if spec.get(k) not in (None, False)]
        if len(kinds) != 1:
            raise ExtensionError(
                errors.INVALID_STRUCTURE, f"{at} needs exactly one of element, attribute or text"
            )
        for key in ("value", "each"):
            if key in spec and (not isinstance(spec[key], str) or not spec[key]):
                raise ExtensionError(errors.INVALID_STRUCTURE, f"{at}.{key} must be a path text")
        if "value" in spec and "const" in spec:
            raise ExtensionError(errors.INVALID_STRUCTURE, f"{at} has both value and const")
        if "fields" in spec:
            if kinds[0] != "element":
                raise ExtensionError(errors.INVALID_STRUCTURE, f"{at}: only an element has fields")
            _check_fields(spec["fields"], f"{at}.fields", depth + 1)
        if "each" in spec and kinds[0] != "element":
            raise ExtensionError(errors.INVALID_STRUCTURE, f"{at}: only an element repeats (each)")
    return list(fields)


class _Builder:
    def __init__(self, job: Job, scope: convert.Namespaces) -> None:
        self.job = job
        self.scope = scope
        self.cdata_fallbacks = 0

    def fields(
        self, parent: Any, fields: Sequence[Mapping[str, Any]], context: Any, where: str
    ) -> None:
        for index, spec in enumerate(fields):
            at = f"{where}, field {index + 1}"
            if spec.get("element"):
                name = str(spec["element"])
                if "each" in spec:
                    items = _lookup(context, spec["each"])
                    if items is None:
                        items = []
                    if not isinstance(items, list):
                        items = [items]
                    for item_index, item in enumerate(items):
                        self.element(parent, name, spec, item, f"{at} ({name} {item_index + 1})")
                else:
                    self.element(parent, name, spec, context, f"{at} ({name})")
            elif spec.get("attribute"):
                name = str(spec["attribute"])
                value = self.value(spec, context, f"{at} ({name})")
                if value is None:
                    continue
                parent.set(self.scope.tag(name, at, attribute=True), value)
            else:
                value = self.value(spec, context, f"{at} (text)")
                if value is not None:
                    self.text(parent, value, bool(spec.get("cdata")))

    def value(self, spec: Mapping[str, Any], context: Any, where: str) -> str | None:
        if "const" in spec:
            raw = spec["const"]
        elif "value" in spec:
            raw = _lookup(context, str(spec["value"]))
        else:
            raw = None
        if raw is None or raw == "":
            if spec.get("required"):
                raise ExtensionError(errors.INVALID_STRUCTURE, f"{where} is required but empty")
            return None if spec.get("skip_empty", True) else ""
        if isinstance(raw, (Mapping, list)):
            raise ExtensionError(
                errors.INVALID_STRUCTURE, f"{where}: the value is an object or list, not a text"
            )
        return convert.scalar_text(raw, where)

    def element(
        self, parent: Any, name: str, spec: Mapping[str, Any], context: Any, where: str
    ) -> None:
        tag = self.scope.tag(name, where)
        has_value = "value" in spec or "const" in spec
        value = self.value(spec, context, where) if has_value else None
        nested = spec.get("fields")
        if has_value and value is None and not nested:
            return
        element = etree.SubElement(parent, tag)
        if value:
            self.text(element, value, bool(spec.get("cdata")))
        if nested:
            self.fields(element, nested, context, where)
            if (
                not has_value
                and not len(element)
                and not element.attrib
                and not element.text
                and spec.get("skip_empty", True)
            ):
                parent.remove(element)

    def text(self, element: Any, value: str, cdata: bool) -> None:
        if cdata and "]]>" not in value:
            element.text = etree.CDATA(value)
            return
        if cdata:
            self.cdata_fallbacks += 1
        element.text = (element.text or "") + value


def _structure(job: Job) -> dict[str, Any]:
    structure = job.values.get("structure")
    if not isinstance(structure, Mapping):
        raise ExtensionError(errors.INVALID_STRUCTURE, "structure must be an object")
    allowed = {"root", "attributes", "namespaces", "wrapper", "record", "fields"}
    unknown = set(structure) - allowed
    if unknown:
        raise ExtensionError(
            errors.INVALID_STRUCTURE, f"structure has unknown keys: {', '.join(sorted(unknown))}"
        )
    for key in ("root", "record"):
        if not isinstance(structure.get(key), str) or not structure[key]:
            raise ExtensionError(errors.INVALID_STRUCTURE, f"structure.{key} is required")
    wrapper = structure.get("wrapper", [])
    if isinstance(wrapper, str):
        wrapper = [wrapper]
    if not isinstance(wrapper, list) or not all(isinstance(w, str) for w in wrapper):
        raise ExtensionError(errors.INVALID_STRUCTURE, "structure.wrapper must be a list of names")
    attributes = structure.get("attributes", {})
    if not isinstance(attributes, Mapping):
        raise ExtensionError(errors.INVALID_STRUCTURE, "structure.attributes must be an object")
    result = dict(structure)
    result["wrapper"] = wrapper
    result["attributes"] = attributes
    if "fields" in structure:
        result["fields"] = _check_fields(structure["fields"], "structure.fields")
    return result


def build_xml(job: Job) -> dict[str, Any]:
    structure = _structure(job)
    records = _records(job)
    declared = convert.check_namespaces(structure.get("namespaces"), "structure.namespaces")
    scope = convert.Namespaces(declared)
    root = etree.Element(scope.tag(structure["root"], "structure.root"), nsmap=declared or None)
    for name, value in structure["attributes"].items():
        root.set(
            scope.tag(str(name), "structure.attributes", attribute=True),
            convert.scalar_text(value, f"structure.attributes.{name}"),
        )
    container = root
    for name in structure["wrapper"]:
        container = etree.SubElement(container, scope.tag(name, "structure.wrapper"))
    builder = _Builder(job, scope)
    options = convert.JsonOptions()
    record_tag = structure["record"]
    for index, record in enumerate(records):
        where = f"Record {index + 1}"
        if "fields" in structure:
            element = etree.SubElement(container, scope.tag(record_tag, "structure.record"))
            builder.fields(element, structure["fields"], record, where)
        else:
            convert.json_to_element(
                record_tag,
                dict(record),
                parent=container,
                scope=scope,
                options=options,
                where=where,
            )
        if index % 1000 == 999:
            job.check_time()
    if builder.cdata_fallbacks:
        job.warn(
            f"{builder.cdata_fallbacks} values contain ]]> and were written as escaped text "
            "instead of CDATA"
        )
    if not records:
        job.warn("There were no records; the document has no record elements")
    return _finish(job, root, {"record_count": len(records)})


# ---------------------------------------------------------------------------
# json_to_xml and xml_to_json


def json_to_xml(job: Job) -> dict[str, Any]:
    source = job.optional_source("data")
    data = load_json(source) if source is not None else job.values.get("data")
    if isinstance(data, str) and source is None:
        try:
            data = json.loads(data, parse_float=Decimal)
        except ValueError:
            raise ExtensionError(errors.INVALID_JSON, "The JSON text is not valid JSON") from None
    options = convert.JsonOptions(
        attribute_prefix=job.values["attribute_prefix"], text_key=job.values["text_key"]
    )
    scope = convert.Namespaces()
    root_name = job.values.get("root_element")
    item = job.values["item_element"]
    if root_name is None:
        if not (isinstance(data, Mapping) and len(data) == 1):
            raise ExtensionError(
                errors.INVALID_STRUCTURE,
                "Give a root element: the JSON is not one object with exactly one key",
            )
        root_name, value = next(iter(data.items()))
        root = convert.json_to_element(
            str(root_name), value, parent=None, scope=scope, options=options, where="$"
        )
        if isinstance(value, list):
            raise ExtensionError(
                errors.INVALID_STRUCTURE, "The root key holds a list; give a root element"
            )
    else:
        root = etree.Element(scope.tag(root_name, "Root element"))
        if isinstance(data, list):
            convert.json_to_element(
                item, data, parent=root, scope=scope, options=options, where="$"
            )
        elif isinstance(data, Mapping):
            built = convert.json_to_element(
                root_name, dict(data), parent=None, scope=scope, options=options, where="$"
            )
            root = built
        else:
            root.text = convert.scalar_text(data, "$") if data is not None else None
    return _finish(job, root, {})


def xml_to_json(job: Job) -> dict[str, Any]:
    tree = job.tree()
    options = convert.options_from(job.values)
    counter = convert.Counter()
    value = convert.document_to_json(tree.getroot(), options, counter)
    if counter.skipped_comments:
        job.warn(f"{counter.skipped_comments} comments or processing instructions were left out")
    with open(job.out_path, "w", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, separators=(",", ":"))
    return {}


# ---------------------------------------------------------------------------
# Tables to XML

DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}


def element_names(columns: Sequence[str], job: Job) -> list[str]:
    """A valid, unique XML name for each column (said in the warnings when adjusted)."""
    names: list[str] = []
    for index, column in enumerate(columns):
        name = re.sub(r"[^\w.-]", "_", column.strip(), flags=re.UNICODE) or f"column_{index + 1}"
        if not convert.NCNAME.match(name):
            name = f"_{name}"
        if not convert.NCNAME.match(name):
            name = f"column_{index + 1}"
        base, number = name, 2
        while name in names:
            name = f"{base}_{number}"
            number += 1
        if name != column:
            job.warn(f"Column {index + 1} ({errors.safe_text(column, 40)!r}) is written as {name}")
        names.append(name)
    return names


def table_to_xml(job: Job, rows: Iterable[list[str]], what: str) -> dict[str, Any]:
    iterator = iter(rows)
    header = next(iterator, None)
    if header is None or not any(str(cell).strip() for cell in header):
        raise ExtensionError(
            errors.INVALID_CSV if what == "CSV" else errors.INVALID_XLSX,
            f"The {what} has no header row",
        )
    if len(header) > limits.MAX_COLUMNS:
        raise ExtensionError(
            errors.LIMIT_EXCEEDED, f"The {what} has more than {limits.MAX_COLUMNS} columns"
        )
    names = element_names([str(h) for h in header], job)
    attribute_columns = job.values.get("attribute_columns") or []
    if not isinstance(attribute_columns, list):
        raise errors.invalid("attribute_columns must be a list of column names")
    as_attribute = {str(c) for c in attribute_columns}
    missing = as_attribute - set(header)
    if missing:
        raise errors.invalid(
            "Columns as attributes: no such column: " + ", ".join(sorted(missing))[:200]
        )
    scope = convert.Namespaces()
    root = etree.Element(scope.tag(job.values["root_element"], "Root element"))
    record_tag = scope.tag(job.values["record_element"], "Record element")
    skip_empty = job.values["skip_empty"]
    count = 0
    for number, row in enumerate(iterator, 2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) > len(header):
            job.warn(f"Row {number} has more cells than the header; the extra cells were left out")
        record = etree.SubElement(root, record_tag)
        for index, name in enumerate(names):
            cell = row[index] if index < len(row) else ""
            where = f"Row {number}, column {header[index]!r}"
            convert.check_text(cell, where)
            if header[index] in as_attribute:
                if cell or not skip_empty:
                    record.set(name, cell)
            elif cell or not skip_empty:
                etree.SubElement(record, name).text = cell or None
        count += 1
        if count % 1000 == 0:
            job.check_time()
    columns = [{"column": h, "element": n} for h, n in zip(header, names, strict=True)]
    return _finish(job, root, {"record_count": count, "columns": columns})


def _csv_text(job: Job) -> str:
    source = job.source("csv")
    data = source.read(limits.MAX_DOCUMENT_BYTES)
    if source.from_text:
        return data.decode("utf-8")
    codec = PYTHON_CODECS[job.values["csv_encoding"]]
    if codec == "utf-8":
        codec = "utf-8-sig"
    try:
        return codecs.decode(data, codec)
    except UnicodeDecodeError as exc:
        raise ExtensionError(
            errors.INVALID_CSV,
            f"The CSV is not valid {job.values['csv_encoding']} (byte {exc.start + 1}); "
            "choose its encoding",
        ) from None


def csv_to_xml(job: Job) -> dict[str, Any]:
    text = _csv_text(job)
    delimiter = DELIMITERS[job.values["delimiter"]]
    csv.field_size_limit(limits.MAX_DOCUMENT_BYTES)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)

    def rows() -> Iterable[list[str]]:
        try:
            yield from reader
        except csv.Error as exc:
            raise ExtensionError(
                errors.INVALID_CSV,
                f"The CSV cannot be read at line {reader.line_num}: {errors.safe_text(exc, 120)}",
            ) from None

    return table_to_xml(job, rows(), "CSV")


def xlsx_to_xml(job: Job) -> dict[str, Any]:
    source = job.source("xlsx")
    sheet, rows = xlsx.read_rows(source.path or "", job.values.get("sheet"), job.check_time)
    result = table_to_xml(job, rows, "spreadsheet")
    result["sheet"] = sheet
    return result


__all__ = [
    "DELIMITERS",
    "PYTHON_CODECS",
    "build_xml",
    "csv_to_xml",
    "json_to_xml",
    "xlsx_to_xml",
    "xml_to_json",
]
