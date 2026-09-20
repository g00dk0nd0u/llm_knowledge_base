from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import fitz

from .errors import QueryCoreError
from .query import validate_database

PAYLOAD_NAME = "project.sqlite"
MIME_TYPE = "application/vnd.sqlite3"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _set_associated_file_semantics(
    document: fitz.Document, stream_xref: int, description: str
) -> None:
    """Associate the stream returned by embfile_add without scanning PDF internals.

    PyMuPDF 1.28 emits the name-tree FileSpec inline and returns the indirect
    EmbeddedFile stream xref.  A separate indirect FileSpec for the catalog AF
    array is valid PDF 2.0 and works for both inline and indirect name-tree forms.
    """
    document.xref_set_key(stream_xref, "Subtype", "/application#2Fvnd.sqlite3")
    filespec = document.get_new_xref()
    filename = fitz.get_pdf_str(PAYLOAD_NAME)
    pdf_description = fitz.get_pdf_str(description)
    document.update_object(
        filespec,
        f"<</Type/Filespec/F{filename}/UF{filename}/Desc{pdf_description}"
        f"/EF<</F {stream_xref} 0 R>>/AFRelationship/Data>>",
    )
    catalog = document.pdf_catalog()
    af_type, af_value = document.xref_get_key(catalog, "AF")
    reference = f"{filespec} 0 R"
    if af_type == "null":
        document.xref_set_key(catalog, "AF", f"[{reference}]")
    elif af_type == "array" and reference not in af_value:
        document.xref_set_key(catalog, "AF", af_value[:-1] + f" {reference}]")
    elif af_type != "array":
        raise QueryCoreError(f"unsupported catalog AF representation: {af_type}")


def _payload_attachment_names(document: fitz.Document) -> list[str]:
    matches = []
    for name in document.embfile_names():
        info = document.embfile_info(name)
        identities = (name, info.get("filename", ""), info.get("ufilename", ""))
        if any(Path(value).name == PAYLOAD_NAME for value in identities if value):
            matches.append(name)
    return matches


def package_pdf(drawing_pdf: Path, database: Path, output: Path) -> Path:
    """Copy drawing pages unchanged and attach the v2 database as an embedded file."""
    drawing_pdf, database, output = map(Path, (drawing_pdf, database, output))
    metadata = validate_database(database)
    if metadata["binding_mode"] != "single_document":
        raise QueryCoreError(
            "project-bound payload cannot be packaged into a single PDF"
        )
    if drawing_pdf.resolve() == output.resolve():
        raise QueryCoreError("enhanced PDF must not overwrite its source drawing")
    if sha256(drawing_pdf) != metadata["source_document_sha256"]:
        raise QueryCoreError("SQLite payload was built for a different source drawing")
    descriptor = json.dumps(
        {
            "af_relationship": "Data",
            "mime_type": MIME_TYPE,
            "payload_sha256": sha256(database),
            "schema_version": int(metadata["schema_version"]),
            "project_id": metadata["project_id"],
            "binding_mode": "single_document",
            "source_document_identity": metadata["source_document_identity"],
            "source_document_sha256": metadata["source_document_sha256"],
            "generator_version": metadata["generator_version"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".pdf", dir=output.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with fitz.open(drawing_pdf) as document:
            if _payload_attachment_names(document):
                raise QueryCoreError(
                    "source PDF already contains a project SQLite payload"
                )
            stream_xref = document.embfile_add(
                PAYLOAD_NAME,
                database.read_bytes(),
                filename=PAYLOAD_NAME,
                ufilename=PAYLOAD_NAME,
                desc=descriptor,
            )
            _set_associated_file_semantics(document, stream_xref, descriptor)
            document.save(temporary)
        os.replace(temporary, output)
    except QueryCoreError:
        raise
    except (fitz.FileDataError, RuntimeError, OSError) as error:
        raise QueryCoreError(f"failed to package enhanced PDF: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
    return output


def inspect_pdf(enhanced_pdf: Path) -> dict:
    try:
        with fitz.open(enhanced_pdf) as document:
            matches = _payload_attachment_names(document)
            if not matches:
                raise QueryCoreError("no embedded project SQLite payload")
            if len(matches) != 1:
                raise QueryCoreError("multiple ambiguous project SQLite payloads")
            name = matches[0]
            info = document.embfile_info(name)
            raw_description = info.get("description")
            if raw_description is None:
                raw_description = info.get("desc")
            if raw_description is None:
                raise QueryCoreError("embedded payload descriptor is missing")
            payload = document.embfile_get(name)
        try:
            descriptor = json.loads(raw_description)
        except (TypeError, json.JSONDecodeError) as error:
            raise QueryCoreError("embedded payload descriptor is invalid") from error
        actual = hashlib.sha256(payload).hexdigest()
        if actual != descriptor.get("payload_sha256"):
            raise QueryCoreError("embedded payload SHA-256 mismatch")
        if descriptor.get("af_relationship") != "Data":
            raise QueryCoreError(
                "embedded payload descriptor has invalid AF relationship"
            )
        if descriptor.get("mime_type") != MIME_TYPE:
            raise QueryCoreError("embedded payload descriptor has invalid MIME type")
        fd, temporary_name = tempfile.mkstemp(suffix=".sqlite")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            metadata = validate_database(Path(temporary_name))
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        for key in (
            "schema_version",
            "project_id",
            "source_document_identity",
            "source_document_sha256",
            "generator_version",
        ):
            expected = int(metadata[key]) if key == "schema_version" else metadata[key]
            if descriptor.get(key) != expected:
                raise QueryCoreError(f"embedded payload descriptor mismatch: {key}")
        descriptor_mode = descriptor.get("binding_mode", "single_document")
        if descriptor_mode != metadata["binding_mode"]:
            raise QueryCoreError("embedded payload descriptor mismatch: binding_mode")
        return {
            "payload_name": name,
            "payload_size": len(payload),
            "payload_sha256": actual,
            **descriptor,
        }
    except QueryCoreError:
        raise
    except (fitz.FileDataError, RuntimeError, OSError) as error:
        raise QueryCoreError(f"invalid enhanced PDF: {error}") from error


def extract_payload(enhanced_pdf: Path, output: Path) -> Path:
    details = inspect_pdf(enhanced_pdf)
    with fitz.open(enhanced_pdf) as document:
        payload = document.embfile_get(details["payload_name"])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        validate_database(Path(name))
        os.replace(name, output)
    finally:
        Path(name).unlink(missing_ok=True)
    return output


def cached_payload(
    enhanced_pdf: Path, cache_root: Path = Path(".cache/query-core")
) -> Path:
    """Content-address cache by enhanced-PDF hash and validated payload hash."""
    details = inspect_pdf(enhanced_pdf)
    cache = (
        Path(cache_root)
        / sha256(enhanced_pdf)
        / details["payload_sha256"]
        / PAYLOAD_NAME
    )
    if cache.exists():
        try:
            validate_database(cache)
            if sha256(cache) == details["payload_sha256"]:
                return cache
        except QueryCoreError:
            cache.unlink(missing_ok=True)
    return extract_payload(enhanced_pdf, cache)
