"""XSD, XSLT, Edit, Merge, Split, Format, Canonicalize, Compare."""

from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree
from support import failed, ok, ok_with_file

from runtime import errors

SCHEMA = (
    '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
    '<xs:element name="items"><xs:complexType><xs:sequence>'
    '<xs:element name="item" maxOccurs="unbounded"><xs:complexType><xs:sequence>'
    '<xs:element name="price" type="xs:decimal"/></xs:sequence>'
    '<xs:attribute name="id" type="xs:integer" use="required"/>'
    "</xs:complexType></xs:element></xs:sequence></xs:complexType></xs:element></xs:schema>"
)


def test_xsd_valid_document() -> None:
    result = ok(
        "validate_xsd",
        {"xml": '<items><item id="1"><price>1.5</price></item></items>', "schema": SCHEMA},
    )
    assert result == {"valid": True, "error_count": 0, "errors": [], "warnings": []}


def test_xsd_errors_have_lines_and_columns() -> None:
    document = '<items>\n<item id="x"><price>cheap</price></item>\n<item/></items>'
    result = ok("validate_xsd", {"xml": document, "schema": SCHEMA})
    assert result["valid"] is False
    assert result["error_count"] >= 3
    assert {e["line"] for e in result["errors"]} == {2, 3}
    assert all(e["column"] is None or isinstance(e["column"], int) for e in result["errors"])
    assert all(e["message"] for e in result["errors"])


def test_xsd_max_errors() -> None:
    document = "<items>" + "<item/>" * 20 + "</items>"
    result = ok("validate_xsd", {"xml": document, "schema": SCHEMA, "max_errors": 5})
    assert len(result["errors"]) == 5
    assert result["error_count"] == 40  # each item lacks its id and its price


def test_xsd_schema_as_a_file() -> None:
    result = ok("validate_xsd", {"xml": "<items/>"}, files={"schema_file": ("s", SCHEMA.encode())})
    assert result["valid"] is False


def test_xsd_with_a_broken_document_is_not_valid() -> None:
    result = ok("validate_xsd", {"xml": "<items>", "schema": SCHEMA})
    assert result["valid"] is False
    assert "line" in result["errors"][0]["message"]


@pytest.mark.parametrize(
    "schema",
    [
        "<notaschema/>",
        "<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'><xs:bogus/></xs:schema>",
        "<xs:schema",
    ],
)
def test_xsd_invalid_schema(schema: str) -> None:
    response = failed("validate_xsd", {"xml": "<a/>", "schema": schema})
    assert response["error_code"] == errors.INVALID_SCHEMA


STYLE = (
    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:param name="currency" select="\'USD\'"/>'
    '<xsl:output method="xml" encoding="UTF-8"/>'
    '<xsl:template match="/"><offers><xsl:for-each select="//item">'
    '<offer id="{@id}" currency="{$currency}"><xsl:value-of select="price"/></offer>'
    "</xsl:for-each></offers></xsl:template></xsl:stylesheet>"
)
ITEMS = '<items><item id="1"><price>1.5</price></item><item id="2"><price>3</price></item></items>'


def test_xslt_transforms_with_parameters() -> None:
    result, body = ok_with_file(
        "transform_xslt", {"xml": ITEMS, "stylesheet": STYLE, "parameters": {"currency": "EUR"}}
    )
    root = etree.fromstring(body)
    assert [o.get("currency") for o in root] == ["EUR", "EUR"]
    assert [o.text for o in root] == ["1.5", "3"]
    assert result["output"] == body.decode()


def test_xslt_parameter_text_is_not_xpath() -> None:
    _, body = ok_with_file(
        "transform_xslt",
        {"xml": ITEMS, "stylesheet": STYLE, "parameters": {"currency": "count(//*)"}},
    )
    assert b'currency="count(//*)"' in body


def test_xslt_text_output_and_messages() -> None:
    style = (
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/><xsl:template match="/">'
        "<xsl:message>counting</xsl:message>"
        '<xsl:for-each select="//item"><xsl:value-of select="@id"/>;</xsl:for-each>'
        "</xsl:template></xsl:stylesheet>"
    )
    result, body = ok_with_file("transform_xslt", {"xml": ITEMS, "stylesheet": style})
    assert body == b"1;2;"
    assert any("counting" in m for m in result["messages"])


def test_xslt_terminate_fails_the_step() -> None:
    style = (
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/"><xsl:message terminate="yes">stop here</xsl:message>'
        "</xsl:template></xsl:stylesheet>"
    )
    response = failed("transform_xslt", {"xml": ITEMS, "stylesheet": style})
    assert response["error_code"] == errors.TRANSFORM_FAILED


@pytest.mark.parametrize("style", ["<xsl:stylesheet", "<a/>"])
def test_xslt_invalid_stylesheet(style: str) -> None:
    response = failed("transform_xslt", {"xml": ITEMS, "stylesheet": style})
    assert response["error_code"] == errors.INVALID_STYLESHEET


# ---------------------------------------------------------------------------
# Edit


DOC = (
    '<?xml version="1.0" encoding="windows-1257"?>\n<shop xmlns:g="urn:g">\n'
    '  <item id="1"><name>A</name><price>1</price><stock>0</stock></item>\n'
    '  <item id="2"><name>B</name><price>2</price><stock>5</stock></item>\n</shop>'
)


def _edit(changes: list, **extra) -> tuple[dict, etree._Element, bytes]:  # type: ignore[type-arg]
    result, body = ok_with_file("edit_xml", {"xml": DOC, "changes": changes, **extra})
    return result, etree.fromstring(body), body


def test_edit_every_action() -> None:
    result, root, body = _edit(
        [
            {"action": "set_text", "xpath": "//item[@id='1']/price", "value": "9.99"},
            {"action": "set_attribute", "xpath": "//item", "name": "g:checked", "value": "yes"},
            {"action": "remove_attribute", "xpath": "//item[@id='2']", "name": "id"},
            {"action": "insert", "xpath": "//item[1]", "xml": "<note>new &amp; shiny</note>"},
            {
                "action": "insert",
                "xpath": "//item[1]",
                "position": "before",
                "xml": "<g:header>h</g:header>",
            },
            {"action": "insert", "xpath": "/shop", "position": "prepend", "xml": "<first/>"},
            {"action": "rename", "xpath": "//stock", "name": "qty"},
            {"action": "replace", "xpath": "//item[2]/name", "xml": "<title>Bee</title>"},
            {"action": "remove", "xpath": "//item[qty=0]/qty"},
        ],
        namespaces={"g": "urn:g"},
    )
    assert [a["matched"] for a in result["applied"]] == [1, 2, 1, 1, 1, 1, 2, 1, 1]
    items = root.findall("item")
    assert items[0].findtext("price") == "9.99"
    assert items[0].get("{urn:g}checked") == "yes"
    assert items[1].get("id") is None
    assert items[0].findtext("note") == "new & shiny"
    assert root[0].tag == "first"
    assert root.find("{urn:g}header").getnext() is items[0]
    assert items[0].find("qty") is None and items[1].findtext("qty") == "5"
    assert items[1].findtext("title") == "Bee" and items[1].find("name") is None
    assert body.startswith(b'<?xml version="1.0" encoding="windows-1257"?>')


def test_edit_remove_attributes_by_xpath() -> None:
    _, root, _ = _edit([{"action": "remove", "xpath": "//item/@id"}])
    assert all(item.get("id") is None for item in root.findall("item"))


def test_edit_set_text_escapes() -> None:
    _, root, body = _edit([{"action": "set_text", "xpath": "//item[1]/name", "value": "<x>&"}])
    assert root.findtext("item/name") == "<x>&"
    assert b"&lt;x&gt;&amp;" in body


def test_edit_change_that_matches_nothing() -> None:
    response = failed(
        "edit_xml", {"xml": DOC, "changes": [{"action": "remove", "xpath": "//nothing"}]}
    )
    assert response["error_code"] == errors.NO_MATCH
    result, _, _ = _edit([{"action": "remove", "xpath": "//nothing"}], require_match=False)
    assert result["applied"][0]["matched"] == 0
    assert result["warnings"]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"action": "remove", "xpath": "/shop"}, errors.INVALID_STRUCTURE),
        ({"action": "explode", "xpath": "/shop"}, errors.INVALID_STRUCTURE),
        ({"action": "insert", "xpath": "/shop", "xml": "text only"}, errors.INVALID_STRUCTURE),
        ({"action": "insert", "xpath": "/shop", "xml": "<a>"}, errors.INVALID_STRUCTURE),
        (
            {"action": "insert", "xpath": "/shop", "position": "after", "xml": "<a/>"},
            errors.INVALID_STRUCTURE,
        ),
        ({"action": "rename", "xpath": "//item", "name": "1x"}, errors.INVALID_NAME),
        ({"action": "set_text", "xpath": "//item", "value": "\x00"}, errors.INVALID_XML_CHARACTER),
        ({"action": "set_text", "xpath": "//item["}, errors.INVALID_XPATH),
    ],
)
def test_edit_refuses_bad_changes(change: dict, code: str) -> None:  # type: ignore[type-arg]
    response = failed("edit_xml", {"xml": DOC, "changes": [change]})
    assert response["error_code"] == code


def test_edit_replace_root() -> None:
    _, root, _ = _edit([{"action": "replace", "xpath": "/shop", "xml": "<store/>"}])
    assert root.tag == "store"


# ---------------------------------------------------------------------------
# Merge and split


def _rss(*ids: int) -> str:
    items = "".join(f"<item><id>{i}</id></item>" for i in ids)
    return f'<rss version="2.0"><channel><title>T</title>{items}</channel></rss>'


def test_merge_adds_records_into_the_first_document() -> None:
    result, body = ok_with_file(
        "merge_xml",
        {"xml_list": [_rss(3), _rss()], "record_xpath": "//item"},
        files={"xml_files": ("f1", _rss(1, 2).encode())},
    )
    root = etree.fromstring(body)
    assert [e.text for e in root.iter("id")] == ["1", "2", "3"]
    assert len(root.findall("channel/title")) == 1
    assert result["document_count"] == 3
    assert result["record_count"] == 3
    assert any("Document 3" in w for w in result["warnings"])


def test_merge_default_takes_the_roots_children() -> None:
    _, body = ok_with_file("merge_xml", {"xml_list": ["<r><a/></r>", "<x><b/><c/></x>"]})
    assert [e.tag for e in etree.fromstring(body)] == ["a", "b", "c"]


def test_merge_needs_two_documents() -> None:
    assert failed("merge_xml", {"xml_list": ["<r/>"]})["error_code"] == errors.INVALID_PAYLOAD


def test_merge_refuses_a_broken_document_and_names_it() -> None:
    response = failed("merge_xml", {"xml_list": ["<r/>", "<r>"]})
    assert response["error_code"] == errors.INVALID_XML
    assert "Document 2" in response["error"]


def test_split_keeps_the_frame_and_header() -> None:
    document = _rss(*range(1, 8)).encode()
    result, body = ok_with_file(
        "split_xml",
        {"record_element": "item", "records_per_part": 3},
        files={"xml_file": ("f", document)},
    )
    assert result["part_count"] == 3
    assert result["record_count"] == 7
    assert [p["records"] for p in result["parts"]] == [3, 3, 1]
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.namelist() == ["part-00001.xml", "part-00002.xml", "part-00003.xml"]
        raw = archive.read("part-00003.xml")
        last = etree.fromstring(raw)
    assert raw.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<rss')
    assert last.tag == "rss" and last.get("version") == "2.0"
    assert last.findtext("channel/title") == "T"
    assert [e.text for e in last.iter("id")] == ["7"]


def test_split_without_header() -> None:
    _, body = ok_with_file(
        "split_xml",
        {"record_element": "item", "records_per_part": 10, "copy_header": False},
        files={"xml_file": ("f", _rss(1, 2).encode())},
    )
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        part = etree.fromstring(archive.read("part-00001.xml"))
    assert part.find("channel/title") is None
    assert len(part.findall("channel/item")) == 2


def test_split_records_per_part_is_checked() -> None:
    response = failed(
        "split_xml",
        {"record_element": "item", "records_per_part": 0},
        files={"xml_file": ("f", b"<r/>")},
    )
    assert response["error_code"] == errors.INVALID_PAYLOAD


# ---------------------------------------------------------------------------
# Format, canonicalize, compare


def test_format_pretty_and_minify() -> None:
    _, pretty = ok_with_file("format_xml", {"xml": "<r><a><b>x</b></a></r>", "indent": 4})
    assert (
        pretty
        == b'<?xml version="1.0" encoding="UTF-8"?>\n<r>\n    <a>\n        <b>x</b>\n    </a>\n</r>'
    )
    _, small = ok_with_file(
        "format_xml",
        {
            "xml": '<r>\n  <a> x </a>\n  <!-- c -->\n  <p xml:space="preserve">  <i/>  </p>\n</r>',
            "mode": "minify",
            "remove_comments": True,
            "xml_declaration": False,
        },
    )
    assert small == b'<r><a> x </a><p xml:space="preserve">  <i/>  </p></r>'


def test_format_keeps_or_changes_the_encoding() -> None:
    source = '<?xml version="1.0" encoding="windows-1257"?><r>Ą</r>'.encode("cp1257")
    result, body = ok_with_file("format_xml", files={"xml_file": ("f", source)})
    assert body.startswith(b'<?xml version="1.0" encoding="windows-1257"?>')
    assert result["xml"].endswith("<r>Ą</r>")
    _, body = ok_with_file("format_xml", {"encoding": "UTF-8"}, files={"xml_file": ("f", source)})
    assert "Ą".encode() in body


def test_canonical_forms_match_for_equal_documents() -> None:
    first, a = ok_with_file("canonicalize_xml", {"xml": '<r b="2" a="1"><e/></r>'})
    second, b = ok_with_file("canonicalize_xml", {"xml": "<r a='1'  b='2'><e></e></r>"})
    assert a == b == b'<r a="1" b="2"><e></e></r>'
    assert first["sha256"] == second["sha256"]


@pytest.mark.parametrize("method", ["c14n", "c14n_exclusive", "c14n2"])
def test_canonical_methods(method: str) -> None:
    document = '<r xmlns:u="urn:u"><x><!-- c --></x></r>'
    _, body = ok_with_file(
        "canonicalize_xml", {"xml": document, "method": method, "with_comments": True}
    )
    assert b"<!-- c -->" in body
    if method in {"c14n_exclusive", "c14n2"}:
        assert b"urn:u" not in body
    else:
        assert b'xmlns:u="urn:u"' in body


def test_compare_equal_documents() -> None:
    result = ok(
        "compare_xml",
        {
            "left_xml": '<r a="1" b="2"><x> t </x></r>',
            "right_xml": "<r b='2' a='1'>\n  <x>t</x>\n</r>",
        },
    )
    assert result["equal"] is True
    assert result["differences"] == []


def test_compare_lists_differences_with_paths() -> None:
    left = '<r><i id="1"><p>1</p></i><i id="2"/><extra/></r>'
    right = '<r><i id="1" new="y"><p>2</p></i><j id="2"/></r>'
    result = ok("compare_xml", {"left_xml": left, "right_xml": right})
    kinds = {(d["path"], d["kind"]) for d in result["differences"]}
    assert ("/r/i[1]/@new", "attribute_missing_in_first") in kinds
    assert ("/r/i[1]/p", "text") in kinds
    assert ("/r/i[2]", "element_name") in kinds
    assert ("/r/extra", "missing_in_second") in kinds
    assert result["equal"] is False
    assert result["difference_count"] == 4


def test_compare_prefixes_and_whitespace_options() -> None:
    left = '<a:r xmlns:a="urn:x"><v>a  b</v></a:r>'
    right = '<b:r xmlns:b="urn:x"><v>a b</v></b:r>'
    assert ok("compare_xml", {"left_xml": left, "right_xml": right})["equal"] is True
    strict = ok(
        "compare_xml",
        {
            "left_xml": left,
            "right_xml": right,
            "ignore_prefixes": False,
            "ignore_whitespace": False,
        },
    )
    assert strict["difference_count"] == 1


def test_compare_max_differences() -> None:
    left = "<r>" + "<i>1</i>" * 30 + "</r>"
    right = "<r>" + "<i>2</i>" * 30 + "</r>"
    result = ok("compare_xml", {"left_xml": left, "right_xml": right, "max_differences": 5})
    assert len(result["differences"]) == 5
    assert result["difference_count"] == 30
    assert result["truncated"] is True


def test_compare_from_files() -> None:
    result = ok("compare_xml", files={"left_file": ("l", b"<r/>"), "right_file": ("r", b"<s/>")})
    assert result["equal"] is False
