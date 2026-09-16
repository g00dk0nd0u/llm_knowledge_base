"""Importer for the Revit-independent export-time snapshot contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .build import build_database
from .errors import QueryCoreError

SNAPSHOT_VERSION = 1
SCHEMA_PATH = Path(__file__).parents[2] / "schema" / "revit_snapshot_v1.schema.json"


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QueryCoreError(f"invalid Revit snapshot JSON: {error}") from error
    if snapshot.get("snapshot_version") != SNAPSHOT_VERSION:
        raise QueryCoreError(
            f"unsupported snapshot version: {snapshot.get('snapshot_version')}"
        )
    if (
        snapshot.get("coordinate_system") != "host_revit_internal_origin"
        or snapshot.get("unit") != "mm"
    ):
        raise QueryCoreError("invalid Revit snapshot canonical geometry convention")
    if not isinstance(snapshot.get("project"), dict) or not isinstance(
        snapshot.get("records"), dict
    ):
        raise QueryCoreError("invalid Revit snapshot structure")
    return snapshot


def import_snapshot(path: Path, output: Path) -> Path:
    """Validate a temporary snapshot, then delegate SQLite ownership to Query Core."""
    snapshot = load_snapshot(path)
    records = dict(snapshot["records"])
    records.update(snapshot["project"])
    return build_database(records, output)
