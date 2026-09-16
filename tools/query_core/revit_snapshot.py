"""Importer for the Revit-independent export-time snapshot contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

from .build import build_database
from .errors import QueryCoreError
from .package import package_pdf

SNAPSHOT_VERSION = 1
SCHEMA_PATH = Path(__file__).parents[2] / "schema" / "revit_snapshot_v1.schema.json"


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QueryCoreError(f"invalid Revit snapshot JSON: {error}") from error
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        validator.check_schema(schema)
        validator.validate(snapshot)
    except (OSError, json.JSONDecodeError, jsonschema.SchemaError) as error:
        raise QueryCoreError(f"invalid Revit snapshot schema: {error}") from error
    except jsonschema.ValidationError as error:
        location = "$" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}"
            for item in error.absolute_path
        )
        if list(error.absolute_path) == ["snapshot_version"]:
            raise QueryCoreError(
                f"unsupported snapshot version at {location}: {snapshot.get('snapshot_version')}"
            ) from error
        raise QueryCoreError(
            f"invalid Revit snapshot at {location}: {error.message}"
        ) from error
    return snapshot


def import_snapshot(path: Path, output: Path) -> Path:
    """Validate a temporary snapshot, then delegate SQLite ownership to Query Core."""
    snapshot = load_snapshot(path)
    records = dict(snapshot["records"])
    records.update(snapshot["project"])
    return build_database(records, output)


def finalize_revit_export(export_directory: Path, output: Path | None = None) -> Path:
    """Validate and package one completed, immutable Revit export run."""
    directory = Path(export_directory)
    drawing = directory / "drawing.pdf"
    snapshot_path = directory / "revit_snapshot.json"
    if not drawing.is_file() or not snapshot_path.is_file():
        raise QueryCoreError(
            "Revit export must contain drawing.pdf and revit_snapshot.json"
        )
    snapshot = load_snapshot(snapshot_path)
    actual_sha = hashlib.sha256(drawing.read_bytes()).hexdigest()
    expected_sha = snapshot["project"]["source_document_sha256"]
    if actual_sha != expected_sha:
        raise QueryCoreError("drawing.pdf SHA-256 does not match Revit snapshot")
    database = directory / "project.sqlite"
    enhanced = Path(output) if output is not None else directory / "enhanced.pdf"
    if enhanced.resolve() in {drawing.resolve(), snapshot_path.resolve()}:
        raise QueryCoreError("final output must not overwrite Revit export inputs")
    import_snapshot(snapshot_path, database)
    return package_pdf(drawing, database, enhanced)
