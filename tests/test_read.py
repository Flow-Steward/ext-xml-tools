"""Parse XML to records, Parse large XML to JSONL file, Select with XPath, Check XML."""

from __future__ import annotations

import json

import pytest
from support import failed, ok, ok_with_file, run

from runtime import errors

FEED = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0"><channel><title>Shop</title>'
    '<item><g:id>1</g:id><title>Vynas &amp; sūris</title><g:price currency="EUR">12.50</g:price>'
    "<image>a.jpg</image><image>b.jpg</image></item>"
    "<item><g:id>2</g:id><title><![CDATA[<b>Bold</b>]]></title><g:price>3</g:price></item>"
    "</channel></rss>"
)
G = {"g": "http://base.google.com/ns/1.0"}


def test_records_follow_the_convention() -> None:
    result = ok("parse_xml", {"xml": FEED, "record_xpath": "//item"})
    first, second = result["records"]
    assert first == {
        "g:id": "1",
        "title": "Vynas & sūris",
        "g:price": {"@currency": "EUR", "#text": "12.50"},
        "image": ["a.jpg", "b.jpg"],
    }
    assert second["title"] == "<b>Bold</b>"
    assert result["count"] == result["total"] == 2
    assert result["truncated"] is False
    assert result["root_element"] == "rss"
    assert result["warnings"] == []


def test_default_records_are_the_roots_children() -> None:
    result = ok("parse_xml", {"xml": "<r><a x='1'/><b>t</b></r>"})
    assert result["records"] == [{"@x": "1"}, {"b": "t"}]


def test_namespaces_can_be_stripped() -> None:
    result = ok("parse_xml", {"xml": FEED, "record_xpath": "//item", "namespace_mode": "strip"})
    assert result["records"][0]["id"] == "1"
    assert result["records"][0]["price"]["#text"] == "12.50"


def test_namespace_declarations_are_kept_where_they_are_declared() -> None:
    result = ok("parse_xml", {"xml": '<r><a xmlns:p="urn:p"><p:b>1</p:b></a></r>'})
    assert result["records"] == [{"@xmlns:p": "urn:p", "p:b": "1"}]


def test_xpath_prefixes_need_not_match_the_document() -> None:
    result = ok(
        "parse_xml",
        {"xml": FEED, "record_xpath": "//item[google:id='2']", "namespaces": {"google": G["g"]}},
    )
    assert result["count"] == 1


def test_mixed_content_keeps_its_order() -> None:
    result = ok("parse_xml", {"xml": "<r><p>Hello <b>big</b> world</p></r>"})
    assert result["records"] == [{"#mixed": ["Hello ", {"b": "big"}, " world"]}]


def test_options_change_the_keys_and_force_lists() -> None:
    result = ok(
        "parse_xml",
        {
            "xml": "<r><i id='1'><t>x</t></i></r>",
            "attribute_prefix": "_",
            "text_key": "value",
            "force_list": ["t"],
        },
    )
    assert result["records"] == [{"_id": "1", "t": ["x"]}]


def test_flatten_gives_flat_keys() -> None:
    result = ok("parse_xml", {"xml": FEED, "record_xpath": "//item", "flatten": True})
    assert result["records"][0]["g:price.@currency"] == "EUR"
    assert result["records"][0]["image.1"] == "b.jpg"


def test_max_records_truncates() -> None:
    result = ok("parse_xml", {"xml": FEED, "record_xpath": "//item", "max_records": 1})
    assert result["count"] == 1
    assert result["total"] == 2
    assert result["truncated"] is True


def test_comments_are_left_out_with_a_warning() -> None:
    result = ok("parse_xml", {"xml": "<r><i><!-- c --><a>1</a></i></r>"})
    assert result["records"] == [{"a": "1"}]
    assert "comments" in result["warnings"][0]


@pytest.mark.parametrize(
    ("encoding", "codec"),
    [("windows-1257", "cp1257"), ("ISO-8859-13", "iso-8859-13"), ("UTF-16", "utf-16")],
)
def test_the_declared_encoding_is_honoured(encoding: str, codec: str) -> None:
    text = f'<?xml version="1.0" encoding="{encoding}"?><r><i>Ąžuolas ščiū</i></r>'
    result = ok("parse_xml", files={"xml_file": ("f", text.encode(codec))})
    assert result["records"] == [{"i": "Ąžuolas ščiū"}]


def test_typed_text_may_declare_another_encoding() -> None:
    text = '<?xml version="1.0" encoding="windows-1257"?><r><i>Ąč</i></r>'
    result = ok("parse_xml", {"xml": text})
    assert result["records"] == [{"i": "Ąč"}]


def test_text_and_file_together_are_refused() -> None:
    response = failed("parse_xml", {"xml": "<r/>"}, files={"xml_file": ("f", b"<r/>")})
    assert response["error_code"] == errors.INVALID_PAYLOAD


def test_no_document_is_refused() -> None:
    assert failed("parse_xml", {})["error_code"] == errors.INVALID_PAYLOAD


def test_a_file_that_is_not_granted_is_refused() -> None:
    response, _ = run("parse_xml", {"xml_file": "artifact:missing"})
    assert response["ok"] is False
    assert response["error_code"] == errors.ARTIFACT_INPUT_UNAVAILABLE


def test_an_xpath_that_selects_values_is_refused_as_records() -> None:
    response = failed("parse_xml", {"xml": FEED, "record_xpath": "count(//item)"})
    assert response["error_code"] == errors.INVALID_XPATH


# ---------------------------------------------------------------------------
# Streaming


def _big_feed(count: int) -> bytes:
    items = "".join(
        f'<item id="{i}"><name>Prekė {i}</name><price>{i}.99</price></item>' for i in range(count)
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><feed><meta>x</meta>{items}</feed>'.encode()


def test_stream_writes_one_json_line_per_record() -> None:
    result, body = ok_with_file(
        "parse_xml_stream", {"record_element": "item"}, files={"xml_file": ("f", _big_feed(20_000))}
    )
    lines = body.decode("utf-8").splitlines()
    assert len(lines) == 20_000 == result["record_count"]
    assert json.loads(lines[7]) == {"@id": "7", "name": "Prekė 7", "price": "7.99"}
    assert len(result["sample"]) == 5
    assert result["root_element"] == "feed"
    assert result["encoding"] == "UTF-8"
    assert result["truncated"] is False
    assert result["sha256"] and result["size_bytes"] == len(body)


def test_stream_max_records_stops_early() -> None:
    result, body = ok_with_file(
        "parse_xml_stream",
        {"record_element": "item", "max_records": 3},
        files={"xml_file": ("f", _big_feed(50))},
    )
    assert result["record_count"] == 3
    assert result["truncated"] is True
    assert len(body.splitlines()) == 3


def test_stream_takes_outermost_records_and_prefixed_names() -> None:
    document = b'<r xmlns:g="urn:g"><g:item><g:item>inner</g:item></g:item><item>other</item></r>'
    result, body = ok_with_file(
        "parse_xml_stream",
        {"record_element": "x:item", "namespaces": {"x": "urn:g"}},
        files={"xml_file": ("f", document)},
    )
    assert result["record_count"] == 1
    assert json.loads(body) == {"g:item": "inner"}


def test_stream_with_no_records_warns() -> None:
    result, body = ok_with_file(
        "parse_xml_stream", {"record_element": "nothing"}, files={"xml_file": ("f", b"<r/>")}
    )
    assert result["record_count"] == 0
    assert body == b""
    assert result["warnings"]


def test_stream_needs_a_known_prefix() -> None:
    response = failed(
        "parse_xml_stream", {"record_element": "g:item"}, files={"xml_file": ("f", b"<r/>")}
    )
    assert response["error_code"] == errors.INVALID_NAME


def test_stream_reports_a_broken_file_with_its_line() -> None:
    response = failed(
        "parse_xml_stream", {"record_element": "i"}, files={"xml_file": ("f", b"<r>\n<i>\n</r>")}
    )
    assert response["error_code"] == errors.INVALID_XML
    assert "line" in response["error"]


# ---------------------------------------------------------------------------
# XPath


def test_xpath_values() -> None:
    result = ok("select_xpath", {"xml": FEED, "xpath": "//item/title"})
    assert result["result_type"] == "nodeset"
    assert result["values"] == ["Vynas & sūris", "<b>Bold</b>"]
    assert result["first"] == "Vynas & sūris"
    assert result["count"] == 2


@pytest.mark.parametrize(
    ("xpath", "kind", "value"),
    [
        ("count(//item)", "number", 2),
        ("sum(//g:price)", "number", 15.5),
        ("boolean(//item[g:id='2'])", "boolean", True),
        ("string(//channel/title)", "string", "Shop"),
    ],
)
def test_xpath_scalars(xpath: str, kind: str, value: object) -> None:
    result = ok("select_xpath", {"xml": FEED, "xpath": xpath, "namespaces": G})
    assert result["result_type"] == kind
    assert result["first"] == value


def test_xpath_attributes_and_nodes() -> None:
    result = ok("select_xpath", {"xml": FEED, "xpath": "//g:price/@currency", "namespaces": G})
    assert result["values"] == ["EUR"]
    result = ok(
        "select_xpath", {"xml": FEED, "xpath": "//g:price", "namespaces": G, "return": "nodes"}
    )
    assert result["values"][0].startswith("<g:price")
    assert 'currency="EUR"' in result["values"][0]


def test_xpath_records() -> None:
    result = ok("select_xpath", {"xml": FEED, "xpath": "//item[2]", "return": "records"})
    assert result["values"][0]["item"]["g:id"] == "2"


def test_xpath_variables() -> None:
    result = ok(
        "select_xpath",
        {"xml": FEED, "xpath": "//item[g:id=$id]/title", "namespaces": G, "variables": {"id": "1"}},
    )
    assert result["values"] == ["Vynas & sūris"]


def test_xpath_max_results() -> None:
    result = ok("select_xpath", {"xml": FEED, "xpath": "//*", "max_results": 2})
    assert len(result["values"]) == 2
    assert result["truncated"] is True


@pytest.mark.parametrize("xpath", ["//item[", "//unknown:x", "$missing"])
def test_bad_xpath_is_reported(xpath: str) -> None:
    response = failed("select_xpath", {"xml": FEED, "xpath": xpath})
    assert response["error_code"] == errors.INVALID_XPATH


def test_a_default_namespace_prefix_is_refused_for_xpath() -> None:
    response = failed("select_xpath", {"xml": FEED, "xpath": "//a", "namespaces": {"": "urn:x"}})
    assert response["error_code"] == errors.INVALID_PAYLOAD


# ---------------------------------------------------------------------------
# Check


def test_check_reports_facts_of_a_good_document() -> None:
    result = ok("check_xml", {"xml": FEED})
    assert result["well_formed"] is True
    assert result["errors"] == []
    assert result["root_element"] == "rss"
    assert result["declared_encoding"] == "UTF-8"
    assert result["xml_version"] == "1.0"
    assert result["element_count"] == 13
    assert result["max_depth"] == 4


def test_check_reports_a_namespace() -> None:
    result = ok("check_xml", {"xml": '<f xmlns="http://www.w3.org/2005/Atom"/>'})
    assert result["root_namespace"] == "http://www.w3.org/2005/Atom"


@pytest.mark.parametrize("document", ["<a>", "<a></b>", "text", "<a/><b/>", "<a x='1' x='2'/>"])
def test_check_reports_a_broken_document_without_failing(document: str) -> None:
    result = ok("check_xml", {"xml": document})
    assert result["well_formed"] is False
    assert result["errors"][0]["code"] == errors.INVALID_XML


def test_check_reads_a_file() -> None:
    result = ok("check_xml", files={"xml_file": ("f", FEED.encode())})
    assert result["well_formed"] is True
