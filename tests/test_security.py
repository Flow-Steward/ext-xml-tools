"""Security: DTDs, entities, XInclude, XSLT and XSD access, limits, time and error text."""

from __future__ import annotations

import os
import time

import pytest
from lxml import etree
from support import failed, ok, ok_with_file, run

from runtime import errors, limits, safe, sandbox
from runtime.errors import ExtensionError

BILLION_LAUGHS = (
    '<?xml version="1.0"?>\n<!DOCTYPE lolz [\n <!ENTITY lol "lol">\n'
    + "".join(
        f' <!ENTITY lol{i} "{("&lol" + (str(i - 1) if i > 1 else "") + ";") * 10}">\n'
        for i in range(1, 10)
    )
    + "]>\n<lolz>&lol9;</lolz>"
)
QUADRATIC = (
    '<?xml version="1.0"?><!DOCTYPE kaboom [<!ENTITY a "' + "x" * 50_000 + '">]>'
    "<kaboom>" + "&a;" * 2_000 + "</kaboom>"
)
XXE_FILE = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
XXE_NETWORK = (
    '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "http://127.0.0.1:9/x">]><r>&x;</r>'
)
EXTERNAL_DTD = '<?xml version="1.0"?><!DOCTYPE r SYSTEM "file:///etc/passwd"><r/>'
PARAMETER_ENTITY = (
    '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY % p SYSTEM "file:///etc/passwd"> %p;]><r/>'
)
ALL_DTD_ATTACKS = [BILLION_LAUGHS, QUADRATIC, XXE_FILE, XXE_NETWORK, EXTERNAL_DTD, PARAMETER_ENTITY]


@pytest.mark.parametrize("document", ALL_DTD_ATTACKS)
def test_every_dtd_is_refused_before_any_entity_is_used(document: str) -> None:
    started = time.monotonic()
    response = failed("parse_xml", {"xml": document})
    assert response["error_code"] == errors.DTD_FORBIDDEN
    assert "root:" not in response["error"]
    assert time.monotonic() - started < 10


@pytest.mark.parametrize("document", ALL_DTD_ATTACKS)
def test_the_streaming_reader_refuses_every_dtd_too(document: str) -> None:
    response = failed(
        "parse_xml_stream", {"record_element": "r"}, files={"xml_file": ("f1", document.encode())}
    )
    assert response["error_code"] in {errors.DTD_FORBIDDEN, errors.INVALID_XML}
    assert "root:" not in response["error"]


def test_a_dtd_in_utf16_is_refused() -> None:
    data = XXE_FILE.replace('<?xml version="1.0"?>', '<?xml version="1.0" encoding="UTF-16"?>')
    response = failed("parse_xml", files={"xml_file": ("f1", data.encode("utf-16"))})
    assert response["error_code"] == errors.DTD_FORBIDDEN


def test_check_xml_reports_a_dtd_as_not_allowed() -> None:
    result = ok("check_xml", {"xml": BILLION_LAUGHS})
    assert result["well_formed"] is False
    assert result["errors"][0]["code"] == errors.DTD_FORBIDDEN


def test_the_parser_options_are_set_explicitly() -> None:
    options = safe.SAFE_PARSER_OPTIONS
    assert options["resolve_entities"] is False
    assert options["no_network"] is True
    assert options["load_dtd"] is False
    assert options["huge_tree"] is False
    assert options["dtd_validation"] is False


def test_xinclude_is_never_processed() -> None:
    document = (
        '<r xmlns:xi="http://www.w3.org/2001/XInclude">'
        '<xi:include href="file:///etc/hostname" parse="text"/></r>'
    )
    result = ok("parse_xml", {"xml": document, "record_xpath": "/*"})
    record = result["records"][0]
    assert "xi:include" in record
    assert record["xi:include"]["@href"] == "file:///etc/hostname"
    hostname = open("/etc/hostname").read().strip() if os.path.exists("/etc/hostname") else ""
    if hostname:
        assert hostname not in str(record)


def test_the_source_never_calls_xinclude() -> None:
    from support import BUNDLE

    for path in (BUNDLE / "runtime").glob("*.py"):
        assert ".xinclude(" not in path.read_text(encoding="utf-8"), path


STYLE = '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">{}</xsl:stylesheet>'


def test_xslt_document_function_cannot_read_files() -> None:
    style = STYLE.format(
        '<xsl:template match="/"><out><xsl:copy-of select="document(\'file:///etc/passwd\')"/>'
        "</out></xsl:template>"
    )
    response, _ = run("transform_xslt", {"xml": "<r/>", "stylesheet": style})
    if response["ok"]:
        assert "root:" not in (response["result"]["output"] or "")
    else:
        assert response["error_code"] == errors.TRANSFORM_FAILED
        assert "root:" not in response["error"]


def test_xslt_document_function_cannot_reach_the_network() -> None:
    style = STYLE.format(
        '<xsl:template match="/"><out><xsl:copy-of select="document(\'http://127.0.0.1:9/x\')"/>'
        "</out></xsl:template>"
    )
    response, _ = run("transform_xslt", {"xml": "<r/>", "stylesheet": style})
    assert response["ok"] is False or "<out/>" in response["result"]["output"]


def test_xslt_cannot_write_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "written.txt"
    style = (
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        'xmlns:exsl="http://exslt.org/common" extension-element-prefixes="exsl">'
        f'<xsl:template match="/"><exsl:document href="{target}" method="text">'
        "secret</exsl:document><done/></xsl:template></xsl:stylesheet>"
    )
    response, _ = run("transform_xslt", {"xml": "<r/>", "stylesheet": style})
    assert not target.exists()
    assert response["ok"] is False
    assert response["error_code"] == errors.TRANSFORM_FAILED


@pytest.mark.parametrize("tag", ["import", "include"])
def test_xslt_cannot_load_other_stylesheets(tag: str) -> None:
    style = STYLE.format(f'<xsl:{tag} href="file:///etc/other.xsl"/>')
    response = failed("transform_xslt", {"xml": "<r/>", "stylesheet": style})
    assert response["error_code"] == errors.INVALID_STYLESHEET


def test_xslt_runs_without_extensions() -> None:
    style = (
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
        'xmlns:py="urn:python"><xsl:template match="/"><o><xsl:value-of select="py:run()"/>'
        "</o></xsl:template></xsl:stylesheet>"
    )
    response, _ = run("transform_xslt", {"xml": "<r/>", "stylesheet": style})
    assert response["ok"] is False
    assert response["error_code"] in {errors.TRANSFORM_FAILED, errors.INVALID_STYLESHEET}


def test_the_xslt_access_control_denies_everything() -> None:
    from runtime.ops_util import NO_ACCESS

    assert NO_ACCESS.options == {
        "read_file": False,
        "write_file": False,
        "create_dir": False,
        "read_network": False,
        "write_network": False,
    }


@pytest.mark.parametrize("tag", ["include", "import", "redefine", "override"])
def test_xsd_cannot_load_other_schemas(tag: str) -> None:
    schema = (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        f'<xs:{tag} namespace="urn:x" schemaLocation="file:///etc/passwd"/></xs:schema>'
    )
    response = failed("validate_xsd", {"xml": "<r/>", "schema": schema})
    assert response["error_code"] == errors.SCHEMA_IMPORT_REFUSED
    assert "root:" not in response["error"]


def test_xsd_network_location_is_refused() -> None:
    schema = (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:import namespace="http://www.w3.org/XML/1998/namespace" '
        'schemaLocation="http://www.w3.org/2001/xml.xsd"/></xs:schema>'
    )
    response = failed("validate_xsd", {"xml": "<r/>", "schema": schema})
    assert response["error_code"] == errors.SCHEMA_IMPORT_REFUSED


def test_the_parser_answers_every_load_with_nothing() -> None:
    """Defence in depth: even a schema load that got past the check gets an empty document."""
    schema = etree.fromstring(
        b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        b'<xs:include schemaLocation="file:///etc/passwd"/></xs:schema>',
        safe.make_parser(),
    )
    with pytest.raises(etree.XMLSchemaParseError):
        etree.XMLSchema(schema)


def test_a_dtd_in_the_schema_is_refused() -> None:
    schema = (
        '<!DOCTYPE s [<!ENTITY e "x">]><xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>'
    )
    response = failed("validate_xsd", {"xml": "<r/>", "schema": schema})
    assert response["error_code"] == errors.DTD_FORBIDDEN


def test_nesting_deeper_than_the_limit_is_refused() -> None:
    depth = limits.MAX_DEPTH + 5
    document = "<a>" * depth + "</a>" * depth
    response = failed("parse_xml", {"xml": document})
    assert response["error_code"] in {errors.LIMIT_EXCEEDED, errors.INVALID_XML}


def test_the_depth_limit_is_ours_too() -> None:
    root = etree.Element("a")
    node = root
    for _ in range(limits.MAX_DEPTH + 1):
        node = etree.SubElement(node, "a")
    with pytest.raises(ExtensionError) as caught:
        safe.check_shape(root)
    assert caught.value.code == errors.LIMIT_EXCEEDED


def test_streaming_depth_limit() -> None:
    depth = limits.MAX_DEPTH + 5
    document = ("<a>" * depth + "</a>" * depth).encode()
    response = failed(
        "parse_xml_stream", {"record_element": "b"}, files={"xml_file": ("f", document)}
    )
    assert response["error_code"] in {errors.LIMIT_EXCEEDED, errors.INVALID_XML}


def test_the_element_count_is_limited() -> None:
    root = etree.fromstring(b"<r>" + b"<i/>" * 50 + b"</r>")
    with pytest.raises(ExtensionError) as caught:
        safe.check_shape(root, max_elements=10)
    assert caught.value.code == errors.LIMIT_EXCEEDED


def test_a_document_larger_than_the_limit_is_refused() -> None:
    with pytest.raises(ExtensionError) as caught:
        safe.parse_document(b"<r/>" + b" " * 100, max_bytes=50)
    assert caught.value.code == errors.LIMIT_EXCEEDED


def test_a_file_larger_than_the_limit_is_refused_while_reading(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from runtime import files as files_module

    def streamer(payload, *, artifact_id, binding_key, timeout_seconds):  # type: ignore[no-untyped-def]
        yield b"<r>" + b"x" * 2000 + b"</r>"

    with pytest.raises(ExtensionError) as caught:
        files_module.download(
            {
                "artifacts": {
                    "inputs": [
                        {
                            "artifact_id": "a",
                            "artifact_handle": "artifact:a",
                            "binding_key": "xml_file",
                        }
                    ]
                }
            },
            "artifact:a",
            binding_key="xml_file",
            label="The XML",
            target_path=str(tmp_path / "in"),
            max_bytes=1000,
            streamer=streamer,
        )
    assert caught.value.code == errors.LIMIT_EXCEEDED
    assert not (tmp_path / "in").exists()


def test_the_work_is_stopped_at_its_time_limit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    style = STYLE.format(
        '<xsl:template match="/"><xsl:call-template name="f"><xsl:with-param name="n" '
        'select="40"/></xsl:call-template></xsl:template>'
        '<xsl:template name="f"><xsl:param name="n"/><xsl:if test="$n &gt; 0">'
        '<xsl:call-template name="f"><xsl:with-param name="n" select="$n - 1"/>'
        '</xsl:call-template><xsl:call-template name="f"><xsl:with-param name="n" '
        'select="$n - 1"/></xsl:call-template></xsl:if></xsl:template>'
    )
    (tmp_path / "x.xml").write_text("<r/>")
    (tmp_path / "s.xsl").write_text(style)
    task = {
        "operation_id": "transform_xslt",
        "values": {"parameters": None},
        "sources": {
            "xml": {"label": "The XML", "path": str(tmp_path / "x.xml"), "from_text": True},
            "stylesheet": {
                "label": "The stylesheet",
                "path": str(tmp_path / "s.xsl"),
                "from_text": True,
            },
        },
        "out_path": str(tmp_path / "out"),
    }
    started = time.monotonic()
    with pytest.raises(ExtensionError) as caught:
        sandbox.run_worker(task, seconds=3, workdir=str(tmp_path))
    assert caught.value.code == errors.TIMEOUT
    assert time.monotonic() - started < 15


def test_a_step_out_of_time_answers_timeout() -> None:
    response = failed("parse_xml", {"xml": "<r/>"}, step_seconds=limits.STEP_MARGIN_SECONDS + 0.5)
    assert response["error_code"] == errors.TIMEOUT


def test_parse_errors_give_line_and_column_and_only_a_short_fragment() -> None:
    secret = "S3cr3tValue" * 20
    document = f"<root>\n  <{secret}>text</other>\n</root>"
    response = failed("parse_xml", {"xml": document})
    assert response["error_code"] == errors.INVALID_XML
    assert "line 2" in response["error"]
    assert "column" in response["error"]
    assert secret not in response["error"]
    for word in response["error"].split():
        assert len(word) <= errors.MAX_FRAGMENT + 2


def test_check_xml_gives_line_and_column() -> None:
    result = ok("check_xml", {"xml": "<a>\n<b></a>"})
    assert result["well_formed"] is False
    assert result["errors"][0]["line"] == 2
    assert isinstance(result["errors"][0]["column"], int)


def test_xsd_errors_quote_no_more_than_a_fragment() -> None:
    schema = (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r" '
        'type="xs:integer"/></xs:schema>'
    )
    long_value = "N" * 500
    result = ok("validate_xsd", {"xml": f"<r>{long_value}</r>", "schema": schema})
    assert result["valid"] is False
    assert long_value not in str(result["errors"])


def test_no_module_uses_the_standard_library_xml_parsers() -> None:
    from support import BUNDLE

    banned = ("xml.etree", "xml.dom", "xml.sax", "xml.parsers", "xmlrpc")
    for path in [*(BUNDLE / "runtime").glob("*.py"), BUNDLE / "main.py", BUNDLE / "health.py"]:
        text = path.read_text(encoding="utf-8")
        for name in banned:
            assert f"import {name}" not in text and f"from {name}" not in text, (path, name)


def test_xpath_has_no_extension_functions() -> None:
    response = failed(
        "select_xpath",
        {
            "xml": "<r/>",
            "xpath": "re:test('a', 'a')",
            "namespaces": {"re": "http://exslt.org/regular-expressions"},
        },
    )
    assert response["error_code"] == errors.INVALID_XPATH


def test_an_edit_fragment_with_a_dtd_is_refused() -> None:
    response = failed(
        "edit_xml",
        {
            "xml": "<r/>",
            "changes": [
                {"action": "insert", "xpath": "/r", "xml": '<!DOCTYPE x [<!ENTITY e "x">]><x/>'}
            ],
        },
    )
    assert response["error_code"] == errors.INVALID_STRUCTURE


def test_a_zip_bomb_spreadsheet_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", b"<w/>")
        archive.writestr("xl/worksheets/sheet1.xml", b" " * (20 * limits.MiB))
    response = failed("xlsx_to_xml", files={"xlsx_file": ("x", buffer.getvalue())})
    assert response["error_code"] == errors.LIMIT_EXCEEDED


def test_writing_never_follows_a_test_mode_switch() -> None:
    """These steps have no external effects: a test run is an ordinary run."""
    real, real_body = ok_with_file("format_xml", {"xml": "<r><a/></r>"})
    test, test_body = ok_with_file("format_xml", {"xml": "<r><a/></r>"}, test_mode=True)
    assert real_body == test_body
    assert real["sha256"] == test["sha256"]
