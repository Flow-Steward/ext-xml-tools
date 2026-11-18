# XML Tools for Flow Steward

`flowsteward.xml` — universal XML steps for Flow Steward workflows: parse, select (XPath 1.0),
build, check, validate (XSD), transform (XSLT), convert (JSON, CSV, XLSX), edit, merge, split,
format, canonicalize and compare. The extension makes no network calls; fetch and deliver files
with `flowsteward.file-transfer` or an HTTP step.

- Steps and their inputs: `runtime/specs.py` (the contracts and step pages are generated from it
  by `python tests/build_contracts.py`).
- Runtime dependency: lxml (wheels for Linux AMD64 and ARM64 in `wheels/`, hash-locked in
  `extension.yaml`).
- Tests: `python -m pytest -q` inside the Flow Steward `fs-audit` container.
