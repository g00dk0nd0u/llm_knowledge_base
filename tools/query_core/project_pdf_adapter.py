"""Deterministic project adapter for already-processed PDF Pipeline v1 output."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .build import (
    GENERATOR_VERSION,
    SCHEMA_VERSION,
    _initialize_database,
    _insert_records,
)
from .errors import QueryCoreError
from .pdf_adapter import _validate_output_location, records_from_pdf_pipeline
from .query import validate_database

PIPELINE_VERSION = "1"


def _logical_path(value: Any, prefix: tuple[str, ...], label: str) -> str:
    if not isinstance(value, str):
        raise QueryCoreError(f"{label} must be a canonical repository-relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or len(path.parts) <= len(prefix)
        or path.parts[: len(prefix)] != prefix
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise QueryCoreError(f"{label} must be under {'/'.join(prefix)}/")
    return value


def _inside(path: Path, parent: Path, label: str) -> Path:
    resolved, expected = path.resolve(), parent.resolve()
    try:
        resolved.relative_to(expected)
    except ValueError as exc:
        raise QueryCoreError(f"{label} resolves outside {expected}") from exc
    return resolved


def _manifest(
    repo_root: Path, project_directory: Path
) -> tuple[str, list[dict[str, Any]]]:
    root = Path(repo_root).resolve()
    project = Path(project_directory)
    if not project.is_absolute():
        logical_project = PurePosixPath(project.as_posix())
        if (
            logical_project.as_posix() != project.as_posix()
            or len(logical_project.parts) != 2
            or logical_project.parts[0] != "projects"
            or logical_project.parts[1] in {"", ".", ".."}
        ):
            raise QueryCoreError("project directory must match projects/<project-id>")
        project = root / project
    project = _inside(project, root / "projects", "project directory")
    relative = project.relative_to(root).parts
    if len(relative) != 2 or relative[0] != "projects" or not relative[1]:
        raise QueryCoreError("project directory must match projects/<project-id>")
    project_id = relative[1]
    try:
        manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueryCoreError(f"invalid project manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise QueryCoreError("project manifest must be a JSON object")
    if manifest.get("project_id") != project_id:
        raise QueryCoreError("manifest project_id does not match project directory")
    if manifest.get("pipeline_version") != PIPELINE_VERSION:
        raise QueryCoreError("manifest pipeline_version must be 1")
    entries = manifest.get("documents")
    if not isinstance(entries, list):
        raise QueryCoreError("manifest documents must be a list")
    seen = {key: set() for key in ("document_id", "source_file", "knowledge_path")}
    required = {
        "document_id": str,
        "source_file": str,
        "source_sha256": str,
        "knowledge_path": str,
        "page_count": int,
        "pipeline_version": str,
    }
    checked: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise QueryCoreError(f"manifest document {index} must be an object")
        for key, kind in required.items():
            value = entry.get(key)
            if not isinstance(value, kind) or isinstance(value, bool):
                raise QueryCoreError(f"manifest document {index} has invalid {key}")
        if entry["pipeline_version"] != PIPELINE_VERSION:
            raise QueryCoreError(f"manifest document {index} pipeline_version must be 1")
        _logical_path(
            entry["source_file"],
            ("projects", project_id, "source"),
            "source_file",
        )
        _logical_path(
            entry["knowledge_path"],
            ("projects", project_id, "knowledge"),
            "knowledge_path",
        )
        source = _inside(
            root / entry["source_file"], project / "source", "source_file"
        )
        knowledge = _inside(
            root / entry["knowledge_path"],
            project / "knowledge",
            "knowledge_path",
        )
        if not source.is_file():
            raise QueryCoreError(f"source PDF does not exist: {entry['source_file']}")
        if not knowledge.is_dir():
            raise QueryCoreError(
                f"knowledge directory does not exist: {entry['knowledge_path']}"
            )
        if not (knowledge / ".pdf-pipeline-v1").is_file():
            raise QueryCoreError(
                f"knowledge directory is unowned: {entry['knowledge_path']}"
            )
        for key in seen:
            if entry[key] in seen[key]:
                raise QueryCoreError(f"duplicate manifest {key}: {entry[key]}")
            seen[key].add(entry[key])
        checked.append(entry)
    return project_id, sorted(checked, key=lambda item: item["source_file"])


def _records_for_manifest_entry(
    repo_root: Path, entry: dict[str, Any]
) -> dict[str, Any]:
    """Load one strict Phase 1B bundle and cross-check its manifest entry."""
    root = Path(repo_root).resolve()
    records = records_from_pdf_pipeline(root, root / entry["knowledge_path"])
    documents = records.get("documents", [])
    if len(documents) != 1:
        raise QueryCoreError(
            f"PDF Pipeline bundle must contain exactly one document: {entry['source_file']}"
        )
    document = documents[0]
    checks = {
        "document_id": document["id"],
        "source_file": document["identity"],
        "source_sha256": document["source_sha256"],
        "page_count": len(records["pdf_pages"]),
    }
    for key, actual in checks.items():
        if entry[key] != actual:
            raise QueryCoreError(
                f"manifest/document {key} mismatch for {entry['source_file']}"
            )
    return records


def build_pdf_project_database(
    repo_root: Path, project_directory: Path, output: Path
) -> Path:
    """Build one fresh project snapshot, materializing one document bundle at a time."""
    root, output = Path(repo_root).resolve(), Path(output)
    project_id, entries = _manifest(root, project_directory)
    project = (root / "projects" / project_id).resolve()
    _validate_output_location(
        output,
        protected_files=(project / "manifest.json",),
        protected_directories=(project / "source", project / "knowledge"),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        _initialize_database(
            connection,
            {
                "schema_version": str(SCHEMA_VERSION),
                "generator_version": GENERATOR_VERSION,
                "project_id": project_id,
                "created_from": "pdf-pipeline/1",
                "binding_mode": "project",
            },
        )
        for entry in entries:
            _insert_records(connection, _records_for_manifest_entry(root, entry))
        connection.commit()
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        connection.close()
        connection = None
        if result != "ok":
            raise QueryCoreError(f"SQLite integrity check failed: {result}")
        validate_database(temporary)
        os.replace(temporary, output)
        return output
    except QueryCoreError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError, OSError) as exc:
        raise QueryCoreError(f"failed to build project query database: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
