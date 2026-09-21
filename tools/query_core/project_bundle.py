"""Portable directory bundles for project-bound PDF Query Core databases."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .build import GENERATOR_VERSION, SCHEMA_VERSION
from .errors import QueryCoreError
from .pdf_adapter import _sha256, _validate_output_location
from .project_pdf_adapter import PIPELINE_VERSION, _manifest
from .project_pdf_update import compare_pdf_project_database
from .query import validate_database

BUNDLE_FORMAT = "query-core-project-bundle/1"
BUNDLE_MANIFEST_NAME = "bundle.json"
BUNDLE_DATABASE_NAME = "project.sqlite"
BUNDLE_SOURCES_DIRECTORY = "sources"
_CREATED_FROM = "pdf-pipeline/1"


def _canonical_relative_path(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise QueryCoreError(f"{label} must be a canonical relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise QueryCoreError(f"{label} must be a canonical relative path")
    return path


def _inside_bundle(root: Path, logical_path: str, label: str) -> Path:
    path = _canonical_relative_path(logical_path, label)
    resolved = (root / Path(*path.parts)).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise QueryCoreError(f"{label} resolves outside project bundle") from exc
    return resolved


def _bundle_source_path(project_id: str, identity: str) -> str:
    logical = PurePosixPath(identity)
    expected = ("projects", project_id, "source")
    if len(logical.parts) <= len(expected) or logical.parts[:3] != expected:
        raise QueryCoreError(
            "bundle source identity must match projects/<project-id>/source/<path>.pdf"
        )
    return PurePosixPath(BUNDLE_SOURCES_DIRECTORY, *logical.parts[3:]).as_posix()


def _database_documents(database: Path) -> dict[str, tuple[str, str, int, int, int]]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
        return {
            identity: (document_id, source_sha256, page_count, min_page, max_page)
            for document_id, identity, source_sha256, page_count, min_page, max_page in connection.execute(
                "SELECT d.id,d.identity,d.source_sha256,count(p.id),"
                "coalesce(min(p.page_number),0),coalesce(max(p.page_number),0) "
                "FROM documents d LEFT JOIN pdf_pages p ON p.document_id=d.id "
                "GROUP BY d.id,d.identity,d.source_sha256 ORDER BY d.identity"
            )
        }
    except sqlite3.Error as exc:
        raise QueryCoreError(f"failed to inspect project Query Core database: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def _page_map(page_count: int) -> dict[str, Any]:
    return {
        "kind": "one_to_one",
        "source_page_start": 1,
        "query_core_page_start": 1,
        "page_count": page_count,
    }


def _bundle_manifest(
    project_id: str,
    database: Path,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = validate_database(database)
    if metadata.get("binding_mode") != "project":
        raise QueryCoreError("project bundle requires a project-bound Query Core database")
    if metadata.get("project_id") != project_id:
        raise QueryCoreError("project bundle database project_id mismatch")
    if metadata.get("created_from") != _CREATED_FROM:
        raise QueryCoreError("project bundle database must be created from pdf-pipeline/1")
    if metadata.get("generator_version") != GENERATOR_VERSION:
        raise QueryCoreError("project bundle database generator_version mismatch")
    if metadata.get("schema_version") != str(SCHEMA_VERSION):
        raise QueryCoreError("project bundle database schema_version mismatch")

    database_documents = _database_documents(database)
    documents: list[dict[str, Any]] = []
    seen_casefold_paths: set[str] = set()
    for entry in entries:
        identity = entry["source_file"]
        database_document = database_documents.get(identity)
        if database_document is None:
            raise QueryCoreError(f"project bundle database is missing document: {identity}")
        document_id, source_sha256, page_count, min_page, max_page = database_document
        expected = (
            entry["document_id"],
            entry["source_sha256"],
            entry["page_count"],
        )
        if (document_id, source_sha256, page_count) != expected:
            raise QueryCoreError(f"project bundle database document mismatch: {identity}")
        if page_count < 1 or min_page != 1 or max_page != page_count:
            raise QueryCoreError(f"project bundle database page mapping is invalid: {identity}")
        bundle_path = _bundle_source_path(project_id, identity)
        portable_key = bundle_path.casefold()
        if portable_key in seen_casefold_paths:
            raise QueryCoreError(
                "project bundle source paths collide on case-insensitive filesystems: "
                + bundle_path
            )
        seen_casefold_paths.add(portable_key)
        documents.append(
            {
                "document_id": document_id,
                "identity": identity,
                "source_sha256": source_sha256,
                "page_count": page_count,
                "bundle_path": bundle_path,
                "page_map": _page_map(page_count),
            }
        )
    if set(database_documents) != {entry["source_file"] for entry in entries}:
        raise QueryCoreError("project bundle database document set mismatch")
    return {
        "bundle_format": BUNDLE_FORMAT,
        "project_id": project_id,
        "binding_mode": "project",
        "created_from": _CREATED_FROM,
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "database": {
            "path": BUNDLE_DATABASE_NAME,
            "sha256": _sha256(database),
        },
        "documents": documents,
    }


def package_pdf_project_bundle(
    repo_root: Path,
    project_directory: Path,
    database: Path,
    output_directory: Path,
) -> Path:
    """Create one atomic portable directory bundle without rewriting source PDFs."""
    root = Path(repo_root).resolve()
    database = Path(database)
    output = Path(output_directory)
    project_id, entries = _manifest(root, project_directory)
    project = (root / "projects" / project_id).resolve()
    _validate_output_location(
        output,
        protected_files=(project / "manifest.json",),
        protected_directories=(project / "source", project / "knowledge"),
    )
    if output.exists() or output.is_symlink():
        raise QueryCoreError("project bundle output must not already exist")
    if not database.is_file() or database.is_symlink():
        raise QueryCoreError("project bundle database must be an existing regular file")

    comparison = compare_pdf_project_database(root, project_directory, database)
    if comparison.added or comparison.changed or comparison.removed:
        raise QueryCoreError(
            "project Query Core is stale relative to the current PDF Pipeline manifest"
        )
    manifest = _bundle_manifest(project_id, database, entries)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.bundle.", dir=output.parent)
    )
    try:
        shutil.copyfile(database, temporary / BUNDLE_DATABASE_NAME)
        sources = temporary / BUNDLE_SOURCES_DIRECTORY
        sources.mkdir()
        for document in manifest["documents"]:
            source = (root / document["identity"]).resolve()
            destination = _inside_bundle(
                temporary, document["bundle_path"], "bundle source path"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if _sha256(destination) != document["source_sha256"]:
                raise QueryCoreError(
                    f"copied source PDF SHA-256 mismatch: {document['identity']}"
                )
        (temporary / BUNDLE_MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        inspect_pdf_project_bundle(temporary)
        os.replace(temporary, output)
        return output
    except QueryCoreError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError, sqlite3.Error) as exc:
        raise QueryCoreError(f"failed to package project Query Bundle: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _load_bundle_manifest(bundle: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(
            (bundle / BUNDLE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueryCoreError(f"invalid project bundle manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise QueryCoreError("project bundle manifest must be a JSON object")
    return manifest


def inspect_pdf_project_bundle(bundle_directory: Path) -> dict[str, Any]:
    """Validate a portable project bundle and return its canonical descriptor."""
    bundle = Path(bundle_directory).resolve()
    if not bundle.is_dir():
        raise QueryCoreError("project bundle must be a directory")
    manifest = _load_bundle_manifest(bundle)
    expected_scalars = {
        "bundle_format": BUNDLE_FORMAT,
        "binding_mode": "project",
        "created_from": _CREATED_FROM,
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "pipeline_version": PIPELINE_VERSION,
    }
    for key, expected in expected_scalars.items():
        if manifest.get(key) != expected:
            raise QueryCoreError(f"project bundle manifest {key} mismatch")
    project_id = manifest.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise QueryCoreError("project bundle project_id must be a non-empty string")

    database_descriptor = manifest.get("database")
    if not isinstance(database_descriptor, dict):
        raise QueryCoreError("project bundle database descriptor must be an object")
    if database_descriptor.get("path") != BUNDLE_DATABASE_NAME:
        raise QueryCoreError("project bundle database path must be project.sqlite")
    database_sha = database_descriptor.get("sha256")
    if (
        not isinstance(database_sha, str)
        or len(database_sha) != 64
        or any(character not in "0123456789abcdef" for character in database_sha)
    ):
        raise QueryCoreError("project bundle database SHA-256 is invalid")
    database = _inside_bundle(bundle, BUNDLE_DATABASE_NAME, "bundle database path")
    if not database.is_file() or database.is_symlink():
        raise QueryCoreError("project bundle database is missing or is not a regular file")
    if _sha256(database) != database_sha:
        raise QueryCoreError("project bundle database SHA-256 mismatch")
    metadata = validate_database(database)
    for key, expected in (
        ("binding_mode", "project"),
        ("project_id", project_id),
        ("created_from", _CREATED_FROM),
        ("schema_version", str(SCHEMA_VERSION)),
        ("generator_version", GENERATOR_VERSION),
    ):
        if metadata.get(key) != expected:
            raise QueryCoreError(f"project bundle database metadata {key} mismatch")

    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise QueryCoreError("project bundle documents must be a list")
    identities: set[str] = set()
    document_ids: set[str] = set()
    bundle_paths: set[str] = set()
    portable_paths: set[str] = set()
    expected_database_documents: dict[str, tuple[str, str, int]] = {}
    previous_identity: str | None = None
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise QueryCoreError(f"project bundle document {index} must be an object")
        identity = document.get("identity")
        document_id = document.get("document_id")
        source_sha = document.get("source_sha256")
        page_count = document.get("page_count")
        bundle_path = document.get("bundle_path")
        if not isinstance(identity, str) or not identity:
            raise QueryCoreError(f"project bundle document {index} has invalid identity")
        expected_bundle_path = _bundle_source_path(project_id, identity)
        if bundle_path != expected_bundle_path:
            raise QueryCoreError(
                f"project bundle document {index} has invalid bundle_path"
            )
        if not isinstance(document_id, str) or not document_id:
            raise QueryCoreError(f"project bundle document {index} has invalid document_id")
        if (
            not isinstance(source_sha, str)
            or len(source_sha) != 64
            or any(character not in "0123456789abcdef" for character in source_sha)
        ):
            raise QueryCoreError(
                f"project bundle document {index} has invalid source_sha256"
            )
        if (
            not isinstance(page_count, int)
            or isinstance(page_count, bool)
            or page_count < 1
        ):
            raise QueryCoreError(f"project bundle document {index} has invalid page_count")
        if document.get("page_map") != _page_map(page_count):
            raise QueryCoreError(f"project bundle document {index} has invalid page_map")
        if previous_identity is not None and identity <= previous_identity:
            raise QueryCoreError("project bundle documents must be sorted by identity")
        previous_identity = identity
        if identity in identities or document_id in document_ids or bundle_path in bundle_paths:
            raise QueryCoreError("project bundle document identities and paths must be unique")
        portable_key = bundle_path.casefold()
        if portable_key in portable_paths:
            raise QueryCoreError(
                "project bundle source paths collide on case-insensitive filesystems"
            )
        identities.add(identity)
        document_ids.add(document_id)
        bundle_paths.add(bundle_path)
        portable_paths.add(portable_key)
        source = _inside_bundle(bundle, bundle_path, "bundle source path")
        sources_root = (bundle / BUNDLE_SOURCES_DIRECTORY).resolve()
        try:
            source.relative_to(sources_root)
        except ValueError as exc:
            raise QueryCoreError("bundle source path must be under sources/") from exc
        if not source.is_file() or source.is_symlink():
            raise QueryCoreError(
                f"project bundle source is missing or is not a regular file: {identity}"
            )
        if _sha256(source) != source_sha:
            raise QueryCoreError(f"project bundle source SHA-256 mismatch: {identity}")
        expected_database_documents[identity] = (document_id, source_sha, page_count)

    actual_database_documents = {
        identity: (document_id, source_sha256, page_count)
        for identity, (document_id, source_sha256, page_count, min_page, max_page) in _database_documents(database).items()
        if page_count >= 1 and min_page == 1 and max_page == page_count
    }
    if actual_database_documents != expected_database_documents:
        raise QueryCoreError("project bundle database/document map mismatch")
    return manifest


def project_bundle_database(bundle_directory: Path) -> Path:
    """Return the validated project.sqlite path for a portable project bundle."""
    manifest = inspect_pdf_project_bundle(bundle_directory)
    return _inside_bundle(
        Path(bundle_directory).resolve(), manifest["database"]["path"], "bundle database path"
    )
