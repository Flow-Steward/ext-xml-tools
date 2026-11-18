"""main.py, the dispatcher, the health command, input checks and the output budget."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from support import BUNDLE, failed, ok, payload, run

from runtime import errors, limits


def _main(request: object) -> tuple[int, dict]:  # type: ignore[type-arg]
    data = request if isinstance(request, bytes) else json.dumps(request).encode()
    finished = subprocess.run(
        [sys.executable, "main.py"],
        cwd=BUNDLE,
        input=data,
        capture_output=True,
        timeout=60,
        check=False,
    )
    return finished.returncode, json.loads(finished.stdout)


def test_main_runs_a_step() -> None:
    code, response = _main(payload("select_xpath", {"xml": "<r><a>1</a></r>", "xpath": "//a"}))
    assert code == 0
    assert response["ok"] is True
    assert response["result"]["values"] == ["1"]


def test_main_refuses_bad_json_and_unknown_operations() -> None:
    code, response = _main(b"{not json")
    assert code == 2 and response["error_code"] == errors.INVALID_PAYLOAD
    code, response = _main(payload("drop_database"))
    assert code == 2 and response["error_code"] == errors.UNSUPPORTED_OPERATION


def test_main_refuses_a_bad_host_envelope() -> None:
    request = payload("check_xml", {"xml": "<r/>"})
    del request["runtime_context"]["step_execution_id"]
    request["runtime_context"]["step_id"] = None
    request["runtime_context"]["group_id"] = 5
    code, response = _main(request)
    assert code == 2
    assert response["error_code"] in {errors.INVALID_HOST_ENVELOPE, errors.INVALID_PAYLOAD}


def test_main_keeps_decimals_exact() -> None:
    request = payload("build_xml", {"structure": {"root": "r", "record": "i"}, "pretty": False})
    text = json.dumps(request).replace(
        '"pretty": false', '"pretty": false, "records": [{"p": 1.10}]'
    )
    import main
    from support import Plane

    from runtime.operations import handle_runtime

    plane = Plane()
    response = handle_runtime(main._load_payload(text.encode()), writer=plane.writer)
    assert response["ok"] is True, response
    assert b"<p>1.10</p>" in plane.body


def test_health_reports_the_libraries_and_the_locked_options() -> None:
    finished = subprocess.run(
        [sys.executable, "health.py"], cwd=BUNDLE, capture_output=True, timeout=30, check=False
    )
    report = json.loads(finished.stdout)
    assert finished.returncode == 0
    assert report["status"] == "healthy"
    assert report["declared_operations"] == 19
    assert report["safe_parser"]["resolve_entities"] is False
    assert report["xslt_file_and_network_access"] is False


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        ({"xml": "<r/>", "xpath": "/r", "max_results": 0}, "at least"),
        ({"xml": "<r/>", "xpath": "/r", "max_results": "many"}, "whole number"),
        ({"xml": "<r/>", "xpath": "/r", "return": "everything"}, "one of"),
        ({"xml": "<r/>", "xpath": "/r", "namespaces": ["x"]}, "object"),
        ({"xml": "<r/>", "xpath": "x" * 5000}, "longer"),
        ({"xml": 5, "xpath": "/r"}, "text"),
    ],
)
def test_inputs_are_checked(inputs: dict, message: str) -> None:  # type: ignore[type-arg]
    response = failed("select_xpath", inputs)
    assert response["error_code"] == errors.INVALID_PAYLOAD
    assert message in response["error"]


def test_structured_inputs_may_come_as_json_text() -> None:
    result = ok(
        "select_xpath",
        {"xml": '<r xmlns="urn:a"><a>1</a></r>', "xpath": "//p:a", "namespaces": '{"p": "urn:a"}'},
    )
    assert result["values"] == ["1"]


def test_a_result_too_large_for_the_output_is_refused_with_advice() -> None:
    items = "".join(f"<i>{'x' * 100}{n}</i>" for n in range(60_000))
    response = failed("parse_xml", {"xml": f"<r>{items}</r>", "max_records": 100_000})
    assert response["error_code"] == errors.RESPONSE_TOO_LARGE
    assert "JSONL" in response["error"]


def test_a_large_file_result_is_only_in_the_file() -> None:
    items = "".join(f"<i>{n}</i>" for n in range(60_000))
    response, plane = run("format_xml", {"xml": f"<r>{items}</r>"})
    assert response["ok"] is True
    assert response["result"]["xml"] is None
    assert len(plane.body) > limits.MAX_INLINE_BYTES
    assert "only in the file" in response["result"]["warnings"][0]


def test_every_declared_output_is_present() -> None:
    from runtime import specs

    result = ok("check_xml", {"xml": "<r/>"})
    for output in specs.BY_ID["check_xml"].outputs:
        assert output.name in result


def test_a_test_run_gives_the_same_result() -> None:
    inputs = {"xml": "<r><a>1</a></r>", "xpath": "//a"}
    assert ok("select_xpath", inputs) == ok("select_xpath", inputs, test_mode=True)


def test_the_file_that_was_saved_is_reported() -> None:
    response, plane = run("canonicalize_xml", {"xml": "<r/>"})
    saved = plane.saved[0]
    assert saved["binding_key"] == "result_file"
    assert saved["content_type"] == "application/xml"
    assert response["result"]["file"] == "artifact:out-1"


def test_a_save_failure_is_reported() -> None:
    def writer(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("storage down")

    from runtime.operations import handle_runtime

    response = handle_runtime(payload("format_xml", {"xml": "<r/>"}), writer=writer)
    assert response["ok"] is False
    assert response["error_code"] == errors.ARTIFACT_OUTPUT_UNAVAILABLE
