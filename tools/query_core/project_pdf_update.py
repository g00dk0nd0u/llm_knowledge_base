"""Incremental updates and read-only diagnostics for PDF project Query Core databases."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .build import GENERATOR_VERSION, SCHEMA_VERSION, _insert_records
from .errors import QueryCoreError
from .pdf_adapter import _project_id, _sha256, _validate_output_location
from .project_pdf_adapter import (
    PIPELINE_VERSION,
    _manifest,
    _records_for_manifest_entry,
)
from .query import validate_database

_CREATED_FROM = "pdf-pipeline/1"
_RELEVANT_REQUIRES_EMPTY = (
    "source_models",
    "link_instances",
    "sheets",
    "views",
    "viewports",
    "levels",
    "spaces",
    "element_types",
    "elements",
    "parameters",
    "relationships",
    "annotations",
    "annotation_segments",
    "annotation_references",
    "entity_appearances",
    "spatial_boundaries",
    "spatial_boundary_segments",
    "geometries",
    "geometry_rtree",
)


@dataclass(frozen=True)
class ProjectUpdateResult:
    database: Path
    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "database": str(self.database),
            "added": list(self.added),
            "changed": list(self.changed),
            "removed": list(self.removed),
            "unchanged": list(self.unchanged),
        }


@dataclass(frozen=True)
class ProjectDocumentChange:
    identity: str
    previous_sha256: str | None
    current_sha256: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "previous_sha256": self.previous_sha256,
            "current_sha256": self.current_sha256,
        }


@dataclass(frozen=True)
class DuplicateByteGroup:
    source_sha256: str
    identities: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_sha256": self.source_sha256,
            "identities": list(self.identities),
        }


@dataclass(frozen=True)
class ProjectComparisonReport:
    database: Path
    project_id: str
    added: tuple[ProjectDocumentChange, ...]
    changed: tuple[ProjectDocumentChange, ...]
    removed: tuple[ProjectDocumentChange, ...]
    unchanged: tuple[ProjectDocumentChange, ...]
    duplicate_byte_groups: tuple[DuplicateByteGroup, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "database": str(self.database),
            "project_id": self.project_id,
            "counts": {
                "added": len(self.added),
                "changed": len(self.changed),
                "removed": len(self.removed),
                "unchanged": len(self.unchanged),
                "duplicate_byte_groups": len(self.duplicate_byte_groups),
            },
            "added": [item.as_dict() for item in self.added],
            "changed": [item.as_dict() for item in self.changed],
            "removed": [item.as_dict() for item in self.removed],
            "unchanged": [item.as_dict() for item in self.unchanged],
            "duplicate_byte_groups": [
                group.as_dict() for group in self.duplicate_byte_groups
            ],
        }


@dataclass(frozen=True)
class _PreviousDocument:
    document_id: str
    source_sha256: str
    page_count: int


def _expected_document_id(identity: str) -> str:
    return "doc-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueryCoreError(f"invalid {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise QueryCoreError(f"{label} must be a JSON object: {path}")
    return value


def _validate_cached_pdf_rows(connection: sqlite3.Connection) -> None:
    """Require the previous PDF-native cache to be internally canonical."""
    unexpected_search = connection.execute(
        "SELECT s.record_kind,s.record_id FROM search_content s "
        "LEFT JOIN pdf_text_blocks b ON b.id=s.record_id "
        "WHERE s.record_kind<>'pdf_text_block' OR b.id IS NULL OR b.text='' "
        "OR s.content<>b.text LIMIT 1"
    ).fetchone()
    if unexpected_search is not None:
        raise QueryCoreError(
            "incremental base has noncanonical cached search_content rows"
        )
    missing_search = connection.execute(
        "SELECT b.id FROM pdf_text_blocks b "
        "LEFT JOIN search_content s ON s.record_kind='pdf_text_block' "
        "AND s.record_id=b.id "
        "WHERE b.text<>'' AND s.record_id IS NULL LIMIT 1"
    ).fetchone()
    if missing_search is not None:
        raise QueryCoreError(
            "incremental base is missing cached search_content rows"
        )
    duplicate_evidence = connection.execute(
        "SELECT evidence_id FROM pdf_text_blocks GROUP BY evidence_id "
        "HAVING count(*)<>1 LIMIT 1"
    ).fetchone()
    if duplicate_evidence is not None:
        raise QueryCoreError(
            "incremental base has noncanonical PDF block evidence mapping"
        )
    orphan_evidence = connection.execute(
        "SELECT e.id FROM evidence e "
        "LEFT JOIN pdf_text_blocks b ON b.evidence_id=e.id "
        "WHERE b.id IS NULL LIMIT 1"
    ).fetchone()
    if orphan_evidence is not None:
        raise QueryCoreError(
            "incremental base has noncanonical unreferenced PDF evidence"
        )
    mismatched_evidence = connection.execute(
        "SELECT b.id FROM pdf_text_blocks b "
        "JOIN pdf_pages p ON p.id=b.page_id "
        "JOIN evidence e ON e.id=b.evidence_id "
        "WHERE e.document_id<>p.document_id OR e.pdf_page<>p.page_number "
        "OR e.coordinate_space IS NOT b.coordinate_space "
        "OR e.x_min IS NOT b.x_min OR e.y_min IS NOT b.y_min "
        "OR e.x_max IS NOT b.x_max OR e.y_max IS NOT b.y_max LIMIT 1"
    ).fetchone()
    if mismatched_evidence is not None:
        raise QueryCoreError(
            "incremental base has inconsistent PDF block evidence"
        )


def _validate_fts_integrity(connection: sqlite3.Connection) -> None:
    """Verify FTS5 index contents against the external search_content table."""
    try:
        connection.execute(
            "INSERT INTO search_fts(search_fts,rank) VALUES('integrity-check',1)"
        )
    except sqlite3.DatabaseError as exc:
        raise QueryCoreError(
            "incremental update produced inconsistent FTS5 search index"
        ) from exc


def _previous_documents(
    database: Path, project_id: str
) -> dict[str, _PreviousDocument]:
    metadata = validate_database(database)
    required_metadata = {
        "binding_mode": "project",
        "project_id": project_id,
        "created_from": _CREATED_FROM,
        "schema_version": str(SCHEMA_VERSION),
        "generator_version": GENERATOR_VERSION,
    }
    for key, expected in required_metadata.items():
        if metadata.get(key) != expected:
            raise QueryCoreError(
                f"incremental base metadata {key} mismatch: "
                f"expected {expected}, got {metadata.get(key)}"
            )

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{database.resolve()}?mode=ro", uri=True
        )
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise QueryCoreError("incremental base has foreign-key violations")
        for table in _RELEVANT_REQUIRES_EMPTY:
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise QueryCoreError(
                    "incremental base is not a PDF-native project Query Core: "
                    f"{table} contains rows"
                )
        _validate_cached_pdf_rows(connection)
        page_counts = dict(
            connection.execute(
                "SELECT document_id,count(*) FROM pdf_pages GROUP BY document_id"
            )
        )
        documents: dict[str, _PreviousDocument] = {}
        for document_id, identity, source_sha256 in connection.execute(
            "SELECT id,identity,source_sha256 FROM documents ORDER BY identity"
        ):
            if _project_id(identity) != project_id:
                raise QueryCoreError(
                    "incremental base document belongs to another project: " + identity
                )
            if document_id != _expected_document_id(identity):
                raise QueryCoreError(
                    "incremental base document_id does not match logical identity: "
                    + identity
                )
            count = page_counts.get(document_id, 0)
            if count < 1:
                raise QueryCoreError(
                    "incremental base document has no PDF pages: " + identity
                )
            documents[identity] = _PreviousDocument(
                document_id=document_id,
                source_sha256=source_sha256,
                page_count=count,
            )
        return documents
    except sqlite3.Error as exc:
        raise QueryCoreError(f"invalid incremental base database: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def _validate_current_entry(
    root: Path, project_id: str, entry: dict[str, Any]
) -> None:
    identity = entry["source_file"]
    expected_document_id = _expected_document_id(identity)
    if entry["document_id"] != expected_document_id:
        raise QueryCoreError(
            f"manifest document_id does not match logical identity: {identity}"
        )
    source_hash = entry["source_sha256"]
    if (
        len(source_hash) != 64
        or any(character not in "0123456789abcdef" for character in source_hash)
    ):
        raise QueryCoreError(f"manifest source_sha256 is invalid: {identity}")
    source = (root / identity).resolve()
    if _sha256(source) != source_hash:
        raise QueryCoreError(f"source PDF byte SHA-256 mismatch: {identity}")

    document = _load_json_object(
        root / entry["knowledge_path"] / "document.json", "PDF Pipeline document"
    )
    expected = {
        "document_id": entry["document_id"],
        "source_file": identity,
        "source_sha256": source_hash,
        "project_id": project_id,
        "page_count": entry["page_count"],
        "pipeline_version": PIPELINE_VERSION,
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise QueryCoreError(f"document metadata {key} mismatch for {identity}")
    pages = document.get("pages")
    page_count = entry["page_count"]
    if (
        not isinstance(page_count, int)
        or isinstance(page_count, bool)
        or page_count < 1
        or not isinstance(pages, list)
        or len(pages) != page_count
    ):
        raise QueryCoreError(f"document page count mismatch for {identity}")
    for number, summary in enumerate(pages, 1):
        if (
            not isinstance(summary, dict)
            or summary.get("page") != number
            or summary.get("structured_page") != f"pages/p{number:04d}.json"
        ):
            raise QueryCoreError(
                f"document page summary mismatch for {identity} page {number}"
            )


def _validate_unchanged_entry(
    root: Path,
    project_id: str,
    entry: dict[str, Any],
    previous: _PreviousDocument,
) -> None:
    _validate_current_entry(root, project_id, entry)
    identity = entry["source_file"]
    if previous.document_id != entry["document_id"]:
        raise QueryCoreError(
            f"incremental base document_id mismatch for unchanged document: {identity}"
        )
    if previous.page_count != entry["page_count"]:
        raise QueryCoreError(
            f"incremental base page count mismatch for unchanged document: {identity}"
        )


def _duplicate_groups(current: dict[str, dict[str, Any]]) -> tuple[DuplicateByteGroup, ...]:
    grouped: dict[str, list[str]] = {}
    for identity, entry in current.items():
        grouped.setdefault(entry["source_sha256"], []).append(identity)
    return tuple(
        DuplicateByteGroup(source_sha256=source_sha256, identities=tuple(sorted(identities)))
        for source_sha256, identities in sorted(grouped.items())
        if len(identities) > 1
    )


def _compare_with_entries(
    root: Path,
    project_id: str,
    entries: list[dict[str, Any]],
    database: Path,
) -> tuple[
    ProjectComparisonReport,
    dict[str, dict[str, Any]],
    dict[str, _PreviousDocument],
]:
    database = Path(database)
    if not database.is_file():
        raise QueryCoreError(
            "incremental base database does not exist; run build-pdf-project first"
        )
    previous = _previous_documents(database, project_id)
    current = {entry["source_file"]: entry for entry in entries}
    for entry in entries:
        _validate_current_entry(root, project_id, entry)

    previous_identities = set(previous)
    current_identities = set(current)
    added_identities = sorted(current_identities - previous_identities)
    removed_identities = sorted(previous_identities - current_identities)
    shared = current_identities & previous_identities
    changed_identities = sorted(
        identity
        for identity in shared
        if current[identity]["source_sha256"] != previous[identity].source_sha256
    )
    changed_set = set(changed_identities)
    unchanged_identities = sorted(shared - changed_set)

    for identity in unchanged_identities:
        _validate_unchanged_entry(
            root, project_id, current[identity], previous[identity]
        )

    report = ProjectComparisonReport(
        database=database,
        project_id=project_id,
        added=tuple(
            ProjectDocumentChange(
                identity=identity,
                previous_sha256=None,
                current_sha256=current[identity]["source_sha256"],
            )
            for identity in added_identities
        ),
        changed=tuple(
            ProjectDocumentChange(
                identity=identity,
                previous_sha256=previous[identity].source_sha256,
                current_sha256=current[identity]["source_sha256"],
            )
            for identity in changed_identities
        ),
        removed=tuple(
            ProjectDocumentChange(
                identity=identity,
                previous_sha256=previous[identity].source_sha256,
                current_sha256=None,
            )
            for identity in removed_identities
        ),
        unchanged=tuple(
            ProjectDocumentChange(
                identity=identity,
                previous_sha256=previous[identity].source_sha256,
                current_sha256=current[identity]["source_sha256"],
            )
            for identity in unchanged_identities
        ),
        duplicate_byte_groups=_duplicate_groups(current),
    )
    return report, current, previous


def compare_pdf_project_database(
    repo_root: Path, project_directory: Path, database: Path
) -> ProjectComparisonReport:
    """Compare current project inputs with a prior project Query Core without mutation."""
    root = Path(repo_root).resolve()
    project_id, entries = _manifest(root, project_directory)
    report, _current, _previous = _compare_with_entries(
        root, project_id, entries, Path(database)
    )
    return report


def _delete_document(connection: sqlite3.Connection, identity: str) -> None:
    row = connection.execute(
        "SELECT id FROM documents WHERE identity=?", (identity,)
    ).fetchone()
    if row is None:
        raise QueryCoreError(f"incremental base document is missing: {identity}")
    document_id = row[0]
    connection.execute(
        "DELETE FROM search_content WHERE record_kind='pdf_text_block' "
        "AND record_id IN ("
        "SELECT b.id FROM pdf_text_blocks b "
        "JOIN pdf_pages p ON p.id=b.page_id WHERE p.document_id=?"
        ")",
        (document_id,),
    )
    connection.execute("DELETE FROM pdf_pages WHERE document_id=?", (document_id,))
    connection.execute("DELETE FROM evidence WHERE document_id=?", (document_id,))
    deleted = connection.execute(
        "DELETE FROM documents WHERE id=?", (document_id,)
    ).rowcount
    if deleted != 1:
        raise QueryCoreError(f"failed to delete incremental document: {identity}")


def _backup_database(source_path: Path, destination_path: Path) -> None:
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(
            f"file:{source_path.resolve()}?mode=ro", uri=True
        )
        destination = sqlite3.connect(destination_path)
        source.backup(destination)
        destination.commit()
    except sqlite3.Error as exc:
        raise QueryCoreError(f"failed to snapshot incremental base database: {exc}") from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()


def _assert_snapshot_matches_manifest(
    database: Path, entries: list[dict[str, Any]]
) -> None:
    expected = {
        entry["source_file"]: (
            entry["document_id"],
            entry["source_sha256"],
            entry["page_count"],
        )
        for entry in entries
    }
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database)
        actual = {
            identity: (document_id, source_sha256, page_count)
            for document_id, identity, source_sha256, page_count in connection.execute(
                "SELECT d.id,d.identity,d.source_sha256,count(p.id) "
                "FROM documents d LEFT JOIN pdf_pages p ON p.document_id=d.id "
                "GROUP BY d.id,d.identity,d.source_sha256"
            )
        }
    except sqlite3.Error as exc:
        raise QueryCoreError(f"failed to verify incremental snapshot: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
    if actual != expected:
        raise QueryCoreError("incremental snapshot does not match current project manifest")


def update_pdf_project_database(
    repo_root: Path, project_directory: Path, database: Path
) -> ProjectUpdateResult:
    """Atomically update one validated project DB, reusing unchanged document rows."""
    root, database = Path(repo_root).resolve(), Path(database)
    project_id, entries = _manifest(root, project_directory)
    project = (root / "projects" / project_id).resolve()
    _validate_output_location(
        database,
        protected_files=(project / "manifest.json",),
        protected_directories=(project / "source", project / "knowledge"),
    )
    report, current, _previous = _compare_with_entries(
        root, project_id, entries, database
    )
    added = tuple(item.identity for item in report.added)
    changed = tuple(item.identity for item in report.changed)
    removed = tuple(item.identity for item in report.removed)
    unchanged = tuple(item.identity for item in report.unchanged)

    result = ProjectUpdateResult(
        database=database,
        added=added,
        changed=changed,
        removed=removed,
        unchanged=unchanged,
    )
    if not (added or changed or removed):
        return result

    database.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{database.name}.update.", dir=database.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    connection: sqlite3.Connection | None = None
    try:
        _backup_database(database, temporary)
        connection = sqlite3.connect(temporary)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for identity in (*removed, *changed):
            _delete_document(connection, identity)
        for identity in (*added, *changed):
            _insert_records(
                connection, _records_for_manifest_entry(root, current[identity])
            )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise QueryCoreError(f"SQLite integrity check failed: {integrity}")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise QueryCoreError("incremental update produced foreign-key violations")
        _validate_cached_pdf_rows(connection)
        _validate_fts_integrity(connection)
        connection.close()
        connection = None
        validate_database(temporary)
        _assert_snapshot_matches_manifest(temporary, entries)
        os.replace(temporary, database)
        return result
    except QueryCoreError:
        if connection is not None:
            connection.rollback()
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError, OSError) as exc:
        if connection is not None:
            connection.rollback()
        raise QueryCoreError(f"failed to update project query database: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
