# XML Tools for Flow Steward — parse, validate, transform and convert XML in workflows

<img src="icon.png" width="96" alt="XML Tools for Flow Steward">

[![CI](https://img.shields.io/github/actions/workflow/status/Flow-Steward/ext-xml-tools/ci.yml?branch=main&label=CI)](https://github.com/Flow-Steward/ext-xml-tools/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/Flow-Steward/ext-xml-tools?label=release)](https://github.com/Flow-Steward/ext-xml-tools/releases/latest)

Automate XML with Flow Steward workflows and AI agents: parse, validate and transform XML, convert it to JSON, CSV or Excel and back — without writing code.

This Flow Steward extension brings XML processing to workflow automation: an XML parser with XPath, XSD validation, XSLT transformation and XML to JSON, XML to CSV and XML to Excel conversion. Use it with any product feed, e-invoice, report or data export, together with any integration that receives or sends XML through an API or a file.

## What is this

XML is a common file format for exchanging product feeds, invoices and reports between suppliers, marketplaces and accounting systems. This extension is a set of ready XML steps for the Flow Steward workflow editor, also available as tools for AI agents. It needs no connection and works only on the documents a workflow gives it. Read more about Flow Steward at <https://docs.flowsteward.org>.

## What you can automate

- Turn a supplier's XML product feed into records your shop or ERP steps can import — no manual copying.
- Process a very large XML feed element by element into a JSONL file, without loading it whole.
- Build a marketplace or price-comparison product feed in XML from records of your shop.
- Validate e-invoices and reports against their XSD schema before you send them, with each error's line and column.
- Convert XML exports to Excel or CSV for your accountant, and Excel or CSV files back to XML.
- Change prices or texts in an XML file by XPath, merge several feeds into one or split a huge one into parts.
- Compare two versions of a document or a feed and list exactly what changed.

## Why teams use it

- **Your data stays in Flow Steward.** The extension makes no network calls: it reads and writes only the documents and workflow files of the run.
- **Safe with untrusted files.** DTDs, external entities, XInclude and file or network access from XSLT and XSD are refused, and every step runs under size, depth and time limits.
- **Large files are fine.** Parse large XML to JSONL file, the CSV and Excel conversions and Split XML document read a file element by element, and large results are saved as workflow files.
- **Clear errors.** Check XML and Validate XML against XSD return each problem with its line and column as a result, not as a failure.
- **A test run changes nothing outside.** The steps have no external effects: they only produce results and files for the next steps.

## What's inside

- **Parse XML to records** — XML into JSON records, attributes and repeated elements kept.
- **Parse large XML to JSONL file** — huge files element by element into JSONL.
- **Select with XPath** — values, nodes or records by an XPath 1.0 expression.
- **Build XML from records** — XML from JSON records with elements, attributes, nesting and CDATA.
- **Check XML** — well-formedness check with line and column.
- **Validate XML against XSD** — XSD 1.0 validation, also with a schema made of several files or a ZIP.
- **Transform XML with XSLT** — an XSLT 1.0 stylesheet to XML, HTML or text.
- **Convert XML to JSON** and **Convert JSON to XML** — the whole document, one convention both ways.
- **Convert XML to CSV** and **Convert CSV to XML** — one row per record element.
- **Convert XML to XLSX** and **Convert XLSX to XML** — Excel sheets in and out.
- **Edit XML** — set text or attributes, insert, replace, rename or remove nodes by XPath.
- **Merge XML documents** — records of several documents into one.
- **Split XML document** — a large document into parts of a set number of records, as one ZIP.
- **Format XML** — indent for reading or minify.
- **Canonicalize XML** — C14N 1.0, exclusive C14N or C14N 2.0 with its SHA-256.
- **Compare XML documents** — differences with their paths.

That is 19 steps in total, covering reading, selecting, building, checking, transforming, converting, editing and comparing XML.

## Get started

1. In Flow Steward, open **Extensions → Catalog**, find **XML Tools** and install it. No connection is needed.
2. Get the XML into the workflow: download it with **File Transfer** (`flowsteward.file-transfer`) or an HTTP step, or pass it as text from an earlier step.
3. Add an XML Tools step, for example **Parse XML to records** with the record XPath of your feed, and give it the document as text or as a workflow file.
4. Pass the result to the next steps, and deliver files with File Transfer.

## Requirements

- Flow Steward 0.9.16 or later, on Linux AMD64 or ARM64: the release includes the lxml library for both.
- File Transfer or an HTTP step to fetch and deliver files: the extension itself never connects anywhere.
- Supported standards: XPath 1.0, XSLT 1.0, XML Schema (XSD) 1.0, C14N 1.0, exclusive C14N and C14N 2.0.
- Documents with a DTD are refused; an HTML result of XSLT is shown as plain text.

## FAQ

**How do I convert an XML product feed to JSON or CSV in a workflow?**
Use **Parse XML to records** or **Convert XML to CSV** with the element that holds one product. For very large feeds use **Parse large XML to JSONL file**.

**Can I validate XML against an XSD schema automatically?**
Yes. **Validate XML against XSD** returns valid or invalid and each error with its line and column. A schema made of several files can be given as files or one ZIP.

**Can XML Tools download a feed from a URL?**
No, the extension makes no network calls. Download the feed with File Transfer or an HTTP step and pass the file to XML Tools.

**How do I build an XML feed for a marketplace?**
Collect your products as records and use **Build XML from records** with the structure the marketplace asks for, or **Transform XML with XSLT** if you have a stylesheet.

**How do I convert Excel to XML?**
Use **Convert XLSX to XML**: the first row gives the column names, each further row becomes one record element.

## Links

- Flow Steward documentation: <https://docs.flowsteward.org>
- Flow Steward extension catalog: <https://github.com/Flow-Steward/extensions>
- Issues and support: <https://github.com/Flow-Steward/ext-xml-tools/issues>
- W3C specifications: [XML 1.0](https://www.w3.org/TR/xml/), [XPath 1.0](https://www.w3.org/TR/xpath-10/), [XSLT 1.0](https://www.w3.org/TR/xslt-10/), [XML Schema](https://www.w3.org/TR/xmlschema-1/)
- Changes: [CHANGELOG.md](CHANGELOG.md)

## For developers

Steps and their inputs are defined in `runtime/specs.py`; the contracts and step pages are generated from it:

```bash
python tests/build_contracts.py
```

The runtime dependency lxml ships as wheels for Linux AMD64 and ARM64 in `wheels/`, hash-locked in `extension.yaml`. From a Flow Steward checkout (the checks run in the `fs-audit` container):

```bash
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root "$PWD/workspace/extensions/flowsteward_xml"
./flow-steward extensions test flowsteward.xml --root workspace/extensions
```

XML is an open W3C standard; the icon is drawn for this extension, see [BRANDING.md](BRANDING.md).
