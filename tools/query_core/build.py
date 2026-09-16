from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .errors import QueryCoreError

SCHEMA_VERSION = 2
GENERATOR_VERSION = "query-core/2.0"
TABLES = (
    "documents",
    "source_models",
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
    "annotation_segments",
    "annotation_references",
    "entity_appearances",
    "spatial_boundaries",
    "spatial_boundary_segments",
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
    source_models = {row.get("id") for row in records.get("source_models", [])}
    for table in (
        "levels",
        "spaces",
        "element_types",
        "elements",
        "views",
        "sheets",
        "annotations",
    ):
        identities: set[tuple[str, str]] = set()
        for row in records.get(table, []):
            model, unique_id = row.get("source_model_id"), row.get("source_unique_id")
            if model is not None and model not in source_models:
                raise QueryCoreError(f"reference to nonexistent source model: {model}")
            if model is not None and unique_id is not None:
                identity = (model, unique_id)
                if identity in identities:
                    raise QueryCoreError(
                        f"duplicate source identity: {model}/{unique_id}"
                    )
                identities.add(identity)
    for row in records.get("source_models", []):
        if row.get("role") == "link":
            if row.get("host_source_model_id") not in source_models:
                raise QueryCoreError("linked source model references nonexistent host")
            _validate_transform(row.get("transform_to_host"), row.get("id"))
    for collection in ("evidence", "entity_appearances"):
        for row in records.get(collection, []):
            if not isinstance(row.get("pdf_page"), int) or row["pdf_page"] < 1:
                raise QueryCoreError(f"invalid appearance page: {row.get('id')}")
            coords = [row.get(k) for k in ("x_min", "y_min", "x_max", "y_max")]
            if any(v is not None for v in coords) and (
                any(v is None for v in coords)
                or coords[0] > coords[2]
                or coords[1] > coords[3]
            ):
                raise QueryCoreError(f"malformed {collection} bbox: {row.get('id')}")
    for row in records.get("evidence", []):
        coords = [row.get(k) for k in ("x_min", "y_min", "x_max", "y_max")]
        if any(v is not None for v in coords) and (
            any(v is None for v in coords)
            or coords[0] > coords[2]
            or coords[1] > coords[3]
        ):
            raise QueryCoreError(f"malformed evidence bbox: {row.get('id')}")
    for table in ("parameters", "annotations", "annotation_segments"):
        for row in records.get(table, []):
            if row.get("numeric_value") is not None and not row.get("unit"):
                raise QueryCoreError(f"numeric record requires unit: {row.get('id')}")
    for row in records.get("geometries", []):
        if (
            row.get("coordinate_system") != "host_revit_internal_origin"
            or row.get("unit") != "mm"
        ):
            raise QueryCoreError(
                "indexed geometry must use host_revit_internal_origin/mm"
            )
    for row in records.get("annotation_references", []):
        model = row.get("target_source_model_id")
        if model is not None and model not in source_models:
            raise QueryCoreError(f"reference to nonexistent source model: {model}")
    _validate_order(records, "annotation_segments", "annotation_id", "segment_index")
    _validate_order(
        records, "annotation_references", "annotation_id", "reference_index"
    )
    _validate_order(records, "spatial_boundaries", "space_id", "loop_index")
    _validate_order(
        records, "spatial_boundary_segments", "boundary_id", "segment_index"
    )
    boundary_segments: dict[str, list[dict[str, Any]]] = {}
    for row in records.get("spatial_boundary_segments", []):
        boundary_segments.setdefault(row["boundary_id"], []).append(row)
        model = row.get("source_model_id")
        if model is not None and model not in source_models:
            raise QueryCoreError(f"reference to nonexistent source model: {model}")
    for boundary in records.get("spatial_boundaries", []):
        segments = sorted(
            boundary_segments.get(boundary["id"], []), key=lambda r: r["segment_index"]
        )
        if len(segments) < 3 or any(
            [a["end_x"], a["end_y"], a["end_z"]]
            != [b["start_x"], b["start_y"], b["start_z"]]
            for a, b in zip(segments, segments[1:] + segments[:1])
        ):
            raise QueryCoreError(f"malformed spatial boundary loop: {boundary['id']}")


def _validate_order(
    records: dict[str, Any], table: str, parent_key: str, index_key: str
) -> None:
    grouped: dict[str, list[int]] = {}
    for row in records.get(table, []):
        grouped.setdefault(row[parent_key], []).append(row[index_key])
    for parent, indexes in grouped.items():
        if sorted(indexes) != list(range(len(indexes))):
            raise QueryCoreError(f"{table} ordering error for {parent}")


def _validate_transform(transform: Any, model_id: Any) -> None:
    keys = ("basis_x", "basis_y", "basis_z", "origin")
    if (
        not isinstance(transform, dict)
        or transform.get("source_unit") != "revit_internal"
        or any(
            not isinstance(transform.get(key), list)
            or len(transform[key]) != 3
            or any(not isinstance(value, (int, float)) for value in transform[key])
            for key in keys
        )
    ):
        raise QueryCoreError(f"invalid link transform: {model_id}")


def build_database(records: dict[str, Any], output: Path) -> Path:
    """Atomically build schema v2 from validated canonical records."""
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
                elif table == "source_models" and "transform_to_host" in values:
                    transform = values.pop("transform_to_host")
                    values["transform_to_host_json"] = (
                        json.dumps(transform, sort_keys=True, separators=(",", ":"))
                        if transform is not None
                        else None
                    )
                elif table == "viewports" and "sheet_to_pdf_transform" in values:
                    transform = values.pop("sheet_to_pdf_transform")
                    values["sheet_to_pdf_transform_json"] = (
                        json.dumps(transform, sort_keys=True, separators=(",", ":"))
                        if transform is not None
                        else None
                    )
                elif table == "annotation_segments":
                    for key in ("origin", "text_position"):
                        if key in values:
                            values[f"{key}_json"] = json.dumps(
                                values.pop(key), separators=(",", ":")
                            )
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
