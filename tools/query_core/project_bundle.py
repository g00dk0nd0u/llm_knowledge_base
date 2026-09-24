"""Portable directory bundles for project-bound PDF Query Core databases."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from .build import GENERATOR_VERSION, SCHEMA_VERSION
from .errors import QueryCoreError
from .pdf_pipeline_contract import created_from, require_created_from, require_pipeline_version
from .query import validate_database

BUNDLE_FORMAT = "query-core-project-bundle/1"
_DATABASE_PATH = "project.sqlite"


def _sha256(path: Path) -> str:
    """Hash a runtime bundle member without importing the PDF ingestion adapter."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_id(source_file: Any) -> str:
    """Validate the persisted project source identity using only the stdlib."""
    if not isinstance(source_file, str):
        raise QueryCoreError("source_file must be a canonical repository-relative path")
    logical = PurePosixPath(source_file)
    parts = logical.parts
    if (
        logical.is_absolute() or logical.as_posix() != source_file or len(parts) < 4
        or parts[0] != "projects" or not parts[1] or parts[2] != "source"
        or any(part in {".", ".."} for part in parts)
        or logical.suffix.lower() != ".pdf"
    ):
        raise QueryCoreError(
            "source_file must match projects/<project-id>/source/<path>.pdf"
        )
    return parts[1]


def _load_manifest(directory: Path) -> dict[str, Any]:
    path = _regular_file(directory, "bundle.json", "project bundle manifest")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueryCoreError(f"invalid project bundle manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise QueryCoreError("project bundle manifest must be a JSON object")
    return value


def _canonical_relative(value: Any, label: str, prefix: str | None = None) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise QueryCoreError(f"{label} must be a canonical relative POSIX path")
    logical = PurePosixPath(value)
    if (
        logical.is_absolute()
        or logical.as_posix() != value
        or any(part in {"", ".", ".."} for part in logical.parts)
        or (prefix is not None and (not logical.parts or logical.parts[0] != prefix))
    ):
        raise QueryCoreError(f"{label} must be a canonical relative POSIX path")
    return value


def _canonical_identity(value: Any, project_id: str) -> str:
    try:
        actual_project = _project_id(value)
    except QueryCoreError as exc:
        raise QueryCoreError("invalid project bundle document identity") from exc
    if actual_project != project_id:
        raise QueryCoreError("project bundle document identity belongs to another project")
    return value


def _regular_file(directory: Path, relative: str, label: str) -> Path:
    """Return a contained regular file, rejecting symlinks in every path component."""
    current = directory
    if directory.is_symlink() or not directory.is_dir():
        raise QueryCoreError("project bundle directory must be a non-symlink directory")
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise QueryCoreError(f"{label} may not be a symlink")
    try:
        current.resolve(strict=True).relative_to(directory.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise QueryCoreError(f"{label} escapes project bundle") from exc
    if not current.is_file():
        raise QueryCoreError(f"{label} must be a regular file")
    return current


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise QueryCoreError(f"invalid {label}")
    return value


def _document_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    project_id = manifest.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise QueryCoreError("invalid project bundle project_id")
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise QueryCoreError("project bundle documents must be a list")
    identities: set[str] = set()
    folded_paths: set[str] = set()
    checked: list[dict[str, Any]] = []
    for index, item in enumerate(documents):
        if not isinstance(item, dict):
            raise QueryCoreError(f"project bundle document {index} must be an object")
        identity = _canonical_identity(item.get("identity"), project_id)
        if identity in identities:
            raise QueryCoreError(f"duplicate project bundle identity: {identity}")
        identities.add(identity)
        bundle_path = _canonical_relative(
            item.get("bundle_path"), "bundle_path", "sources"
        )
        if len(PurePosixPath(bundle_path).parts) < 2 or not bundle_path.lower().endswith(".pdf"):
            raise QueryCoreError("bundle_path must identify a PDF below sources/")
        identity_relative = PurePosixPath(identity).relative_to(
            PurePosixPath("projects") / project_id / "source"
        )
        if bundle_path != (PurePosixPath("sources") / identity_relative).as_posix():
            raise QueryCoreError("bundle_path does not match document identity")
        folded = bundle_path.casefold()
        if folded in folded_paths:
            raise QueryCoreError("case-insensitive project bundle path collision")
        folded_paths.add(folded)
        document_id = item.get("document_id")
        page_count = item.get("page_count")
        if not isinstance(document_id, str) or not document_id:
            raise QueryCoreError("invalid project bundle document_id")
        expected_id = "doc-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        if document_id != expected_id:
            raise QueryCoreError("project bundle document_id does not match identity")
        if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
            raise QueryCoreError("invalid project bundle page_count")
        _sha(item.get("source_sha256"), "project bundle source_sha256")
        expected_map = {
            "kind": "one_to_one",
            "source_page_start": 1,
            "query_core_page_start": 1,
            "page_count": page_count,
        }
        if item.get("page_map") != expected_map:
            raise QueryCoreError(f"invalid page_map for {identity}")
        checked.append(item)
    if [item["identity"] for item in checked] != sorted(identities):
        raise QueryCoreError("project bundle documents must be sorted by identity")
    return checked


def _lightweight_manifest(directory: Path) -> tuple[dict[str, Any], Path]:
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise QueryCoreError("project bundle directory must be a non-symlink directory")
    manifest = _load_manifest(directory)
    pipeline_version = require_pipeline_version(
        manifest.get("pipeline_version"), "project bundle pipeline_version"
    )
    expected = {
        "bundle_format": BUNDLE_FORMAT,
        "binding_mode": "project",
        "created_from": created_from(pipeline_version),
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise QueryCoreError(f"project bundle {key} mismatch")
    _document_entries(manifest)
    database_entry = manifest.get("database")
    if not isinstance(database_entry, dict):
        raise QueryCoreError("project bundle database must be an object")
    database_relative = _canonical_relative(database_entry.get("path"), "database path")
    if database_relative != _DATABASE_PATH:
        raise QueryCoreError("project bundle database path must be project.sqlite")
    expected_sha = _sha(database_entry.get("sha256"), "project bundle database SHA-256")
    database = _regular_file(directory, database_relative, "project bundle database")
    if _sha256(database) != expected_sha:
        raise QueryCoreError("project bundle database SHA-256 mismatch")
    metadata = validate_database(database)
    metadata_expected = {
        "project_id": manifest.get("project_id"),
        "binding_mode": "project",
        "created_from": created_from(pipeline_version),
        "schema_version": str(SCHEMA_VERSION),
        "generator_version": GENERATOR_VERSION,
    }
    for key, value in metadata_expected.items():
        if metadata.get(key) != value:
            raise QueryCoreError(f"project bundle database metadata {key} mismatch")
    return manifest, database


def project_bundle_database(bundle_directory: Path) -> Path:
    """Resolve a bundle database with lightweight validation (sources are not read)."""
    return _lightweight_manifest(Path(bundle_directory))[1]


def _database_documents(database: Path) -> dict[str, tuple[str, str, int]]:
    try:
        with closing(
            sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
        ) as connection:
            return {
                identity: (document_id, source_sha, page_count)
                for document_id, identity, source_sha, page_count in connection.execute(
                    "SELECT d.id,d.identity,d.source_sha256,count(p.id) "
                    "FROM documents d LEFT JOIN pdf_pages p ON p.document_id=d.id "
                    "GROUP BY d.id,d.identity,d.source_sha256"
                )
            }
    except sqlite3.Error as exc:
        raise QueryCoreError(f"invalid project bundle database: {exc}") from exc


def _database_document(
    database: Path, identity: str
) -> tuple[str, str, int] | None:
    """Read one document tuple and require its PDF pages to be one-based contiguous."""
    try:
        with closing(
            sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
        ) as connection:
            row = connection.execute(
                "SELECT d.id,d.source_sha256,count(p.id) "
                "FROM documents d LEFT JOIN pdf_pages p ON p.document_id=d.id "
                "WHERE d.identity=? GROUP BY d.id,d.source_sha256",
                (identity,),
            ).fetchone()
            if row is None:
                return None
            pages = [
                page[0]
                for page in connection.execute(
                    "SELECT p.page_number FROM pdf_pages p "
                    "JOIN documents d ON d.id=p.document_id "
                    "WHERE d.identity=? ORDER BY p.page_number",
                    (identity,),
                )
            ]
            if pages != list(range(1, row[2] + 1)):
                raise QueryCoreError(
                    f"project bundle database has non-contiguous PDF pages: {identity}"
                )
            return row[0], row[1], row[2]
    except sqlite3.Error as exc:
        raise QueryCoreError(f"invalid project bundle database: {exc}") from exc


def inspect_pdf_project_bundle(bundle_directory: Path) -> dict[str, Any]:
    """Exhaustively verify a portable project bundle and every bundled source."""
    directory = Path(bundle_directory)
    manifest, database = _lightweight_manifest(directory)
    entries = _document_entries(manifest)
    expected = {
        item["identity"]: (
            item["document_id"], item["source_sha256"], item["page_count"]
        )
        for item in entries
    }
    if _database_documents(database) != expected:
        raise QueryCoreError("project bundle database/document map mismatch")
    for item in entries:
        _database_document(database, item["identity"])
        source = _regular_file(directory, item["bundle_path"], "bundled source PDF")
        if _sha256(source) != item["source_sha256"]:
            raise QueryCoreError(f"bundled source PDF SHA-256 mismatch: {item['identity']}")
    sources = directory / "sources"
    if sources.is_symlink() or not sources.is_dir():
        raise QueryCoreError("project bundle sources must be a non-symlink directory")
    return manifest


def project_bundle_source(bundle_directory: Path, identity: str) -> Path:
    """Resolve and SHA-verify exactly one source PDF selected by logical identity."""
    directory = Path(bundle_directory)
    manifest, database = _lightweight_manifest(directory)
    entries = _document_entries(manifest)
    matches = [item for item in entries if item["identity"] == identity]
    if not matches:
        raise QueryCoreError(f"unknown project bundle identity: {identity}")
    item = matches[0]
    database_tuple = _database_document(database, identity)
    manifest_tuple = (
        item["document_id"], item["source_sha256"], item["page_count"]
    )
    if database_tuple != manifest_tuple:
        raise QueryCoreError(
            f"project bundle source does not match Query Core document: {identity}"
        )
    source = _regular_file(directory, item["bundle_path"], "bundled source PDF")
    if _sha256(source) != item["source_sha256"]:
        raise QueryCoreError(f"bundled source PDF SHA-256 mismatch: {identity}")
    return source


def package_pdf_project_bundle(
    repo_root: Path,
    project_directory: Path,
    database: Path,
    output: Path,
) -> Path:
    """Atomically package a current project snapshot as a portable directory."""
    # Creation dependencies stay outside the read-only, zero-install bundle path.
    from .pdf_adapter import _validate_output_location
    from .project_pdf_adapter import _manifest
    from .project_pdf_update import compare_pdf_project_database

    root = Path(repo_root).resolve()
    project_id, pipeline_version, entries = _manifest(root, project_directory)
    project = (root / "projects" / project_id).resolve()
    output = Path(output)
    _validate_output_location(
        output,
        protected_files=(project / "manifest.json",),
        protected_directories=(project / "source", project / "knowledge"),
    )
    if output.exists() or output.is_symlink():
        raise QueryCoreError("project bundle output already exists")
    report = compare_pdf_project_database(root, project_directory, database)
    if report.added or report.changed or report.removed:
        raise QueryCoreError("project Query Core is stale; build or update it first")
    database_metadata = validate_database(database)
    require_created_from(
        database_metadata.get("created_from"), pipeline_version,
        "project bundle database created_from",
    )

    documents: list[dict[str, Any]] = []
    folded: set[str] = set()
    for entry in entries:
        identity = _canonical_identity(entry["source_file"], project_id)
        relative_source = PurePosixPath(identity).relative_to(
            PurePosixPath("projects") / project_id / "source"
        )
        bundle_path = (PurePosixPath("sources") / relative_source).as_posix()
        key = bundle_path.casefold()
        if key in folded:
            raise QueryCoreError("case-insensitive project bundle path collision")
        folded.add(key)
        documents.append({
            "document_id": entry["document_id"],
            "identity": identity,
            "source_sha256": entry["source_sha256"],
            "page_count": entry["page_count"],
            "bundle_path": bundle_path,
            "page_map": {
                "kind": "one_to_one", "source_page_start": 1,
                "query_core_page_start": 1, "page_count": entry["page_count"],
            },
        })
    documents.sort(key=lambda item: item["identity"])

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        copied_database = temporary / _DATABASE_PATH
        shutil.copyfile(database, copied_database)
        database_sha = _sha256(copied_database)
        if database_sha != _sha256(Path(database)):
            raise QueryCoreError("project database changed while packaging")
        (temporary / "sources").mkdir()
        for item in documents:
            source = root / item["identity"]
            destination = temporary / item["bundle_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if _sha256(destination) != item["source_sha256"]:
                raise QueryCoreError(f"source PDF changed while packaging: {item['identity']}")
        manifest = {
            "bundle_format": BUNDLE_FORMAT,
            "project_id": project_id,
            "binding_mode": "project",
            "created_from": created_from(pipeline_version),
            "schema_version": SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "pipeline_version": pipeline_version,
            "database": {"path": _DATABASE_PATH, "sha256": database_sha},
            "documents": documents,
        }
        (temporary / "bundle.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        inspect_pdf_project_bundle(temporary)
        if output.exists() or output.is_symlink():
            raise QueryCoreError("project bundle output already exists")
        os.rename(temporary, output)
        return output
    except QueryCoreError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise QueryCoreError(f"failed to package project bundle: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
