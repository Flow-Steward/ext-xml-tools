"""A minimal, safe XLSX (Office Open XML spreadsheet) reader and writer.

Only what a table needs: one sheet, text and number cells. The parts are read
with the same locked-down parser as every other XML here, and the ZIP itself is
bounded (number of parts, unpacked size, compression ratio) against zip bombs.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from lxml import etree

from . import convert, errors, limits, safe
from .errors import ExtensionError

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN, "r": REL, "p": PKG_REL}

MAX_PARTS = 2_000
MAX_PART_BYTES = 512 * limits.MiB
MAX_TOTAL_BYTES = 1024 * limits.MiB
MAX_RATIO = 200
MAX_ROWS = 1_048_576
MAX_CELL_TEXT = 32_767
MAX_SHARED_STRINGS = 2_000_000

_CELL_REF = re.compile(r"^([A-Z]{1,3})([0-9]{1,7})$")


def _bad(message: str) -> ExtensionError:
    return ExtensionError(errors.INVALID_XLSX, message)


def _check_zip(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_PARTS:
        raise ExtensionError(errors.LIMIT_EXCEEDED, "The spreadsheet has too many parts")
    total = 0
    for info in infos:
        total += info.file_size
        if info.file_size > MAX_PART_BYTES or total > MAX_TOTAL_BYTES:
            raise ExtensionError(errors.LIMIT_EXCEEDED, "The spreadsheet unpacks to too much data")
        if info.file_size > limits.MiB and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise ExtensionError(
                errors.LIMIT_EXCEEDED, "The spreadsheet is compressed suspiciously well (zip bomb?)"
            )


def _part(archive: zipfile.ZipFile, name: str, what: str) -> Any:
    try:
        data = archive.read(name)
    except KeyError:
        raise _bad(f"The spreadsheet has no {what}") from None
    try:
        return safe.parse_document(data, what=f"The spreadsheet's {what}", max_bytes=MAX_PART_BYTES)
    except ExtensionError as exc:
        if exc.code == errors.LIMIT_EXCEEDED:
            raise
        raise _bad(exc.message) from None


def _target(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = base.split("/")[:-1] + target.split("/")
    out: list[str] = []
    for part in parts:
        if part == "..":
            if out:
                out.pop()
        elif part and part != ".":
            out.append(part)
    return "/".join(out)


def _column_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile, path: str | None) -> list[str]:
    if not path or path not in archive.namelist():
        return []
    strings: list[str] = []
    with archive.open(path) as stream:
        info = safe.StreamInfo()
        for item in safe.iter_records(
            stream, lambda e: e.tag == f"{{{MAIN}}}si", info, what="The spreadsheet's strings"
        ):
            strings.append("".join(item.itertext()))
            if len(strings) > MAX_SHARED_STRINGS:
                raise ExtensionError(errors.LIMIT_EXCEEDED, "The spreadsheet has too many texts")
    return strings


def read_rows(
    path: str, sheet_name: str | None, check_time: Callable[[], None]
) -> tuple[str, Iterator[list[str]]]:
    """The chosen sheet's name and its rows as lists of texts."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise _bad("The file is not an XLSX spreadsheet") from None
    _check_zip(archive)
    workbook = _part(archive, "xl/workbook.xml", "workbook")
    rels = _part(archive, "xl/_rels/workbook.xml.rels", "workbook relationships")
    targets = {
        rel.get("Id"): _target("xl/workbook.xml", rel.get("Target") or "")
        for rel in rels.getroot().iterfind("p:Relationship", NS)
    }
    sheets = [
        (sheet.get("name") or "", sheet.get(f"{{{REL}}}id"))
        for sheet in workbook.getroot().iterfind("m:sheets/m:sheet", NS)
    ]
    if not sheets:
        raise _bad("The spreadsheet has no sheets")
    if sheet_name:
        chosen = [s for s in sheets if s[0] == sheet_name]
        if not chosen:
            raise _bad(f"The spreadsheet has no sheet named {errors.safe_text(sheet_name, 40)!r}")
        name, rel_id = chosen[0]
    else:
        name, rel_id = sheets[0]
    sheet_path = targets.get(rel_id)
    if not sheet_path or sheet_path not in archive.namelist():
        raise _bad("The spreadsheet's sheet part is missing")
    shared_path = next(
        (
            _target("xl/workbook.xml", rel.get("Target") or "")
            for rel in rels.getroot().iterfind("p:Relationship", NS)
            if (rel.get("Type") or "").endswith("/sharedStrings")
        ),
        None,
    )
    strings = _shared_strings(archive, shared_path)
    return name, _rows(archive, sheet_path, strings, check_time)


def _cell_value(cell: Any, strings: Sequence[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        node = cell.find("m:is", NS)
        return "".join(node.itertext()) if node is not None else ""
    value = cell.findtext("m:v", default="", namespaces=NS)
    if kind == "s":
        try:
            return strings[int(value)]
        except (ValueError, IndexError):
            raise _bad("The spreadsheet refers to a text it does not have") from None
    if kind == "b":
        return "true" if value == "1" else "false"
    return value


def _rows(
    archive: zipfile.ZipFile, path: str, strings: Sequence[str], check_time: Callable[[], None]
) -> Iterator[list[str]]:
    with archive.open(path) as stream:
        info = safe.StreamInfo()
        expected = 1
        for row in safe.iter_records(
            stream,
            lambda e: e.tag == f"{{{MAIN}}}row",
            info,
            what="The spreadsheet's sheet",
            check_time=check_time,
        ):
            number = int(row.get("r") or expected)
            while expected < number:
                yield []
                expected += 1
            expected = number + 1
            cells: list[str] = []
            for position, cell in enumerate(row.iterfind("m:c", NS)):
                match = _CELL_REF.match(cell.get("r") or "")
                index = _column_index(match.group(1)) if match else position
                if index >= limits.MAX_COLUMNS:
                    raise ExtensionError(
                        errors.LIMIT_EXCEEDED,
                        f"The spreadsheet has more than {limits.MAX_COLUMNS} columns",
                    )
                while len(cells) < index:
                    cells.append("")
                cells.append(_cell_value(cell, strings))
            yield cells


# ---------------------------------------------------------------------------
# Writing

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'  # noqa: E501
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'  # noqa: E501
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'  # noqa: E501
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'  # noqa: E501
    "</Types>"
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<Relationships xmlns="{PKG_REL}">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'  # noqa: E501
    "</Relationships>"
)
_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<Relationships xmlns="{PKG_REL}">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'  # noqa: E501
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'  # noqa: E501
    "</Relationships>"
)
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<styleSheet xmlns="{MAIN}">'
    '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
    "</styleSheet>"
)
_SHEET_NAME_FORBIDDEN = re.compile(r"[\\/?*\[\]:]")


def column_letters(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def check_sheet_name(name: str) -> str:
    if not name or len(name) > 31 or _SHEET_NAME_FORBIDDEN.search(name) or name.startswith("'"):
        raise errors.invalid("Sheet name must be 1–31 characters without \\ / ? * [ ] :")
    convert.check_text(name, "Sheet name")
    return name


class Writer:
    """Writes rows of texts to one sheet, streaming."""

    def __init__(self, path: str, sheet_name: str) -> None:
        self.sheet_name = check_sheet_name(sheet_name)
        self.archive = zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED)
        self.rows = 0
        self.truncated_cells = 0
        self._stream = self.archive.open("xl/worksheets/sheet1.xml", "w", force_zip64=True)
        self._xf = etree.xmlfile(self._stream, encoding="UTF-8")
        self._context = self._xf.__enter__()
        self._context.write_declaration(standalone=True)
        self._sheet = self._context.element(f"{{{MAIN}}}worksheet", nsmap={None: MAIN})
        self._sheet.__enter__()
        self._data = self._context.element(f"{{{MAIN}}}sheetData")
        self._data.__enter__()

    def row(self, cells: Sequence[str], where: str) -> None:
        if self.rows >= MAX_ROWS:
            raise ExtensionError(errors.LIMIT_EXCEEDED, f"XLSX holds at most {MAX_ROWS} rows")
        self.rows += 1
        row = etree.Element(f"{{{MAIN}}}row", r=str(self.rows))
        for index, value in enumerate(cells):
            if value is None or value == "":
                continue
            text = convert.check_text(str(value), f"{where}, column {index + 1}")
            if len(text) > MAX_CELL_TEXT:
                text = text[:MAX_CELL_TEXT]
                self.truncated_cells += 1
            cell = etree.SubElement(
                row, f"{{{MAIN}}}c", r=f"{column_letters(index)}{self.rows}", t="inlineStr"
            )
            container = etree.SubElement(cell, f"{{{MAIN}}}is")
            node = etree.SubElement(container, f"{{{MAIN}}}t")
            node.text = text
            if text != text.strip():
                node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        self._context.write(row)

    def close(self) -> None:
        self._data.__exit__(None, None, None)
        self._sheet.__exit__(None, None, None)
        self._xf.__exit__(None, None, None)
        self._stream.close()
        workbook = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>'
            f'<sheet name="{_attribute(self.sheet_name)}" sheetId="1" r:id="rId1"/>'
            "</sheets></workbook>"
        )
        self.archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        self.archive.writestr("_rels/.rels", _ROOT_RELS)
        self.archive.writestr("xl/workbook.xml", workbook)
        self.archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        self.archive.writestr("xl/styles.xml", _STYLES)
        self.archive.close()


def _attribute(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


__all__ = ["Writer", "check_sheet_name", "column_letters", "read_rows"]
