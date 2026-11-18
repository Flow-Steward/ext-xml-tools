"""Health command for XML Tools (no network, no files)."""

from __future__ import annotations

import json


def check_health() -> dict[str, object]:
    try:
        from lxml import etree
    except ImportError:
        return {
            "extension_id": "flowsteward.xml",
            "ok": False,
            "status": "unhealthy",
            "error_code": "dependency_unavailable",
            "missing_dependencies": ["lxml"],
        }

    from runtime.ops_util import NO_ACCESS
    from runtime.safe import SAFE_PARSER_OPTIONS, parse_document
    from runtime.specs import OPERATION_IDS

    parse_document(b"<health/>")
    return {
        "extension_id": "flowsteward.xml",
        "ok": True,
        "status": "healthy",
        "declared_operations": len(OPERATION_IDS),
        "lxml": ".".join(str(part) for part in etree.LXML_VERSION[:3]),
        "libxml2": ".".join(str(part) for part in etree.LIBXML_VERSION),
        "libxslt": ".".join(str(part) for part in etree.LIBXSLT_VERSION),
        "safe_parser": {key: SAFE_PARSER_OPTIONS[key] for key in sorted(SAFE_PARSER_OPTIONS)},
        "xslt_file_and_network_access": bool(NO_ACCESS.options.get("read_file")),
        "dependency_versions": {"lxml": ".".join(str(p) for p in etree.LXML_VERSION[:3])},
    }


def main() -> int:
    report = check_health()
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0 if report.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
