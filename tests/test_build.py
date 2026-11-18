"""Build XML from records, JSON ↔ XML, CSV ↔ XML, XLSX ↔ XML."""

from __future__ import annotations

import io
import json
import zipfile
from decimal import Decimal

import pytest
from lxml import etree
from support import failed, ok_with_file

from runtime import errors

G = "http://base.google.com/ns/1.0"
STRUCTURE = {
    "root": "rss",
    "attributes": {"version": "2.0"},
    "namespaces": {"g": G},
    "wrapper": ["channel"],
    "record": "item",
    "fields": [
        {"element": "g:id", "value": "sku", "required": True},
        {"element": "title", "value": "name", "cdata": True},
        {
            "element": "g:price",
            "value": "price",
            "fields": [
                {"attribute": "currency", "const": "EUR"},
            ],
        },
        {"element": "g:image_link", "each": "images", "value": "."},
        {
            "element": "g:shipping",
            "fields": [
                {"element": "g:country", "value": "ship.country"},
            ],
        },
        {"element": "note", "value": "missing"},
    ],
}
RECORDS = [
    {
        "sku": "A-1",
        "name": 'Vynas <Raudonas> & "Sausas"',
        "price": Decimal("12.50"),
        "images": ["a.jpg", "b.jpg"],
        "ship": {"country": "LT"},
    },
    {"sku": "A-2", "name": "Sūris", "price": 3, "images": [], "ship": {}},
]


def _parse(body: bytes) -> etree._Element:
    return etree.fromstring(body)


def test_build_follows_the_structure() -> None:
    result, body = ok_with_file("build_xml", {"records": RECORDS, "structure": STRUCTURE})
    root = _parse(body)
    assert root.tag == "rss" and root.get("version") == "2.0"
    items = root.findall("channel/item")
    assert len(items) == 2 == result["record_count"]
    first = items[0]
    assert first.findtext(f"{{{G}}}id") == "A-1"
    assert first.findtext("title") == 'Vynas <Raudonas> & "Sausas"'
    assert b'<![CDATA[Vynas <Raudonas> & "Sausas"]]>' in body
    price = first.find(f"{{{G}}}price")
    assert price.text == "12.50" and price.get("currency") == "EUR"
    assert [e.text for e in first.findall(f"{{{G}}}image_link")] == ["a.jpg", "b.jpg"]
    assert first.findtext(f"{{{G}}}shipping/{{{G}}}country") == "LT"
    assert first.find("note") is None
    second = items[1]
    assert second.find(f"{{{G}}}image_link") is None
    assert second.find(f"{{{G}}}shipping") is None
    assert body.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n')
    assert result["xml"] == body.decode("utf-8")
    assert result["size_bytes"] == len(body)


def test_text_is_escaped_without_cdata() -> None:
    _, body = ok_with_file(
        "build_xml",
        {
            "records": [{"t": 'a < b & c > "d"'}],
            "structure": {
                "root": "r",
                "record": "i",
                "fields": [{"element": "t", "value": "t"}, {"attribute": "a", "value": "t"}],
            },
        },
    )
    assert b'<t>a &lt; b &amp; c &gt; "d"</t>' in body
    assert b'a="a &lt; b &amp; c &gt; &quot;d&quot;"' in body
    assert _parse(body).findtext("i/t") == 'a < b & c > "d"'


@pytest.mark.parametrize("bad", ["\x00", "\x01", "\x0b", "\x1f", "￾", "￿"])
def test_characters_xml_does_not_allow_are_refused_with_record_and_field(bad: str) -> None:
    response = failed(
        "build_xml",
        {
            "records": [{"t": "ok"}, {"t": f"x{bad}y"}],
            "structure": {"root": "r", "record": "i", "fields": [{"element": "t", "value": "t"}]},
        },
    )
    assert response["error_code"] == errors.INVALID_XML_CHARACTER
    assert "Record 2" in response["error"]
    assert "U+" in response["error"]


def test_tab_newline_and_astral_characters_are_allowed() -> None:
    _, body = ok_with_file(
        "build_xml",
        {
            "records": [{"t": "a\tb\nc 😀"}],
            "structure": {"root": "r", "record": "i", "fields": [{"element": "t", "value": "t"}]},
        },
    )
    assert _parse(body).findtext("i/t") == "a\tb\nc 😀"


def test_required_empty_value_stops_the_step() -> None:
    response = failed("build_xml", {"records": [{"x": ""}], "structure": STRUCTURE})
    assert response["error_code"] == errors.INVALID_STRUCTURE
    assert "Record 1" in response["error"]


def test_cdata_with_its_end_marker_falls_back_to_escaped_text() -> None:
    result, body = ok_with_file(
        "build_xml",
        {
            "records": [{"t": "a]]>b"}],
            "structure": {
                "root": "r",
                "record": "i",
                "fields": [{"element": "t", "value": "t", "cdata": True}],
            },
        },
    )
    assert _parse(body).findtext("i/t") == "a]]>b"
    assert "CDATA" in result["warnings"][0]


@pytest.mark.parametrize("encoding", ["windows-1257", "ISO-8859-13", "UTF-16", "US-ASCII"])
def test_encoding_and_declaration(encoding: str) -> None:
    result, body = ok_with_file(
        "build_xml",
        {
            "records": [{"t": "Ąžuolas"}],
            "encoding": encoding,
            "pretty": False,
            "structure": {"root": "r", "record": "i", "fields": [{"element": "t", "value": "t"}]},
        },
    )
    assert _parse(body).findtext("i/t") == "Ąžuolas"
    inline = result["xml"]
    assert isinstance(inline, str)
    assert "Ąžuolas" in inline or "&#" in inline
    if encoding != "UTF-16":
        assert body.startswith(f'<?xml version="1.0" encoding="{encoding}"?>'.encode())


def test_without_declaration_and_without_indent() -> None:
    _, body = ok_with_file(
        "build_xml",
        {
            "records": [{"t": "x"}],
            "xml_declaration": False,
            "pretty": False,
            "structure": {"root": "r", "record": "i", "fields": [{"element": "t", "value": "t"}]},
        },
    )
    assert body == b"<r><i><t>x</t></i></r>"


def test_without_fields_records_follow_the_json_convention() -> None:
    _, body = ok_with_file(
        "build_xml",
        {
            "records": [{"@id": 7, "name": "A", "tag": ["x", "y"], "d": {"#cdata": "<z>"}}],
            "structure": {"root": "items", "record": "item"},
            "pretty": False,
        },
    )
    assert body.endswith(
        b'<items><item id="7"><name>A</name><tag>x</tag><tag>y</tag><d><![CDATA[<z>]]></d>'
        b"</item></items>"
    )


def test_records_from_a_jsonl_file() -> None:
    lines = b'{"t": "a"}\n{"t": "b"}\n'
    result, body = ok_with_file(
        "build_xml",
        {"structure": {"root": "r", "record": "i"}},
        files={"records_file": ("f", lines)},
    )
    assert result["record_count"] == 2
    assert [e.text for e in _parse(body).iter("t")] == ["a", "b"]


def test_records_from_a_json_file_keep_exact_decimals() -> None:
    _, body = ok_with_file(
        "build_xml",
        {"structure": {"root": "r", "record": "i"}, "pretty": False},
        files={"records_file": ("f", b'[{"p": 12.50}, {"p": 0.1}]')},
    )
    assert b"<p>12.50</p>" in body and b"<p>0.1</p>" in body


@pytest.mark.parametrize(
    ("structure", "code"),
    [
        ({"record": "i"}, errors.INVALID_STRUCTURE),
        ({"root": "r", "record": "1bad"}, errors.INVALID_NAME),
        (
            {"root": "r", "record": "i", "fields": [{"element": "x:y", "value": "a"}]},
            errors.INVALID_NAME,
        ),
        (
            {"root": "r", "record": "i", "fields": [{"element": "a", "attribute": "b"}]},
            errors.INVALID_STRUCTURE,
        ),
        (
            {"root": "r", "record": "i", "fields": [{"element": "a", "bogus": 1}]},
            errors.INVALID_STRUCTURE,
        ),
        ({"root": "r", "record": "i", "extra": 1}, errors.INVALID_STRUCTURE),
    ],
)
def test_a_bad_structure_is_refused(structure: dict, code: str) -> None:
    response = failed("build_xml", {"records": [{"a": "1"}], "structure": structure})
    assert response["error_code"] == code


def test_records_must_be_objects() -> None:
    response = failed("build_xml", {"records": [1], "structure": {"root": "r", "record": "i"}})
    assert response["error_code"] == errors.INVALID_STRUCTURE


# ---------------------------------------------------------------------------
# JSON


DOCUMENT = (
    '<catalog xmlns:g="urn:g" version="1"><g:item id="1"><name>A &amp; B</name><tag>x</tag>'
    "<tag>y</tag><p>Hello <b>big</b> world</p></g:item></catalog>"
)


def test_xml_to_json_and_back_keeps_the_document() -> None:
    result, body = ok_with_file("xml_to_json", {"xml": DOCUMENT})
    data = json.loads(body)
    assert result["data"] == data
    assert data["catalog"]["@xmlns:g"] == "urn:g"
    assert data["catalog"]["g:item"]["tag"] == ["x", "y"]
    back, xml = ok_with_file(
        "json_to_xml", {"data": data, "pretty": False, "xml_declaration": False}
    )
    assert etree.tostring(etree.fromstring(xml), method="c14n") == etree.tostring(
        etree.fromstring(DOCUMENT.encode()), method="c14n"
    )


def test_json_to_xml_from_a_file_and_a_text() -> None:
    _, body = ok_with_file("json_to_xml", files={"json_file": ("f", b'{"r": {"a": 1.50}}')})
    assert b"<a>1.50</a>" in body
    _, body = ok_with_file("json_to_xml", {"data": '{"r": {"a": true}}'})
    assert b"<a>true</a>" in body


def test_json_to_xml_needs_a_root_for_a_list() -> None:
    response = failed("json_to_xml", {"data": [{"a": 1}]})
    assert response["error_code"] == errors.INVALID_STRUCTURE
    _, body = ok_with_file(
        "json_to_xml",
        {
            "data": [{"a": 1}, {"a": 2}],
            "root_element": "rows",
            "item_element": "row",
            "pretty": False,
            "xml_declaration": False,
        },
    )
    assert body == b"<rows><row><a>1</a></row><row><a>2</a></row></rows>"


def test_json_to_xml_refuses_bad_names_and_characters() -> None:
    assert failed("json_to_xml", {"data": {"r": {"a b": 1}}})["error_code"] == errors.INVALID_NAME
    assert failed("json_to_xml", {"data": {"r": "\x00"}})["error_code"] == (
        errors.INVALID_XML_CHARACTER
    )


def test_json_to_xml_reports_invalid_json() -> None:
    response = failed("json_to_xml", files={"json_file": ("f", b"{broken")})
    assert response["error_code"] == errors.INVALID_JSON
    assert "line 1" in response["error"]


# ---------------------------------------------------------------------------
# CSV


ITEMS = (
    b'<feed><item id="1"><name>A, "quoted"</name><price currency="EUR">1.5</price></item>'
    b'<item id="2"><name>B</name><extra>e</extra></item></feed>'
)


def test_xml_to_csv_takes_columns_from_the_records() -> None:
    result, body = ok_with_file(
        "xml_to_csv", {"record_element": "item"}, files={"xml_file": ("f", ITEMS)}
    )
    assert result["columns"] == ["@id", "name", "price.@currency", "price.#text", "extra"]
    assert body.decode().splitlines() == [
        "@id,name,price.@currency,price.#text,extra",
        '1,"A, ""quoted""",EUR,1.5,',
        "2,B,,,e",
    ]
    assert result["record_count"] == 2
    assert result["csv"] == body.decode()


def test_xml_to_csv_with_xpath_columns_semicolon_and_bom() -> None:
    result, body = ok_with_file(
        "xml_to_csv",
        {
            "record_element": "item",
            "delimiter": "semicolon",
            "excel_bom": True,
            "columns": [
                {"name": "id", "xpath": "@id"},
                {"name": "name", "xpath": "name"},
                {"name": "has_price", "xpath": "boolean(price)"},
            ],
        },
        files={"xml_file": ("f", ITEMS)},
    )
    assert body.startswith(b"\xef\xbb\xbf")
    assert body.decode("utf-8-sig").splitlines() == [
        "id;name;has_price",
        '1;"A, ""quoted""";true',
        "2;B;false",
    ]
    assert result["csv"].startswith("id;")


def test_xml_to_csv_without_header() -> None:
    _, body = ok_with_file(
        "xml_to_csv",
        {"record_element": "item", "header": False, "columns": [{"name": "id", "xpath": "@id"}]},
        files={"xml_file": ("f", ITEMS)},
    )
    assert body.decode().splitlines() == ["1", "2"]


def test_csv_to_xml() -> None:
    csv_text = 'SKU;Pavadinimas;Kaina (EUR);2nd\nA-1;Vynas & sūris;12,50;x\n;;;\nA-2;"B;C";3;\n'
    result, body = ok_with_file(
        "csv_to_xml",
        {
            "csv": csv_text,
            "delimiter": "semicolon",
            "root_element": "products",
            "record_element": "product",
            "attribute_columns": ["SKU"],
            "pretty": False,
        },
    )
    root = _parse(body)
    products = root.findall("product")
    assert len(products) == 2 == result["record_count"]
    assert products[0].get("SKU") == "A-1"
    assert products[0].findtext("Pavadinimas") == "Vynas & sūris"
    assert products[0].findtext("Kaina__EUR_") == "12,50"
    assert products[1].findtext("Pavadinimas") == "B;C"
    assert products[1].find("_2nd") is not None
    assert {"column": "2nd", "element": "_2nd"} in result["columns"]
    assert any("Kaina" in w for w in result["warnings"])


def test_csv_to_xml_skip_empty_and_file_encoding() -> None:
    data = "a,b\nĄ,\n".encode("cp1257")
    _, body = ok_with_file(
        "csv_to_xml",
        {"csv_encoding": "windows-1257", "skip_empty": True, "pretty": False},
        files={"csv_file": ("f", data)},
    )
    assert body.endswith(b"<rows><row><a>\xc4\x84</a></row></rows>")


def test_csv_to_xml_wrong_encoding_is_reported() -> None:
    response = failed("csv_to_xml", files={"csv_file": ("f", "a\nĄ".encode("cp1257"))})
    assert response["error_code"] == errors.INVALID_CSV


def test_csv_to_xml_refuses_control_characters() -> None:
    response = failed("csv_to_xml", {"csv": "a\nx\x01y\n"})
    assert response["error_code"] == errors.INVALID_XML_CHARACTER
    assert "Row 2" in response["error"]


def test_csv_without_header_is_refused() -> None:
    assert failed("csv_to_xml", {"csv": " "})["error_code"] in {
        errors.INVALID_CSV,
        errors.INVALID_PAYLOAD,
    }


# ---------------------------------------------------------------------------
# XLSX


def test_xml_to_xlsx_and_back() -> None:
    result, body = ok_with_file(
        "xml_to_xlsx",
        {"record_element": "item", "sheet_name": "Prekės"},
        files={"xml_file": ("f", ITEMS)},
    )
    assert result["record_count"] == 2
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = set(archive.namelist())
        assert {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml"} <= names
        sheet = etree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert len(sheet.findall(".//{*}row")) == 3
    back, xml = ok_with_file(
        "xlsx_to_xml",
        {"pretty": False, "record_element": "item"},
        files={"xlsx_file": ("x", body)},
    )
    assert back["sheet"] == "Prekės"
    assert back["record_count"] == 2
    items = _parse(xml).findall("item")
    assert items[0].findtext("name") == 'A, "quoted"'
    assert items[0].findtext("_id") == "1"
    assert items[1].findtext("extra") == "e"


def _workbook(sheet_xml: str, shared: str | None = None, sheets: str | None = None) -> bytes:
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
            + (sheets or '<sheet name="Data" sheetId="1" r:id="rId1"/>')
            + "</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="{pkg}"><Relationship Id="rId1" Type="{rel}/worksheet" '
            'Target="worksheets/sheet1.xml"/><Relationship Id="rId9" '
            f'Type="{rel}/sharedStrings" Target="sharedStrings.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{main}"><sheetData>{sheet_xml}</sheetData></worksheet>',
        )
        if shared is not None:
            archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{main}">{shared}</sst>')
    return buffer.getvalue()


def test_xlsx_shared_strings_numbers_gaps_and_booleans() -> None:
    data = _workbook(
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2"><v>12.5</v></c><c r="C2" t="b"><v>1</v></c></row>'
        '<row r="4"><c r="B4" t="inlineStr"><is><t>in</t><r><t>line</t></r></is></c></row>',
        shared="<si><t>Name</t></si><si><r><t>Fl</t></r><r><t>ag</t></r></si>",
    )
    result, xml = ok_with_file("xlsx_to_xml", {"pretty": False}, files={"xlsx_file": ("x", data)})
    assert result["sheet"] == "Data"
    rows = _parse(xml).findall("row")
    assert [r.findtext("Name") for r in rows] == ["12.5", ""]
    assert rows[0].findtext("Flag") == "true"
    assert rows[1].findtext("column_2") == "inline"


def test_xlsx_named_sheet_missing_and_not_a_zip() -> None:
    data = _workbook('<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>')
    response = failed("xlsx_to_xml", {"sheet": "Other"}, files={"xlsx_file": ("x", data)})
    assert response["error_code"] == errors.INVALID_XLSX
    response = failed("xlsx_to_xml", files={"xlsx_file": ("x", b"not a zip")})
    assert response["error_code"] == errors.INVALID_XLSX


def test_xlsx_parts_with_a_dtd_are_refused() -> None:
    data = _workbook('<row r="1"/>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w") as target:
        for name in source.namelist():
            body = source.read(name)
            if name.endswith("sheet1.xml"):
                body = b'<!DOCTYPE w [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + body
            target.writestr(name, body)
    response = failed("xlsx_to_xml", files={"xlsx_file": ("x", buffer.getvalue())})
    assert response["error_code"] in {errors.DTD_FORBIDDEN, errors.INVALID_XLSX}


def test_xlsx_sheet_name_is_checked() -> None:
    response = failed(
        "xml_to_xlsx",
        {"record_element": "item", "sheet_name": "a/b"},
        files={"xml_file": ("f", ITEMS)},
    )
    assert response["error_code"] == errors.INVALID_PAYLOAD


def test_xlsx_refuses_control_characters() -> None:
    response = failed(
        "xml_to_xlsx",
        {"record_element": "item", "columns": [{"name": "x\x01", "xpath": "@id"}]},
        files={"xml_file": ("f", ITEMS)},
    )
    assert response["error_code"] == errors.INVALID_XML_CHARACTER
