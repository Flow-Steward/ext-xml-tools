"""Each operation id and the function that does its work (in the worker process)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import ops_build, ops_edit, ops_read, ops_table, ops_util
from .job import Job

RUNNERS: dict[str, Callable[[Job], dict[str, Any]]] = {
    "parse_xml": ops_read.parse_xml,
    "parse_xml_stream": ops_read.parse_xml_stream,
    "select_xpath": ops_read.select_xpath,
    "check_xml": ops_read.check_xml,
    "build_xml": ops_build.build_xml,
    "json_to_xml": ops_build.json_to_xml,
    "xml_to_json": ops_build.xml_to_json,
    "csv_to_xml": ops_build.csv_to_xml,
    "xlsx_to_xml": ops_build.xlsx_to_xml,
    "xml_to_csv": ops_table.xml_to_csv,
    "xml_to_xlsx": ops_table.xml_to_xlsx,
    "edit_xml": ops_edit.edit_xml,
    "merge_xml": ops_edit.merge_xml,
    "split_xml": ops_edit.split_xml,
    "validate_xsd": ops_util.validate_xsd,
    "transform_xslt": ops_util.transform_xslt,
    "format_xml": ops_util.format_xml,
    "canonicalize_xml": ops_util.canonicalize_xml,
    "compare_xml": ops_util.compare_xml,
}

__all__ = ["RUNNERS"]
