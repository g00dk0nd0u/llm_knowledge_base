"""Importer for the Revit-independent export-time snapshot contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .build import build_database
from .errors import QueryCoreError

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
