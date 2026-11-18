"""Validate XML against XSD with a schema made of several files."""

from __future__ import annotations

import io
import zipfile

import pytest
from support import failed, ok

from runtime import errors, limits

HEAD = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"'
TYPES = (
    f'{HEAD}><xs:simpleType name="Price"><xs:restriction base="xs:decimal">'
    '<xs:minInclusive value="0"/></xs:restriction></xs:simpleType></xs:schema>'
)
UNITS = (
    f'{HEAD} targetNamespace="urn:units" xmlns="urn:units" elementFormDefault="qualified">'
    '<xs:element name="unit" type="xs:string"/></xs:schema>'
)


def _main(*references: str) -> str:
    return (
        f'{HEAD} xmlns:u="urn:units">'
        + "".join(references)
        + '<xs:element name="item"><xs:complexType><xs:sequence>'
        '<xs:element name="price" type="Price"/>'
        "</xs:sequence></xs:complexType></xs:element></xs:schema>"
    )


INCLUDE_TYPES = '<xs:include schemaLocation="types.xsd"/>'
GOOD = "<item><price>1.5</price></item>"
BAD = "<item><price>-1</price></item>"


def _zip(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def test_include_resolves_to_a_given_file_by_its_own_name() -> None:
    files = {"schema_files": [("t", TYPES.encode(), "types.xsd")]}
    assert (
        ok("validate_xsd", {"xml": GOOD, "schema": _main(INCLUDE_TYPES)}, files=files)["valid"]
        is True
    )
    result = ok("validate_xsd", {"xml": BAD, "schema": _main(INCLUDE_TYPES)}, files=files)
    assert result["valid"] is False


def test_names_can_be_given_for_files_without_a_useful_name() -> None:
    result = ok(
        "validate_xsd",
        {"xml": BAD, "schema": _main(INCLUDE_TYPES), "schema_file_names": ["types.xsd"]},
        files={"schema_files": [("t", TYPES.encode(), "download.bin")]},
    )
    assert result["valid"] is False


def test_import_and_folders_resolve_relative_to_the_referring_file() -> None:
    main = _main(
        '<xs:include schemaLocation="common/types.xsd"/>',
        '<xs:import namespace="urn:units" schemaLocation="common/units.xsd"/>',
    )
    archive = _zip({"common/types.xsd": TYPES, "common/units.xsd": UNITS})
    result = ok(
        "validate_xsd",
        {"xml": GOOD, "schema_name": "feed.xsd"},
        files={"schema_file": ("m", main.encode()), "schema_files": [("z", archive, "set.zip")]},
    )
    assert result["valid"] is True


def test_nested_include_inside_a_folder() -> None:
    inner = f'{HEAD}><xs:include schemaLocation="price.xsd"/></xs:schema>'
    archive = _zip({"lib/all.xsd": inner, "lib/price.xsd": TYPES})
    result = ok(
        "validate_xsd",
        {"xml": BAD, "schema": _main('<xs:include schemaLocation="lib/all.xsd"/>')},
        files={"schema_files": [("z", archive)]},
    )
    assert result["valid"] is False


def test_redefine_resolves_among_the_given_files() -> None:
    main = (
        f'{HEAD}><xs:redefine schemaLocation="types.xsd"><xs:simpleType name="Price">'
        '<xs:restriction base="Price"><xs:maxInclusive value="10"/></xs:restriction>'
        "</xs:simpleType></xs:redefine>"
        '<xs:element name="price" type="Price"/></xs:schema>'
    )
    result = ok(
        "validate_xsd",
        {"xml": "<price>11</price>", "schema": main},
        files={"schema_files": [("t", TYPES.encode(), "types.xsd")]},
    )
    assert result["valid"] is False


@pytest.mark.parametrize(
    "location",
    [
        "http://example.com/types.xsd",
        "https://www.w3.org/2001/xml.xsd",
        "file:///etc/passwd",
        "ftp://example.com/x.xsd",
        "urn:types",
        "//example.com/types.xsd",
        "/etc/passwd",
        "/types.xsd",
        "../types.xsd",
        "common/../../types.xsd",
        "..\\types.xsd",
        "C:\\schemas\\types.xsd",
        "missing.xsd",
    ],
)
def test_references_outside_the_given_files_are_refused(location: str) -> None:
    main = _main(f'<xs:include schemaLocation="{location}"/>')
    response = failed(
        "validate_xsd",
        {"xml": GOOD, "schema": main},
        files={"schema_files": [("t", TYPES.encode(), "types.xsd")]},
    )
    assert response["error_code"] == errors.SCHEMA_IMPORT_REFUSED
    assert "root:" not in response["error"]


@pytest.mark.parametrize("tag", ["import", "redefine", "override"])
def test_every_reference_kind_is_checked(tag: str) -> None:
    main = f'{HEAD}><xs:{tag} namespace="urn:x" schemaLocation="/etc/passwd"/></xs:schema>'
    response = failed("validate_xsd", {"xml": GOOD, "schema": main})
    assert response["error_code"] == errors.SCHEMA_IMPORT_REFUSED


def test_a_nested_reference_is_checked_too() -> None:
    inner = f'{HEAD}><xs:include schemaLocation="../../etc/passwd"/></xs:schema>'
    response = failed(
        "validate_xsd",
        {"xml": GOOD, "schema": _main('<xs:include schemaLocation="lib/all.xsd"/>')},
        files={"schema_files": [("z", _zip({"lib/all.xsd": inner}))]},
    )
    assert response["error_code"] == errors.SCHEMA_IMPORT_REFUSED


@pytest.mark.parametrize(
    "name", ["../evil.xsd", "/abs.xsd", "a\\b.xsd", "http://x/y.xsd", "dir/../../x.xsd"]
)
def test_bad_file_names_in_a_zip_are_refused(name: str) -> None:
    response = failed(
        "validate_xsd",
        {"xml": GOOD, "schema": _main(INCLUDE_TYPES)},
        files={"schema_files": [("z", _zip({name: TYPES, "types.xsd": TYPES}))]},
    )
    assert response["error_code"] == errors.SCHEMA_IMPORT_REFUSED


@pytest.mark.parametrize("name", ["../types.xsd", "/types.xsd", "http://x/types.xsd"])
def test_bad_given_names_are_refused(name: str) -> None:
    response = failed(
        "validate_xsd",
        {"xml": GOOD, "schema": _main(INCLUDE_TYPES), "schema_file_names": [name]},
        files={"schema_files": [("t", TYPES.encode())]},
    )
    assert response["error_code"] == errors.SCHEMA_IMPORT_REFUSED


def test_a_file_without_a_name_needs_one() -> None:
    response = failed(
        "validate_xsd",
        {"xml": GOOD, "schema": _main(INCLUDE_TYPES)},
        files={"schema_files": [("t", TYPES.encode())]},
    )
    assert response["error_code"] == errors.INVALID_PAYLOAD


def test_two_files_with_one_name_are_refused() -> None:
    response = failed(
        "validate_xsd",
        {"xml": GOOD, "schema": _main(INCLUDE_TYPES)},
        files={
            "schema_files": [("a", TYPES.encode(), "types.xsd"), ("b", TYPES.encode(), "types.xsd")]
        },
    )
    assert response["error_code"] == errors.INVALID_PAYLOAD


def test_a_dtd_in_another_schema_file_is_refused() -> None:
    evil = '<!DOCTYPE s [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + TYPES
    response = failed(
        "validate_xsd",
        {"xml": GOOD, "schema": _main(INCLUDE_TYPES)},
        files={"schema_files": [("t", evil.encode(), "types.xsd")]},
    )
    assert response["error_code"] == errors.DTD_FORBIDDEN


def test_a_zip_bomb_of_schemas_is_refused() -> None:
    archive = _zip({"types.xsd": TYPES, "big.xsd": " " * (20 * limits.MiB)})
    response = failed(
        "validate_xsd",
        {"xml": GOOD, "schema": _main(INCLUDE_TYPES)},
        files={"schema_files": [("z", archive)]},
    )
    assert response["error_code"] == errors.LIMIT_EXCEEDED


def test_the_compiler_cannot_load_anything_outside_the_set(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Defence in depth: even past the check, the loader answers only from the set."""
    from lxml import etree

    from runtime import xsd_set

    (tmp_path / "types.xsd").write_text(TYPES)
    resolver = xsd_set.SetResolver({"main.xsd": b""})
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    parser.resolvers.add(resolver)
    main = _main(f'<xs:include schemaLocation="{tmp_path / "types.xsd"}"/>').encode()
    root = etree.fromstring(main, parser, base_url=xsd_set.VIRTUAL_ROOT + "main.xsd")
    with pytest.raises(etree.XMLSchemaParseError):
        etree.XMLSchema(root.getroottree())
    assert resolver.refused


def test_compiling_twice_in_one_process_gives_the_same_answer() -> None:
    """The set's resolver is the parser's only one: a second resolver would race with it."""
    from runtime import xsd_set

    files = {"main.xsd": _main(INCLUDE_TYPES).encode(), "types.xsd": TYPES.encode()}
    for _ in range(5):
        xsd_set.compile_schema("main.xsd", files)
