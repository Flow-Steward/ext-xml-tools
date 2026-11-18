"""Every step of XML Tools: its inputs, outputs, result file and errors.

The single source of truth: ``tests/build_contracts.py`` writes the operation
manifest, the actions, the step forms, the file policies and the step pages from
it, and ``tests/test_contracts.py`` fails when they drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import errors, limits

ENCODINGS: tuple[str, ...] = (
    "UTF-8",
    "UTF-16",
    "ISO-8859-1",
    "ISO-8859-2",
    "ISO-8859-13",
    "ISO-8859-15",
    "windows-1250",
    "windows-1251",
    "windows-1252",
    "windows-1257",
    "US-ASCII",
)


@dataclass(frozen=True)
class Field:
    name: str
    value_type: str
    title: str
    description: str
    required: bool = False
    options: tuple[str, ...] = ()
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    max_length: int | None = None


@dataclass(frozen=True)
class Document:
    """A document given as typed text or as a workflow file."""

    key: str
    label: str
    text_field: str | None
    file_field: str
    kind: str
    description: str
    required: bool = True
    many: bool = False
    streaming: bool = False
    max_bytes: int = limits.MAX_DOCUMENT_BYTES


@dataclass(frozen=True)
class Output:
    name: str
    value_type: str
    description: str
    schema_type: Any = None


@dataclass(frozen=True)
class FileOutput:
    filename: str
    content_type: str
    description: str
    inline_field: str | None = None
    inline_description: str = ""
    inline_json: bool = False
    #: The step names the file and its type itself (ADR 0045 presentation claim).
    presentation: str | None = None


@dataclass(frozen=True)
class Operation:
    operation_id: str
    display_name: str
    group: str
    description: str
    documents: tuple[Document, ...]
    fields: tuple[Field, ...]
    outputs: tuple[Output, ...]
    error_codes: tuple[str, ...]
    file_output: FileOutput | None = None
    example: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


GROUPS: tuple[tuple[str, str], ...] = (
    ("read", "Read"),
    ("select", "Select"),
    ("build", "Build"),
    ("check", "Check"),
    ("transform", "Transform"),
    ("edit", "Edit"),
    ("utility", "Format and compare"),
)

WARNINGS = Output(
    "warnings", "array", "What the step could not do fully or what to check.", "strings"
)


def _xml(
    key: str = "xml",
    label: str = "The XML",
    *,
    streaming: bool = False,
    required: bool = True,
    file_field: str | None = None,
) -> Document:
    return Document(
        key=key,
        label=label,
        text_field=key if key == "xml" else f"{key}_xml",
        file_field=file_field or f"{key}_file",
        kind="xml",
        description=(
            "The XML as text. Give it here or as a file, not both."
            if key == "xml"
            else f"{label} as text. Give it here or as a file, not both."
        ),
        required=required,
        streaming=streaming,
        max_bytes=limits.MAX_STREAM_BYTES if streaming else limits.MAX_DOCUMENT_BYTES,
    )


NAMESPACES = Field(
    "namespaces",
    "object",
    "Namespace prefixes",
    'Prefixes the XPath or element names use, for example {"g": "http://base.google.com/ns/1.0"}. '
    "A prefix here need not match the document's own.",
)
RECORD_ELEMENT = Field(
    "record_element",
    "text",
    "Record element",
    "The repeating element that is one record, for example item, product or g:item (with its "
    "prefix in Namespace prefixes). Without a prefix it matches that name in any namespace.",
    required=True,
    max_length=200,
)
ATTRIBUTE_PREFIX = Field(
    "attribute_prefix",
    "text",
    "Attribute prefix",
    "Put before attribute names in the JSON. Default @.",
    default="@",
    max_length=5,
)
TEXT_KEY = Field(
    "text_key",
    "text",
    "Text key",
    "The JSON key for an element's text when it also has attributes or children. Default #text.",
    default="#text",
    max_length=20,
)
NAMESPACE_MODE = Field(
    "namespace_mode",
    "text",
    "Namespaces in names",
    "keep writes names as the document does (g:price) with their declarations as @xmlns keys; "
    "strip keeps local names only. Default keep.",
    options=("keep", "strip"),
    default="keep",
)
FORCE_LIST = Field(
    "force_list",
    "array",
    "Always lists",
    "Element names that are always a JSON list, even when they occur once.",
)
FLATTEN = Field(
    "flatten",
    "boolean",
    "Flatten",
    "Turn nested values into flat keys such as price.@currency and images.image.0.",
    default=False,
)
JSON_OPTIONS = (ATTRIBUTE_PREFIX, TEXT_KEY, NAMESPACE_MODE, FORCE_LIST)
ENCODING = Field(
    "encoding",
    "text",
    "Encoding",
    "The encoding of the XML written, named in its declaration. Default UTF-8.",
    options=ENCODINGS,
    default="UTF-8",
)
XML_DECLARATION = Field(
    "xml_declaration",
    "boolean",
    "XML declaration",
    'Start with <?xml version="1.0" encoding="…"?>. Default on.',
    default=True,
)
PRETTY = Field(
    "pretty", "boolean", "Indent", "Indent the XML for reading. Default on.", default=True
)
PROLOG = (ENCODING, XML_DECLARATION, PRETTY)
ROOT_ELEMENT = Field(
    "root_element",
    "text",
    "Root element",
    "The root element's name. Default rows.",
    default="rows",
    max_length=200,
)
ROW_ELEMENT = Field(
    "record_element",
    "text",
    "Record element",
    "One element per row. Default row.",
    default="row",
    max_length=200,
)
ATTRIBUTE_COLUMNS = Field(
    "attribute_columns",
    "array",
    "Columns as attributes",
    "Columns written as attributes of the record element instead of child elements.",
)
SKIP_EMPTY = Field(
    "skip_empty",
    "boolean",
    "Skip empty cells",
    "Leave out elements for empty cells. Default off: an empty cell is an empty element.",
    default=False,
)
COLUMNS = Field(
    "columns",
    "array",
    "Columns",
    'Optional: [{"name": "price", "xpath": "g:price"}, …], each XPath relative to the record. '
    "Without it the columns are the record's flattened fields in the order they first occur.",
)

XML_RESULT = FileOutput(
    "result.xml",
    "application/xml",
    "The XML, as a workflow file.",
    inline_field="xml",
    inline_description=f"The XML as text, when it is at most {limits.MAX_INLINE_BYTES // 1024} KiB.",
)
FILE_FACTS = (
    Output("size_bytes", "integer", "The result file's size."),
    Output("sha256", "text", "The result file's SHA-256."),
)


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "parse_xml",
        "Parse XML to records",
        "read",
        "Turns an XML document into JSON records: each element the record XPath selects becomes "
        "one record, with attributes as @name, repeated elements as lists and mixed text kept in "
        "order. For files too large for the step output use Parse large XML to JSONL file.",
        (_xml(),),
        (
            Field(
                "record_xpath",
                "text",
                "Record XPath",
                "XPath 1.0 selecting the record elements. Default /*/* (every child of the root).",
                default="/*/*",
                max_length=2000,
            ),
            NAMESPACES,
            FLATTEN,
            Field(
                "max_records",
                "integer",
                "Max records",
                "Return at most this many records. Default 10000.",
                default=10000,
                minimum=1,
                maximum=limits.MAX_INLINE_RECORDS,
            ),
            *JSON_OPTIONS,
        ),
        (
            Output("records", "array", "The records as JSON objects.", "objects"),
            Output("count", "integer", "Records returned."),
            Output("total", "integer", "Records the XPath selected."),
            Output("truncated", "boolean", "True when more records matched than were returned."),
            Output("root_element", "text", "The root element's name."),
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.INVALID_XPATH)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE,),
        example="A supplier feed with `product` elements under the root gives one record per "
        "product.",
    ),
    Operation(
        "parse_xml_stream",
        "Parse large XML to JSONL file",
        "read",
        "Reads a large XML file element by element and writes one JSON record per record element "
        "to a JSONL file, without loading the whole file. Returns the count and the first records "
        "as a sample.",
        (_xml(streaming=True),),
        (
            RECORD_ELEMENT,
            NAMESPACES,
            FLATTEN,
            Field(
                "max_records",
                "integer",
                "Max records",
                "Stop after this many records. Empty: all.",
                minimum=1,
            ),
            *JSON_OPTIONS,
        ),
        (
            Output("record_count", "integer", "Records written."),
            Output("sample", "array", "The first 5 records.", "objects"),
            Output("root_element", "text", "The root element's name."),
            Output("encoding", "text", "The document's encoding."),
            Output("truncated", "boolean", "True when Max records stopped the reading."),
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.INVALID_NAME)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE, errors.ARTIFACT_OUTPUT_UNAVAILABLE),
        file_output=FileOutput(
            "records.jsonl",
            "application/x-ndjson",
            "One JSON record per line, as a workflow file.",
        ),
    ),
    Operation(
        "select_xpath",
        "Select with XPath",
        "select",
        "Evaluates an XPath 1.0 expression. Returns values (the text of each node, or the "
        "number, text or boolean the expression gives), the matching nodes as XML, or the "
        "matching elements as JSON records.",
        (_xml(),),
        (
            Field(
                "xpath",
                "text",
                "XPath",
                "The XPath 1.0 expression, for example //item[price > 10]/title or count(//item).",
                required=True,
                max_length=4000,
            ),
            NAMESPACES,
            Field(
                "variables",
                "object",
                "Variables",
                'Values for $name variables in the XPath, for example {"sku": "A-1"}.',
            ),
            Field(
                "return",
                "text",
                "Return",
                "values (default), nodes (each as XML) or records (each element as JSON).",
                options=("values", "nodes", "records"),
                default="values",
            ),
            Field(
                "max_results",
                "integer",
                "Max results",
                "Return at most this many. Default 1000.",
                default=1000,
                minimum=1,
                maximum=limits.MAX_SELECT_RESULTS,
            ),
            *JSON_OPTIONS,
        ),
        (
            Output("result_type", "text", "nodeset, string, number or boolean."),
            Output("values", "array", "The values, nodes or records.", "any_items"),
            Output("first", "json", "The first value, or null.", "any"),
            Output("count", "integer", "How many the expression gave."),
            Output("truncated", "boolean", "True when more matched than were returned."),
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.INVALID_XPATH)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE,),
    ),
    Operation(
        "build_xml",
        "Build XML from records",
        "build",
        "Builds an XML document from JSON records and a structure: root, optional wrapper "
        "elements, one record element per record, and for each field an element or attribute, "
        "nested elements, repeated elements and CDATA. Text is escaped; characters XML 1.0 does "
        "not allow stop the step and name the record and field.",
        (
            Document(
                "records",
                "The records",
                "records",
                "records_file",
                "records",
                "The records: a JSON list of objects. Give them here or as a file (JSON list or "
                "JSONL), not both.",
            ),
        ),
        (
            Field(
                "structure",
                "object",
                "Structure",
                'For example {"root": "products", "record": "product", "fields": [{"attribute": '
                '"id", "value": "id"}, {"element": "title", "value": "title", "cdata": true}]}. '
                "See the XML Tools page for every key. Without fields each record's keys become "
                "elements.",
                required=True,
            ),
            *PROLOG,
        ),
        (
            Output("record_count", "integer", "Records written."),
            *FILE_FACTS,
            WARNINGS,
        ),
        (errors.INVALID_JSON, errors.INVALID_STRUCTURE, errors.INVALID_NAME)
        + (errors.INVALID_XML_CHARACTER, errors.ARTIFACT_INPUT_UNAVAILABLE)
        + (errors.ARTIFACT_OUTPUT_UNAVAILABLE,),
        file_output=XML_RESULT,
    ),
    Operation(
        "check_xml",
        "Check XML",
        "check",
        "Checks that a document is well-formed XML and allowed here (no DTD). A problem is "
        "returned as a result with its line and column, not as a step failure.",
        (_xml(),),
        (),
        (
            Output("well_formed", "boolean", "True when the document is well-formed and allowed."),
            Output("errors", "array", "Problems: line, column, code and message.", "objects"),
            Output("root_element", "text", "The root element's name."),
            Output("root_namespace", "text", "The root element's namespace URI."),
            Output("declared_encoding", "text", "The encoding the declaration names."),
            Output("xml_version", "text", "The version the declaration names."),
            Output("element_count", "integer", "Elements in the document."),
            Output("max_depth", "integer", "The deepest nesting."),
            WARNINGS,
        ),
        (errors.ARTIFACT_INPUT_UNAVAILABLE,),
    ),
    Operation(
        "validate_xsd",
        "Validate XML against XSD",
        "check",
        "Validates a document against an XML Schema (XSD 1.0) given as text or a file. Returns "
        "valid and each error with its line and column. The schema must be one file: "
        "xs:include, xs:import, xs:redefine and xs:override with a schemaLocation are refused, "
        "nothing is loaded from the network or the disk.",
        (
            _xml(),
            Document(
                "schema",
                "The schema",
                "schema",
                "schema_file",
                "xml",
                "The XSD as text. Give it here or as a file, not both.",
                max_bytes=limits.MAX_AUXILIARY_BYTES,
            ),
        ),
        (
            Field(
                "max_errors",
                "integer",
                "Max errors",
                "List at most this many errors. Default 100.",
                default=100,
                minimum=1,
                maximum=1000,
            ),
        ),
        (
            Output("valid", "boolean", "True when the document matches the schema."),
            Output("error_count", "integer", "Errors found."),
            Output("errors", "array", "The errors: line, column and message.", "objects"),
            WARNINGS,
        ),
        (errors.INVALID_SCHEMA, errors.SCHEMA_IMPORT_REFUSED, errors.DTD_FORBIDDEN)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE,),
    ),
    Operation(
        "transform_xslt",
        "Transform XML with XSLT",
        "transform",
        "Applies an XSLT 1.0 stylesheet. The stylesheet cannot read or write files, reach the "
        "network or call extensions: document(), xsl:import, xsl:include and exsl:document are "
        "refused. The file follows xsl:output method: xml gives result.xml, html result.html, "
        "text result.txt.",
        (
            _xml(),
            Document(
                "stylesheet",
                "The stylesheet",
                "stylesheet",
                "stylesheet_file",
                "xml",
                "The XSLT stylesheet as text. Give it here or as a file, not both.",
                max_bytes=limits.MAX_AUXILIARY_BYTES,
            ),
        ),
        (
            Field(
                "parameters",
                "object",
                "Parameters",
                'Values for the stylesheet\'s xsl:param, as text, for example {"currency": "EUR"}.',
            ),
        ),
        (
            Output("messages", "array", "Texts of xsl:message.", "strings"),
            Output("output_method", "text", "xml, html or text, as xsl:output method says."),
            Output("file_name", "text", "result.xml, result.html or result.txt."),
            Output("content_type", "text", "The file's type: application/xml or text/plain."),
            *FILE_FACTS,
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.INVALID_STYLESHEET)
        + (errors.TRANSFORM_FAILED, errors.ARTIFACT_INPUT_UNAVAILABLE)
        + (errors.ARTIFACT_OUTPUT_UNAVAILABLE,),
        file_output=FileOutput(
            "result.xml",
            "application/xml",
            "The transformation's output, as a workflow file.",
            inline_field="output",
            inline_description="The output as text, when it is at most "
            f"{limits.MAX_INLINE_BYTES // 1024} KiB.",
            presentation="file_presentation",
        ),
    ),
    Operation(
        "xml_to_json",
        "Convert XML to JSON",
        "transform",
        "Converts a whole XML document to JSON by the convention of Parse XML to records: "
        "attributes as @name, text as #text, repeated elements as lists.",
        (_xml(),),
        JSON_OPTIONS,
        (*FILE_FACTS, WARNINGS),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.ARTIFACT_INPUT_UNAVAILABLE)
        + (errors.ARTIFACT_OUTPUT_UNAVAILABLE,),
        file_output=FileOutput(
            "result.json",
            "application/json",
            "The JSON, as a workflow file.",
            inline_field="data",
            inline_description="The JSON value, when the file is at most "
            f"{limits.MAX_INLINE_BYTES // 1024} KiB.",
            inline_json=True,
        ),
    ),
    Operation(
        "json_to_xml",
        "Convert JSON to XML",
        "transform",
        "Converts JSON to XML by the same convention: keys become elements, @name keys "
        "attributes, #text text, #cdata CDATA, lists repeated elements.",
        (
            Document(
                "data",
                "The JSON",
                "data",
                "json_file",
                "json",
                "The JSON value. Give it here or as a file, not both.",
            ),
        ),
        (
            Field(
                "root_element",
                "text",
                "Root element",
                "Needed when the JSON is not one object with one key (that key is then the root).",
                max_length=200,
            ),
            Field(
                "item_element",
                "text",
                "List item element",
                "The element for each item of a top-level list. Default item.",
                default="item",
                max_length=200,
            ),
            ATTRIBUTE_PREFIX,
            TEXT_KEY,
            *PROLOG,
        ),
        (*FILE_FACTS, WARNINGS),
        (errors.INVALID_JSON, errors.INVALID_STRUCTURE, errors.INVALID_NAME)
        + (errors.INVALID_XML_CHARACTER, errors.ARTIFACT_INPUT_UNAVAILABLE)
        + (errors.ARTIFACT_OUTPUT_UNAVAILABLE,),
        file_output=XML_RESULT,
    ),
    Operation(
        "xml_to_csv",
        "Convert XML to CSV",
        "transform",
        "Writes one CSV row per record element, reading the file element by element. Columns are "
        "given as XPaths or taken from the records' fields.",
        (_xml(streaming=True),),
        (
            RECORD_ELEMENT,
            NAMESPACES,
            COLUMNS,
            Field(
                "delimiter",
                "text",
                "Delimiter",
                "comma (default), semicolon, tab or pipe.",
                options=("comma", "semicolon", "tab", "pipe"),
                default="comma",
            ),
            Field(
                "header",
                "boolean",
                "Header row",
                "Write the column names first. Default on.",
                default=True,
            ),
            Field(
                "excel_bom",
                "boolean",
                "Excel BOM",
                "Start the file with a UTF-8 byte order mark so Excel reads letters like ą, č "
                "correctly. Default off.",
                default=False,
            ),
        ),
        (
            Output("record_count", "integer", "Rows written (without the header)."),
            Output("columns", "array", "The column names.", "strings"),
            *FILE_FACTS,
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.INVALID_XPATH, errors.INVALID_NAME)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE, errors.ARTIFACT_OUTPUT_UNAVAILABLE),
        file_output=FileOutput(
            "result.csv",
            "text/csv",
            "The CSV (UTF-8), as a workflow file.",
            inline_field="csv",
            inline_description="The CSV as text, when it is at most "
            f"{limits.MAX_INLINE_BYTES // 1024} KiB.",
        ),
    ),
    Operation(
        "csv_to_xml",
        "Convert CSV to XML",
        "transform",
        "Turns a CSV with a header row into XML: one record element per row, one child element "
        "(or attribute) per column. Column names that are not valid XML names are adjusted and "
        "the warnings say how.",
        (
            Document(
                "csv",
                "The CSV",
                "csv",
                "csv_file",
                "csv",
                "The CSV as text. Give it here or as a file, not both.",
            ),
        ),
        (
            Field(
                "delimiter",
                "text",
                "Delimiter",
                "comma (default), semicolon, tab or pipe.",
                options=("comma", "semicolon", "tab", "pipe"),
                default="comma",
            ),
            Field(
                "csv_encoding",
                "text",
                "CSV file encoding",
                "The file's encoding. Default UTF-8 (a byte order mark is removed).",
                options=ENCODINGS,
                default="UTF-8",
            ),
            ROOT_ELEMENT,
            ROW_ELEMENT,
            ATTRIBUTE_COLUMNS,
            SKIP_EMPTY,
            *PROLOG,
        ),
        (
            Output("record_count", "integer", "Rows written."),
            Output("columns", "array", "Each column and its element name.", "objects"),
            *FILE_FACTS,
            WARNINGS,
        ),
        (errors.INVALID_CSV, errors.INVALID_NAME, errors.INVALID_XML_CHARACTER)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE, errors.ARTIFACT_OUTPUT_UNAVAILABLE),
        file_output=XML_RESULT,
    ),
    Operation(
        "xml_to_xlsx",
        "Convert XML to XLSX",
        "transform",
        "Writes one spreadsheet row per record element to an Excel file (one sheet, a header "
        "row, every cell as text), reading the XML element by element.",
        (_xml(streaming=True),),
        (
            RECORD_ELEMENT,
            NAMESPACES,
            COLUMNS,
            Field(
                "sheet_name",
                "text",
                "Sheet name",
                "Default Sheet1.",
                default="Sheet1",
                max_length=31,
            ),
        ),
        (
            Output("record_count", "integer", "Rows written (without the header)."),
            Output("columns", "array", "The column names.", "strings"),
            *FILE_FACTS,
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.INVALID_XPATH, errors.INVALID_NAME)
        + (errors.INVALID_XML_CHARACTER, errors.ARTIFACT_INPUT_UNAVAILABLE)
        + (errors.ARTIFACT_OUTPUT_UNAVAILABLE,),
        file_output=FileOutput(
            "result.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "The spreadsheet, as a workflow file.",
        ),
    ),
    Operation(
        "xlsx_to_xml",
        "Convert XLSX to XML",
        "transform",
        "Turns one sheet of an Excel file (first row = column names) into XML: one record "
        "element per row. Values are taken as stored: dates are Excel day numbers, formulas give "
        "their last saved result.",
        (
            Document(
                "xlsx",
                "The spreadsheet",
                None,
                "xlsx_file",
                "xlsx",
                "",
            ),
        ),
        (
            Field(
                "sheet",
                "text",
                "Sheet",
                "The sheet's name. Default the first sheet.",
                max_length=31,
            ),
            ROOT_ELEMENT,
            ROW_ELEMENT,
            ATTRIBUTE_COLUMNS,
            SKIP_EMPTY,
            *PROLOG,
        ),
        (
            Output("sheet", "text", "The sheet read."),
            Output("record_count", "integer", "Rows written."),
            Output("columns", "array", "Each column and its element name.", "objects"),
            *FILE_FACTS,
            WARNINGS,
        ),
        (errors.INVALID_XLSX, errors.INVALID_NAME, errors.INVALID_XML_CHARACTER)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE, errors.ARTIFACT_OUTPUT_UNAVAILABLE),
        file_output=XML_RESULT,
    ),
    Operation(
        "edit_xml",
        "Edit XML",
        "edit",
        "Applies changes in order, each to the nodes an XPath selects: set text, set or remove "
        "an attribute, insert an XML fragment, replace, rename or remove. By default a change "
        "that selects nothing stops the step.",
        (_xml(),),
        (
            Field(
                "changes",
                "array",
                "Changes",
                'For example [{"action": "set_text", "xpath": "//item[@id=\'7\']/price", '
                '"value": "9.99"}, {"action": "remove", "xpath": "//item[stock=0]"}]. Actions: '
                "set_text, set_attribute, remove_attribute, insert (append, prepend, before or "
                "after), replace, rename, remove.",
                required=True,
            ),
            NAMESPACES,
            Field(
                "require_match",
                "boolean",
                "Every change must match",
                "Stop when a change selects nothing. Default on.",
                default=True,
            ),
        ),
        (
            Output("applied", "array", "Each change and how many nodes it changed.", "objects"),
            *FILE_FACTS,
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.INVALID_XPATH, errors.NO_MATCH)
        + (errors.INVALID_NAME, errors.INVALID_XML_CHARACTER, errors.INVALID_STRUCTURE)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE, errors.ARTIFACT_OUTPUT_UNAVAILABLE),
        file_output=XML_RESULT,
    ),
    Operation(
        "merge_xml",
        "Merge XML documents",
        "edit",
        "Joins documents into the first one: the records the XPath selects in each further "
        "document are added after the last record of the first (for example every item of "
        "several feeds into one rss/channel).",
        (
            Document(
                "xml",
                "The documents",
                "xml_list",
                "xml_files",
                "xml",
                "The documents as texts, in order. Files come first, then these.",
                many=True,
            ),
        ),
        (
            Field(
                "record_xpath",
                "text",
                "Record XPath",
                "XPath 1.0 selecting the records in each document. Default /*/*.",
                default="/*/*",
                max_length=2000,
            ),
            NAMESPACES,
        ),
        (
            Output("document_count", "integer", "Documents merged."),
            Output("record_count", "integer", "Records in the result."),
            *FILE_FACTS,
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.INVALID_XPATH)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE, errors.ARTIFACT_OUTPUT_UNAVAILABLE),
        file_output=XML_RESULT,
    ),
    Operation(
        "split_xml",
        "Split XML document",
        "edit",
        "Splits a large document into parts of a set number of records, reading it element by "
        "element. Each part keeps the elements around the records (for example rss and "
        "channel) and, if wanted, the elements before the first record. The parts come as one "
        "ZIP file.",
        (_xml(streaming=True),),
        (
            RECORD_ELEMENT,
            NAMESPACES,
            Field(
                "records_per_part",
                "integer",
                "Records per part",
                "How many records each part holds.",
                required=True,
                minimum=1,
                maximum=1_000_000,
            ),
            Field(
                "copy_header",
                "boolean",
                "Copy header",
                "Copy the elements before the first record (such as a channel's title) into "
                "every part. Default on.",
                default=True,
            ),
        ),
        (
            Output("part_count", "integer", "Parts written."),
            Output("record_count", "integer", "Records in all parts."),
            Output("parts", "array", "Each part's file name and record count.", "objects"),
            *FILE_FACTS,
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.INVALID_NAME)
        + (errors.ARTIFACT_INPUT_UNAVAILABLE, errors.ARTIFACT_OUTPUT_UNAVAILABLE),
        file_output=FileOutput(
            "parts.zip", "application/zip", "The parts part-00001.xml, … in a ZIP file."
        ),
    ),
    Operation(
        "format_xml",
        "Format XML",
        "utility",
        "Indents a document for reading, or minifies it by removing whitespace between elements. "
        'Text with content is kept as it is; xml:space="preserve" is respected when minifying.',
        (_xml(),),
        (
            Field(
                "mode",
                "text",
                "Mode",
                "pretty (default) or minify.",
                options=("pretty", "minify"),
                default="pretty",
            ),
            Field(
                "indent",
                "integer",
                "Indent",
                "Spaces per level. Default 2.",
                default=2,
                minimum=0,
                maximum=8,
            ),
            Field("remove_comments", "boolean", "Remove comments", "Default off.", default=False),
            Field(
                "encoding",
                "text",
                "Encoding",
                "Re-encode to this encoding. Default: keep the document's.",
                options=ENCODINGS,
            ),
            XML_DECLARATION,
        ),
        (*FILE_FACTS, WARNINGS),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.ARTIFACT_INPUT_UNAVAILABLE)
        + (errors.ARTIFACT_OUTPUT_UNAVAILABLE,),
        file_output=XML_RESULT,
    ),
    Operation(
        "canonicalize_xml",
        "Canonicalize XML",
        "utility",
        "Writes the canonical form of a document (C14N 1.0, exclusive C14N or C14N 2.0) and its "
        "SHA-256, so two documents with the same content compare equal byte for byte.",
        (_xml(),),
        (
            Field(
                "method",
                "text",
                "Method",
                "c14n (default, inclusive 1.0), c14n_exclusive or c14n2.",
                options=("c14n", "c14n_exclusive", "c14n2"),
                default="c14n",
            ),
            Field("with_comments", "boolean", "With comments", "Default off.", default=False),
            Field(
                "inclusive_prefixes",
                "array",
                "Inclusive prefixes",
                "For c14n_exclusive: namespace prefixes to keep as in inclusive C14N.",
            ),
        ),
        (*FILE_FACTS, WARNINGS),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.ARTIFACT_INPUT_UNAVAILABLE)
        + (errors.ARTIFACT_OUTPUT_UNAVAILABLE,),
        file_output=XML_RESULT,
    ),
    Operation(
        "compare_xml",
        "Compare XML documents",
        "utility",
        "Compares two documents element by element and lists the differences with their paths: "
        "names, attributes, text and missing or extra elements. Attribute order never counts.",
        (_xml("left", "The first document"), _xml("right", "The second document")),
        (
            Field(
                "ignore_whitespace",
                "boolean",
                "Ignore whitespace",
                "Compare text with surrounding whitespace removed and runs of spaces as one. "
                "Default on.",
                default=True,
            ),
            Field("ignore_comments", "boolean", "Ignore comments", "Default on.", default=True),
            Field(
                "ignore_prefixes",
                "boolean",
                "Ignore prefixes",
                "Compare names by namespace URI, not by prefix. Default on.",
                default=True,
            ),
            Field(
                "max_differences",
                "integer",
                "Max differences",
                "List at most this many. Default 100.",
                default=100,
                minimum=1,
                maximum=limits.MAX_DIFFERENCES,
            ),
        ),
        (
            Output("equal", "boolean", "True when no difference was found."),
            Output("difference_count", "integer", "Differences found."),
            Output(
                "differences", "array", "Each difference: path, kind, first, second.", "objects"
            ),
            Output("truncated", "boolean", "True when more differences exist than are listed."),
            WARNINGS,
        ),
        (errors.INVALID_XML, errors.DTD_FORBIDDEN, errors.ARTIFACT_INPUT_UNAVAILABLE),
    ),
)

BY_ID: dict[str, Operation] = {op.operation_id: op for op in OPERATIONS}
OPERATION_IDS: frozenset[str] = frozenset(BY_ID)


def all_error_codes(operation: Operation) -> list[str]:
    codes = list(errors.COMMON_ERROR_CODES)
    for code in operation.error_codes:
        if code not in codes:
            codes.append(code)
    return codes


__all__ = [
    "BY_ID",
    "ENCODINGS",
    "GROUPS",
    "OPERATIONS",
    "OPERATION_IDS",
    "Document",
    "Field",
    "FileOutput",
    "Operation",
    "Output",
    "all_error_codes",
]
