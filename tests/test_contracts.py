"""The manifest, the generated contracts, the step forms, the pages, the icon and the lock."""

from __future__ import annotations

import hashlib
import re
import struct

import build_contracts
import yaml
from support import BUNDLE

from runtime import errors, limits, specs

EXPECTED_OPERATIONS = [
    "parse_xml",
    "parse_xml_stream",
    "select_xpath",
    "build_xml",
    "check_xml",
    "validate_xsd",
    "transform_xslt",
    "xml_to_json",
    "json_to_xml",
    "xml_to_csv",
    "csv_to_xml",
    "xml_to_xlsx",
    "xlsx_to_xml",
    "edit_xml",
    "merge_xml",
    "split_xml",
    "format_xml",
    "canonicalize_xml",
    "compare_xml",
]
VERBS = {
    "Parse",
    "Select",
    "Build",
    "Check",
    "Validate",
    "Transform",
    "Convert",
    "Edit",
    "Merge",
    "Split",
    "Format",
    "Canonicalize",
    "Compare",
}


def _yaml(relative: str) -> dict:  # type: ignore[type-arg]
    return yaml.safe_load((BUNDLE / relative).read_text(encoding="utf-8"))


def test_generated_files_match_the_specs() -> None:
    for relative, text in build_contracts.generated_files().items():
        assert (BUNDLE / relative).read_text(encoding="utf-8") == text, relative


def test_the_operation_ids_are_exactly_these() -> None:
    assert [op.operation_id for op in specs.OPERATIONS] == EXPECTED_OPERATIONS
    manifest = _yaml("contracts/operation_manifest.yaml")
    assert [op["operation_id"] for op in manifest["operations"]] == EXPECTED_OPERATIONS
    actions = _yaml("ui/actions/actions.yaml")
    assert [a["action_id"] for a in actions["actions"]] == EXPECTED_OPERATIONS


def test_names_are_unique_and_start_with_a_verb() -> None:
    names = [op.display_name for op in specs.OPERATIONS]
    assert len(set(names)) == len(names)
    for name in names:
        assert name.split()[0] in VERBS, name


def test_every_step_returns_warnings() -> None:
    for op in _yaml("contracts/operation_manifest.yaml")["operations"]:
        assert "warnings" in [o["name"] for o in op["outputs"]], op["operation_id"]
        assert "result.warnings" in op["result_fields"]


def test_every_step_is_visible_and_declares_the_common_errors() -> None:
    for op in _yaml("contracts/operation_manifest.yaml")["operations"]:
        assert op["workflow_visible"] is True
        for code in errors.COMMON_ERROR_CODES:
            assert code in op["error_codes"]
        assert set(op["error_codes"]) <= errors.SAFE_ERROR_CODES


def test_every_input_has_a_form_field_and_files_use_the_file_picker() -> None:
    manifest = _yaml("contracts/operation_manifest.yaml")
    forms = {f["operation_id"]: f for f in _yaml("contracts/step_ui_manifest.yaml")["forms"]}
    for op in manifest["operations"]:
        fields = {f["name"]: f for f in forms[op["operation_id"]]["fields"]}
        for item in op["inputs"]:
            assert item["name"] in fields, (op["operation_id"], item["name"])
            field = fields[item["name"]]
            if item["value_type"] == "artifact_handle" or item["name"] == "xml_files":
                assert field["widget_type"] == "artifact_picker"
            assert field["label"] and field["help_text"]
        assert set(fields) == {i["name"] for i in op["inputs"]}


def test_every_file_input_and_output_has_its_policy() -> None:
    policies = {
        p["operation_id"]: p for p in _yaml("contracts/artifact_policies.yaml")["artifact_policies"]
    }
    for op in specs.OPERATIONS:
        policy = policies[op.operation_id]
        fields = {i["field"]: i for i in policy.get("inputs", [])}
        for document in op.documents:
            entry = fields[document.file_field]
            assert entry["binding_key"] == document.file_field
            assert entry["required"] is (document.text_field is None)
            assert entry["max_size_env"] == "FS_EXTENSION_ARTIFACT_MAX_BYTES"
        if op.file_output is None:
            assert "outputs" not in policy
        else:
            (output,) = policy["outputs"]
            assert output["binding_key"] == "result_file"
            assert output["content_type"] == op.file_output.content_type
            assert output["filename"] == op.file_output.filename


def test_the_manifest() -> None:
    manifest = _yaml("extension.yaml")
    assert manifest["extension_id"] == "flowsteward.xml"
    assert manifest["version"] == "0.1.0"
    assert manifest["entrypoint"]["timeout_seconds"] == 600 == limits.STEP_TIMEOUT_SECONDS
    assert manifest["egress"] == {}
    assert manifest["external_effects"] == []
    assert "dependencies" not in manifest
    assert manifest["runtime"]["contract_version"] == "extension_host_v2"


def test_the_lock_has_both_architectures_and_the_wheels_match() -> None:
    manifest = _yaml("extension.yaml")
    (requirement,) = manifest["python_requirements"]
    assert requirement["name"] == "lxml"
    wheels = sorted((BUNDLE / "wheels").glob("lxml-*.whl"))
    assert any("x86_64" in w.name for w in wheels)
    assert any("aarch64" in w.name for w in wheels)
    digests = {"sha256:" + hashlib.sha256(w.read_bytes()).hexdigest() for w in wheels}
    assert digests == set(requirement["hashes"])
    assert f"lxml=={requirement['version']}" in (BUNDLE / "requirements.in").read_text()
    assert f"lxml=={requirement['version']}" in (BUNDLE / "requirements-dev.txt").read_text()


def test_pages_are_short_and_have_no_tables() -> None:
    manifest = _yaml("ui/ui_manifest.yaml")
    for ref in manifest["pages"]:
        page = _yaml(f"ui/{ref['ref']}")
        for component in page["components"]:
            body = component.get("body", "")
            assert not re.search(r"^\s*\|", body, re.MULTILINE), (ref, component["component_id"])
            for banned in ("Limits", "не проверено", "not verified", "unverified"):
                assert banned not in body and banned not in component.get("title", "")


def test_the_setup_guide_says_file_transfer_does_the_network() -> None:
    body = " ".join(c["body"] for c in _yaml("ui/pages/setup-guide.yaml")["components"])
    assert "File Transfer" in body
    assert "never connects" in body


def test_the_changelog_names_this_version() -> None:
    version = _yaml("extension.yaml")["version"]
    assert f"## {version} " in (BUNDLE / "CHANGELOG.md").read_text(encoding="utf-8")


def test_the_icon_is_our_own_and_valid() -> None:
    data = (BUNDLE / "icon.png").read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    assert width == height and 32 <= width <= 512
    assert len(data) <= 100 * 1024
    digest = hashlib.sha256(data).hexdigest()
    assert digest in (BUNDLE / "BRANDING.md").read_text(encoding="utf-8")
    import make_icon

    assert make_icon.png(make_icon.pixels()) == data


def test_the_step_pages_list_every_step() -> None:
    text = "".join(
        (BUNDLE / f"ui/pages/{page_id}.yaml").read_text(encoding="utf-8")
        for page_id, *_ in build_contracts.PAGES
    )
    for op in specs.OPERATIONS:
        assert f"### {op.display_name}" in text
        assert f"`{op.operation_id}`" in text
