"""Limits shared by every step."""

from __future__ import annotations

MiB = 1024 * 1024

#: The entrypoint is given 600 s (extension.yaml timeout_seconds).
STEP_TIMEOUT_SECONDS = 600.0
#: Kept back from the step's time for the envelope and the host.
STEP_MARGIN_SECONDS = 30.0
#: Kept back for saving the result file after the work is done.
UPLOAD_RESERVE_SECONDS = 90.0
#: Reading one input file.
DOWNLOAD_TIMEOUT_SECONDS = 120.0

#: A document read whole into memory (every step except the streaming ones).
MAX_DOCUMENT_BYTES = 64 * MiB
#: A file read element by element (Parse large XML, XML to CSV/XLSX, Split).
MAX_STREAM_BYTES = 2048 * MiB
#: A schema or a stylesheet.
MAX_AUXILIARY_BYTES = 8 * MiB
#: The other files of a schema together (or one ZIP of them, unpacked).
MAX_SCHEMA_SET_BYTES = 64 * MiB
#: Files in a schema set.
MAX_SCHEMA_FILES = 500
#: Nesting depth of elements.
MAX_DEPTH = 256
#: Elements in a document read whole.
MAX_ELEMENTS = 5_000_000
#: Elements inside one record of a streamed file.
MAX_RECORD_ELEMENTS = 100_000
#: Documents one Merge step reads.
MAX_MERGE_DOCUMENTS = 50
#: A result file is also returned as text in the step output up to this size.
MAX_INLINE_BYTES = 256 * 1024
#: Changes one Edit step applies.
MAX_EDIT_CHANGES = 500
#: Values one XPath selection returns.
MAX_SELECT_RESULTS = 100_000
#: Parts one Split step writes.
MAX_PARTS = 10_000
#: Differences one Compare step lists.
MAX_DIFFERENCES = 1_000
#: Columns of a CSV or XLSX file.
MAX_COLUMNS = 1_000
#: Records Parse XML to records returns in the step output.
MAX_INLINE_RECORDS = 100_000
