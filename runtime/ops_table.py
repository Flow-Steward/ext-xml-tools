"""XML to tables, reading element by element: xml_to_csv, xml_to_xlsx."""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from . import convert, errors, limits, safe, xlsx
from .errors import ExtensionError
from .job import Job, compile_xpath, matcher, run_xpath
from .ops_build import DELIMITERS


def _explicit_columns(job: Job, namespaces: Mapping[str, str]) -> list[tuple[str, Any]] | None:
    raw = job.values.get("columns")
    if raw in (None, []):
        return None
    if not isinstance(raw, list) or len(raw) > limits.MAX_COLUMNS:
        raise errors.invalid(f"columns must be a list of at most {limits.MAX_COLUMNS} items")
    columns = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise errors.invalid(f"columns[{index}] needs a name and an xpath")
        xpath = item.get("xpath")
        compiled = compile_xpath(xpath, namespaces, f"columns[{index}].xpath")
        columns.append((item["name"], compiled))
    return columns


def _value(result: Any) -> str:
    if isinstance(result, list):
        parts = []
        for node in result:
            parts.append(node.xpath("string()") if hasattr(node, "xpath") else str(node))
        return " ".join(part for part in parts if part)
    if isinstance(result, bool):
        return "true" if result else "false"
    if isinstance(result, float):
        return str(int(result)) if result.is_integer() else repr(result)
    return str(result)


def _records(job: Job, matches: Callable[[Any], bool]) -> Iterator[Any]:
    source = job.source("xml")
    info = safe.StreamInfo()
    with source.open() as stream:
        yield from safe.iter_records(
            stream, matches, info, what=source.label, check_time=job.check_time
        )


def _rows(job: Job) -> tuple[list[str], Iterator[list[str]]]:
    """The column names and an iterator over the rows (each a list of texts)."""
    namespaces = job.namespaces()
    matches = matcher(job.values["record_element"], namespaces)
    explicit = _explicit_columns(job, namespaces)
    if explicit is not None:

        def explicit_rows() -> Iterator[list[str]]:
            for element in _records(job, matches):
                yield [_value(run_xpath(xpath, element, name)) for name, xpath in explicit]

        return [name for name, _ in explicit], explicit_rows()
    options = convert.JsonOptions(namespaces="strip")
    names: dict[str, None] = {}
    for element in _records(job, matches):
        flat = convert.flatten(_as_object(element, options))
        for key in flat:
            if key not in names:
                if len(names) >= limits.MAX_COLUMNS:
                    raise ExtensionError(
                        errors.LIMIT_EXCEEDED,
                        f"The records have more than {limits.MAX_COLUMNS} different fields; "
                        "give the columns",
                    )
                names[key] = None
    columns = list(names)

    def auto_rows() -> Iterator[list[str]]:
        for element in _records(job, matches):
            flat = convert.flatten(_as_object(element, options))
            yield ["" if flat.get(c) is None else str(flat[c]) for c in columns]

    return columns, auto_rows()


def _as_object(element: Any, options: convert.JsonOptions) -> Any:
    value = convert.element_to_json(element, options)
    return value if isinstance(value, dict) else {"#text": value}


def xml_to_csv(job: Job) -> dict[str, Any]:
    columns, rows = _rows(job)
    count = 0
    encoding = "utf-8-sig" if job.values["excel_bom"] else "utf-8"
    with open(job.out_path, "w", encoding=encoding, newline="") as out:
        writer = csv.writer(out, delimiter=DELIMITERS[job.values["delimiter"]])
        if job.values["header"]:
            writer.writerow(columns)
        for row in rows:
            writer.writerow(row)
            count += 1
    if count == 0:
        job.warn("No record element was found; the CSV has no rows")
    return {"record_count": count, "columns": columns, "_encoding": encoding}


def xml_to_xlsx(job: Job) -> dict[str, Any]:
    columns, rows = _rows(job)
    writer = xlsx.Writer(job.out_path, job.values["sheet_name"])
    writer.row(columns, "The header")
    count = 0
    for row in rows:
        count += 1
        writer.row(row, f"Record {count}")
    writer.close()
    if writer.truncated_cells:
        job.warn(
            f"{writer.truncated_cells} cells were longer than {xlsx.MAX_CELL_TEXT} characters, "
            "the most a spreadsheet cell holds, and were cut"
        )
    if count == 0:
        job.warn("No record element was found; the sheet has only the header")
    return {"record_count": count, "columns": columns}


__all__ = ["xml_to_csv", "xml_to_xlsx"]
