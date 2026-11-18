"""A schema made of several files, resolved only among the files given to the step.

The main schema and its other files (given one by one or in one ZIP) get names.
Every ``xs:include``, ``xs:import``, ``xs:redefine`` and ``xs:override`` with a
``schemaLocation`` must name one of them, relative to the file that refers to it.
URLs, absolute paths, backslashes and ``..`` are refused before anything is compiled,
and the compiler's loader answers only from this set: nothing is read from the
network or the disk.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from collections.abc import Iterable, Mapping
from typing import Any

from lxml import etree

from . import errors, limits, safe
from .errors import ExtensionError
from .files import Source

XSD = "http://www.w3.org/2001/XMLSchema"
REFERENCES = ("include", "import", "redefine", "override")
#: The base the compiler sees; it never touches the disk under it.
VIRTUAL_ROOT = "/fs-xml-schema-set/"
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ZIP_RATIO = 200


def _refused(message: str) -> ExtensionError:
    return ExtensionError(errors.SCHEMA_IMPORT_REFUSED, message)


def check_name(name: str, what: str) -> str:
    """A relative POSIX path inside the set: no URL, absolute path, backslash or ``..``."""
    shown = errors.safe_text(name, 60)
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise _refused(f"{what}: a schema file name must be a short relative path")
    if any(ord(char) < 32 for char in name):
        raise _refused(f"{what}: {shown!r} has control characters")
    if "\\" in name:
        raise _refused(f"{what}: {shown!r} has a backslash; use / between folders")
    if _SCHEME.match(name) or name.startswith("//"):
        raise _refused(f"{what}: {shown!r} is a URL; only the given files are read")
    if name.startswith("/"):
        raise _refused(f"{what}: {shown!r} is an absolute path; only the given files are read")
    parts = name.split("/")
    if ".." in parts:
        raise _refused(f"{what}: {shown!r} goes up with ../; only the given files are read")
    return posixpath.normpath(name)


def _zip_members(data: bytes, label: str) -> Iterable[tuple[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExtensionError(errors.INVALID_SCHEMA, f"{label} is not a valid ZIP file") from None
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > limits.MAX_SCHEMA_FILES:
            raise ExtensionError(errors.LIMIT_EXCEEDED, f"{label} has too many files")
        total = 0
        for info in infos:
            total += info.file_size
            if total > limits.MAX_SCHEMA_SET_BYTES:
                raise ExtensionError(errors.LIMIT_EXCEEDED, f"{label} unpacks to too much data")
            if info.file_size > limits.MiB and info.file_size > _ZIP_RATIO * max(
                info.compress_size, 1
            ):
                raise ExtensionError(
                    errors.LIMIT_EXCEEDED, f"{label} is compressed suspiciously well (zip bomb?)"
                )
            yield check_name(info.filename, label), archive.read(info)


def collect(
    main: Source,
    main_name: str | None,
    parts: list[Source],
    part_names: list[Any] | None,
) -> tuple[str, dict[str, bytes]]:
    """The main schema's name and every file of the set by name."""
    files: dict[str, bytes] = {}
    name = check_name(main_name or main.filename or "schema.xsd", "Schema file name")
    files[name] = main.read(limits.MAX_AUXILIARY_BYTES)
    if part_names is not None and not all(isinstance(n, str) for n in part_names):
        raise errors.invalid("Other schema file names must be a list of names")
    total = len(files[name])
    for index, part in enumerate(parts):
        data = part.read(limits.MAX_SCHEMA_SET_BYTES)
        given = part_names[index] if part_names and index < len(part_names) else ""
        if data.startswith(b"PK\x03\x04"):
            members = list(_zip_members(data, part.label))
        else:
            own = given or part.filename
            if not own:
                raise errors.invalid(
                    f"{part.label} has no name: give it in Other schema file names, or send a ZIP"
                )
            members = [(check_name(own, part.label), data)]
        for member, body in members:
            total += len(body)
            if total > limits.MAX_SCHEMA_SET_BYTES:
                raise ExtensionError(errors.LIMIT_EXCEEDED, "The schema files are too large")
            if member in files:
                raise errors.invalid(f"Two schema files are named {errors.safe_text(member, 60)!r}")
            files[member] = body
    if len(files) > limits.MAX_SCHEMA_FILES:
        raise ExtensionError(errors.LIMIT_EXCEEDED, "Too many schema files")
    return name, files


def check_references(main_name: str, files: Mapping[str, bytes]) -> dict[str, Any]:
    """Parse every reachable schema safely and check every reference; the parsed trees."""
    trees: dict[str, Any] = {}
    pending = [main_name]
    while pending:
        current = pending.pop()
        if current in trees:
            continue
        try:
            tree = safe.parse_document(
                files[current],
                what=f"Schema file {errors.safe_text(current, 60)!r}",
                max_bytes=limits.MAX_SCHEMA_SET_BYTES,
            )
        except ExtensionError as exc:
            if exc.code == errors.INVALID_XML:
                raise ExtensionError(errors.INVALID_SCHEMA, exc.message) from None
            raise
        trees[current] = tree
        folder = posixpath.dirname(current)
        for tag in REFERENCES:
            for node in tree.iter(f"{{{XSD}}}{tag}"):
                location = node.get("schemaLocation")
                if location is None:
                    continue
                where = f"xs:{tag} in {errors.safe_text(current, 60)!r} (line {node.sourceline})"
                target = posixpath.normpath(posixpath.join(folder, check_name(location, where)))
                if target.startswith("../") or target == "..":
                    raise _refused(f"{where} goes outside the given files")
                if target not in files:
                    raise _refused(
                        f"{where} refers to {errors.safe_text(location, 60)!r}, which is not "
                        "among the given schema files"
                    )
                pending.append(target)
    return trees


class SetResolver(etree.Resolver):
    """Answers the schema compiler only from the set; anything else is refused."""

    def __init__(self, files: Mapping[str, bytes]) -> None:
        super().__init__()
        self.files = files
        self.refused: list[str] = []

    def resolve(self, system_url: str, public_id: str, context: Any) -> Any:
        url = system_url or ""
        if url.startswith(VIRTUAL_ROOT):
            name = posixpath.normpath(url[len(VIRTUAL_ROOT) :])
            if name in self.files and not name.startswith(".."):
                return self.resolve_string(self.files[name], context, base_url=url)
        self.refused.append(errors.safe_text(url, 60))
        return self.resolve_string("", context)


def compile_schema(
    main_name: str, files: Mapping[str, bytes], *, main_from_text: bool = False
) -> Any:
    """The compiled XMLSchema of the set (``main_from_text``: typed UTF-8 text)."""
    check_references(main_name, files)
    resolver = SetResolver(files)
    parser = safe.make_parser(encoding="utf-8" if main_from_text else None, resolver=resolver)
    try:
        root = etree.fromstring(files[main_name], parser, base_url=VIRTUAL_ROOT + main_name)
    except etree.XMLSyntaxError as exc:
        raise ExtensionError(
            errors.INVALID_SCHEMA, safe.syntax_message(exc, "The schema")
        ) from None
    if root.tag != f"{{{XSD}}}schema":
        raise ExtensionError(errors.INVALID_SCHEMA, "The schema's root is not xs:schema")
    try:
        schema = etree.XMLSchema(root.getroottree())
    except etree.XMLSchemaParseError as exc:
        if resolver.refused:
            raise _refused(
                f"The schema tried to load {resolver.refused[0]!r}, which is not among the given "
                "schema files"
            ) from None
        entries = list(exc.error_log)[:1]
        detail = errors.safe_text(entries[0].message, 300) if entries else errors.safe_text(exc)
        line = entries[0].line if entries else None
        where = f" at line {line}" if line else ""
        raise ExtensionError(
            errors.INVALID_SCHEMA, f"The schema is not valid{where}: {detail}"
        ) from None
    if resolver.refused:
        raise _refused(
            f"The schema tried to load {resolver.refused[0]!r}, which is not among the given "
            "schema files"
        )
    return schema


__all__ = [
    "SetResolver",
    "VIRTUAL_ROOT",
    "check_name",
    "check_references",
    "collect",
    "compile_schema",
]
