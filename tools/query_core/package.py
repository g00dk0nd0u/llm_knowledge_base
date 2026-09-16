from __future__ import annotations

import hashlib
import json
import os
import re
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


def _set_associated_file_semantics(document: fitz.Document) -> None:
    """Add PDF 2.0 AF links and MIME metadata to PyMuPDF's file spec."""
    candidates: list[int] = []
    for xref in range(1, document.xref_length()):
        try:
            obj = document.xref_object(xref, compressed=True)
        except RuntimeError:
            continue
        if "/Filespec" in obj and PAYLOAD_NAME in obj:
            candidates.append(xref)
    if len(candidates) != 1:
        raise QueryCoreError(
            "could not uniquely identify embedded project SQLite file specification"
        )
    filespec = candidates[0]
    document.xref_set_key(filespec, "AFRelationship", "/Data")
    ef_type, ef_value = document.xref_get_key(filespec, "EF")
    match = re.search(r"/F\s+(\d+)\s+0\s+R", ef_value if ef_type == "dict" else "")
    if not match:
        raise QueryCoreError("embedded project SQLite stream is missing")
    document.xref_set_key(int(match.group(1)), "Subtype", "/application#2Fvnd.sqlite3")
    catalog = document.pdf_catalog()
    af_type, af_value = document.xref_get_key(catalog, "AF")
    reference = f"{filespec} 0 R"
    if af_type == "null":
        document.xref_set_key(catalog, "AF", f"[{reference}]")
    elif af_type == "array" and reference not in af_value:
        document.xref_set_key(catalog, "AF", af_value[:-1] + f" {reference}]")


def _payload_attachment_names(document: fitz.Document) -> list[str]:
    matches = []
    for name in document.embfile_names():
        info = document.embfile_info(name)
        identities = (name, info.get("filename", ""), info.get("ufilename", ""))
        if any(Path(value).name == PAYLOAD_NAME for value in identities if value):
            matches.append(name)
    return matches


def package_pdf(drawing_pdf: Path, database: Path, output: Path) -> Path:
    """Copy drawing pages unchanged and attach the v1 database as an embedded file."""
    drawing_pdf, database, output = map(Path, (drawing_pdf, database, output))
    metadata = validate_database(database)
    if drawing_pdf.resolve() == output.resolve():
        raise QueryCoreError("enhanced PDF must not overwrite its source drawing")
    descriptor = json.dumps(
        {
            "af_relationship": "Data",
            "mime_type": MIME_TYPE,
            "payload_sha256": sha256(database),
            "schema_version": int(metadata["schema_version"]),
            "project_id": metadata["project_id"],
            "source_document_identity": metadata["source_document_identity"],
            "source_document_sha256": metadata["source_document_sha256"],
            "generator_version": metadata["generator_version"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        document = fitz.open(drawing_pdf)
        if _payload_attachment_names(document):
            raise QueryCoreError("source PDF already contains a project SQLite payload")
        document.embfile_add(
            PAYLOAD_NAME,
            database.read_bytes(),
            filename=PAYLOAD_NAME,
            ufilename=PAYLOAD_NAME,
            desc=descriptor,
        )
        _set_associated_file_semantics(document)
        document.save(output)
        document.close()
    except QueryCoreError:
        output.unlink(missing_ok=True)
        raise
    except (fitz.FileDataError, RuntimeError, OSError) as error:
        output.unlink(missing_ok=True)
        raise QueryCoreError(f"failed to package enhanced PDF: {error}") from error
    return output


def inspect_pdf(enhanced_pdf: Path) -> dict:
    try:
        document = fitz.open(enhanced_pdf)
        matches = _payload_attachment_names(document)
        if not matches:
            raise QueryCoreError("no embedded project SQLite payload")
        if len(matches) != 1:
            raise QueryCoreError("multiple ambiguous project SQLite payloads")
        name = matches[0]
        info = document.embfile_info(name)
        raw_description = info.get("desc", "")
        try:
            descriptor = json.loads(raw_description)
        except (TypeError, json.JSONDecodeError) as error:
            raise QueryCoreError("embedded payload descriptor is invalid") from error
        payload = document.embfile_get(name)
        document.close()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != descriptor.get("payload_sha256"):
            raise QueryCoreError("embedded payload SHA-256 mismatch")
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
    document = fitz.open(enhanced_pdf)
    payload = document.embfile_get(details["payload_name"])
    document.close()
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
