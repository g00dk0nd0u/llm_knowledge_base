from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .errors import QueryCoreError

SCHEMA_VERSION = 1
GENERATOR_VERSION = "query-core/1.0"
TABLES = (
    "documents",
    "sheets",
    "views",
    "viewports",
    "levels",
    "spaces",
    "element_types",
    "elements",
    "evidence",
    "parameters",
    "relationships",
    "annotations",
    "geometries",
    "search_content",
)


def _validate(records: dict[str, Any]) -> None:
    required = {
        "project_id",
        "created_from",
        "source_document_identity",
        "source_document_sha256",
    }
    missing = sorted(required - records.keys())
    if missing:
        raise QueryCoreError(f"missing metadata: {', '.join(missing)}")
    sha = records["source_document_sha256"]
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(c not in "0123456789abcdef" for c in sha)
    ):
        raise QueryCoreError("source_document_sha256 must be a lowercase SHA-256")
    for row in records.get("evidence", []):
        coords = [row.get(k) for k in ("x_min", "y_min", "x_max", "y_max")]
        if any(v is not None for v in coords) and (
            any(v is None for v in coords)
            or coords[0] > coords[2]
            or coords[1] > coords[3]
        ):
            raise QueryCoreError(f"malformed evidence bbox: {row.get('id')}")
    for table in ("parameters", "annotations"):
        for row in records.get(table, []):
            if row.get("numeric_value") is not None and not row.get("unit"):
                raise QueryCoreError(f"numeric record requires unit: {row.get('id')}")


def build_database(records: dict[str, Any], output: Path) -> Path:
    """Atomically build schema v1 from canonical records sorted by stable ID."""
    _validate(records)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(
            (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
        )
        metadata = {
            "schema_version": str(SCHEMA_VERSION),
            "generator_version": GENERATOR_VERSION,
            "project_id": records["project_id"],
            "created_from": records["created_from"],
            "source_document_identity": records["source_document_identity"],
            "source_document_sha256": records["source_document_sha256"],
        }
        connection.executemany(
            "INSERT INTO metadata VALUES (?,?)", sorted(metadata.items())
        )
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        for table in TABLES:
            rows = sorted(
                records.get(table, []),
                key=lambda row: (str(row.get("id", "")), str(row.get("record_id", ""))),
            )
            for row in rows:
                values = dict(row)
                if table == "geometries":
                    values["geometry_json"] = json.dumps(
                        values["geometry"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    del values["geometry"]
                columns = ",".join(values)
                placeholders = ",".join("?" for _ in values)
                connection.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                    tuple(values.values()),
                )
        connection.commit()
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        connection.close()
        if result != "ok":
            raise QueryCoreError(f"SQLite integrity check failed: {result}")
        os.replace(temporary, output)
        return output
    except (sqlite3.Error, KeyError, TypeError, ValueError) as error:
        raise QueryCoreError(f"failed to build query database: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
