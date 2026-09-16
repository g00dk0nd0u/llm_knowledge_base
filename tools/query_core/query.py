from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .errors import QueryCoreError

SCHEMA_VERSION = 2
REQUIRED_METADATA = {
    "schema_version",
    "generator_version",
    "project_id",
    "created_from",
    "source_document_identity",
    "source_document_sha256",
}
REQUIRED_VIRTUAL_TABLES = {"geometry_rtree", "search_fts"}
REQUIRED_COLUMNS = {
    "metadata": {"key", "value"},
    "documents": {"id", "identity", "title", "source_filename", "source_sha256"},
    "sheets": {"id", "document_id", "number", "name", "pdf_page"},
    "views": {"id", "document_id", "name", "view_type"},
    "viewports": {
        "id",
        "sheet_id",
        "view_id",
        "x_min",
        "y_min",
        "x_max",
        "y_max",
        "coordinate_space",
    },
    "levels": {
        "id",
        "name",
        "elevation",
        "unit",
        "source_id",
        "provenance",
        "confidence",
    },
    "spaces": {
        "id",
        "kind",
        "name",
        "number",
        "level_id",
        "source_id",
        "provenance",
        "confidence",
    },
    "element_types": {
        "id",
        "name",
        "category",
        "source_id",
        "provenance",
        "confidence",
    },
    "elements": {
        "id",
        "name",
        "category",
        "type_id",
        "space_id",
        "level_id",
        "source_id",
        "provenance",
        "confidence",
    },
    "evidence": {
        "id",
        "document_id",
        "sheet_id",
        "view_id",
        "pdf_page",
        "x_min",
        "y_min",
        "x_max",
        "y_max",
        "coordinate_space",
    },
    "parameters": {
        "id",
        "entity_kind",
        "entity_id",
        "name",
        "value_text",
        "numeric_value",
        "unit",
        "provenance",
        "confidence",
        "evidence_id",
    },
    "relationships": {
        "id",
        "source_kind",
        "source_id",
        "relation_type",
        "target_kind",
        "target_id",
        "provenance",
        "confidence",
        "evidence_id",
    },
    "annotations": {
        "id",
        "kind",
        "semantic_type",
        "display_text",
        "numeric_value",
        "unit",
        "related_entity_kind",
        "related_entity_id",
        "provenance",
        "confidence",
        "evidence_id",
    },
    "geometries": {
        "rowid",
        "id",
        "entity_kind",
        "entity_id",
        "geometry_type",
        "geometry_json",
        "coordinate_system",
        "unit",
        "min_x",
        "max_x",
        "min_y",
        "max_y",
        "min_z",
        "max_z",
        "provenance",
        "confidence",
        "evidence_id",
    },
    "search_content": {"rowid", "record_kind", "record_id", "content"},
    "geometry_rtree": {"rowid", "min_x", "max_x", "min_y", "max_y", "min_z", "max_z"},
    "search_fts": {"content"},
}
# v2 replaces the v1 identity and placement shapes and adds structured Revit data.
REQUIRED_COLUMNS.update(
    {
        "source_models": {
            "id",
            "role",
            "model_identity_kind",
            "model_identity",
            "transform_to_host_json",
        },
        "entity_appearances": {
            "id",
            "entity_kind",
            "entity_id",
            "pdf_page",
            "bbox_quality",
        },
        "annotation_segments": {
            "id",
            "annotation_id",
            "segment_index",
            "numeric_value",
            "display_text",
        },
        "annotation_references": {
            "id",
            "annotation_id",
            "reference_index",
            "target_source_model_id",
            "target_source_unique_id",
        },
        "spatial_boundaries": {
            "id",
            "space_id",
            "loop_index",
            "loop_kind",
            "coordinate_system",
            "unit",
        },
        "spatial_boundary_segments": {
            "id",
            "boundary_id",
            "segment_index",
            "start_x",
            "end_x",
        },
    }
)
# Old columns below are removed by v2; replace their validation sets.
REQUIRED_COLUMNS["sheets"] = {
    "id",
    "document_id",
    "number",
    "name",
    "pdf_page",
    "export_order",
    "source_model_id",
    "source_unique_id",
}
REQUIRED_COLUMNS["views"] = {
    "id",
    "document_id",
    "name",
    "view_type",
    "export_order",
    "source_model_id",
    "source_unique_id",
}
REQUIRED_COLUMNS["viewports"] = {
    "id",
    "sheet_id",
    "view_id",
    "placement_kind",
    "sheet_x_min",
    "pdf_x_min",
    "sheet_to_pdf_transform_json",
    "mapping_quality",
}
REQUIRED_COLUMNS["levels"] = {
    "id",
    "name",
    "elevation",
    "unit",
    "source_model_id",
    "source_unique_id",
    "provenance",
    "confidence",
}
REQUIRED_COLUMNS["spaces"] = {
    "id",
    "kind",
    "name",
    "number",
    "level_id",
    "source_model_id",
    "source_unique_id",
    "phase_source_unique_id",
    "provenance",
    "confidence",
}
REQUIRED_COLUMNS["element_types"] = {
    "id",
    "family_name",
    "type_name",
    "category",
    "source_model_id",
    "source_unique_id",
    "provenance",
    "confidence",
}
REQUIRED_COLUMNS["elements"] = {
    "id",
    "name",
    "category",
    "type_id",
    "space_id",
    "level_id",
    "source_model_id",
    "source_unique_id",
    "provenance",
    "confidence",
}
REQUIRED_COLUMNS["parameters"] = {
    "id",
    "entity_kind",
    "entity_id",
    "scope",
    "definition_name",
    "definition_key",
    "storage_type",
    "data_type_id",
    "parameter_type_id",
    "shared_parameter_guid",
    "unit_type_id",
    "raw_value_text",
    "raw_numeric_value",
    "numeric_value",
    "unit",
    "value_text",
    "provenance",
    "confidence",
    "evidence_id",
}
REQUIRED_COLUMNS["annotations"] = {
    "id",
    "kind",
    "display_text",
    "numeric_value",
    "unit",
    "source_model_id",
    "source_unique_id",
    "view_id",
    "evidence_id",
}
ENTITY_TABLES = {
    "level": "levels",
    "space": "spaces",
    "element_type": "element_types",
    "element": "elements",
}


def validate_database(path: Path) -> dict[str, str]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise QueryCoreError(f"SQLite integrity check failed: {integrity}")
        objects = {
            row["name"]: row
            for row in connection.execute(
                "SELECT name,type,sql FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        missing_tables = sorted(REQUIRED_COLUMNS.keys() - objects.keys())
        if missing_tables:
            raise QueryCoreError(
                f"incomplete Query Core v2 schema; missing tables: {', '.join(missing_tables)}"
            )
        invalid_virtual_tables = sorted(
            table
            for table in REQUIRED_VIRTUAL_TABLES
            if not (objects[table]["sql"] or "")
            .lstrip()
            .upper()
            .startswith("CREATE VIRTUAL TABLE")
        )
        if invalid_virtual_tables:
            raise QueryCoreError(
                "incompatible Query Core v2 schema; expected virtual tables: "
                + ", ".join(invalid_virtual_tables)
            )
        for table, required in REQUIRED_COLUMNS.items():
            actual = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({json.dumps(table)})")
            }
            missing_columns = sorted(required - actual)
            if missing_columns:
                raise QueryCoreError(
                    f"incompatible Query Core v2 schema; {table} missing columns: "
                    + ", ".join(missing_columns)
                )
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        missing_metadata = sorted(REQUIRED_METADATA - metadata.keys())
        if missing_metadata:
            raise QueryCoreError(
                f"payload missing required metadata: {', '.join(missing_metadata)}"
            )
    except sqlite3.Error as error:
        raise QueryCoreError(f"invalid SQLite payload: {error}") from error
    finally:
        if connection is not None:
            connection.close()
    try:
        version = int(metadata["schema_version"])
    except (TypeError, ValueError) as error:
        raise QueryCoreError("payload has no valid schema version") from error
    if version != SCHEMA_VERSION or user_version != SCHEMA_VERSION:
        raise QueryCoreError(
            f"unsupported schema version: metadata={version}, user_version={user_version}"
        )
    for key in REQUIRED_METADATA - {"schema_version"}:
        if not isinstance(metadata[key], str) or not metadata[key].strip():
            raise QueryCoreError(f"payload metadata {key} must be a non-empty string")
    source_hash = metadata["source_document_sha256"]
    if len(source_hash) != 64 or any(
        character not in "0123456789abcdef" for character in source_hash
    ):
        raise QueryCoreError(
            "payload source_document_sha256 must be a lowercase SHA-256"
        )
    return metadata


class QueryCore:
    """Structured, read-only API over a validated Query Core v2 database."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.metadata = validate_database(self.path)
        self.connection = sqlite3.connect(
            f"file:{self.path.resolve()}?mode=ro", uri=True
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "QueryCore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _rows(self, sql: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(sql, values)]

    def search_text(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        # FTS5 unicode61 does not split unspaced Japanese consistently. Substring
        # fallback preserves predictable Japanese lookup without external tokenizers.
        try:
            rows = self._rows(
                "SELECT s.record_kind,s.record_id,s.content FROM search_fts f JOIN search_content s ON s.rowid=f.rowid WHERE search_fts MATCH ? ORDER BY rank,s.record_kind,s.record_id LIMIT ?",
                (query, limit),
            )
        except sqlite3.OperationalError:
            rows = []
        if rows:
            return rows
        return self._rows(
            "SELECT record_kind,record_id,content FROM search_content WHERE instr(lower(content),lower(?))>0 ORDER BY record_kind,record_id LIMIT ?",
            (query, limit),
        )

    def get_entity(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        table = ENTITY_TABLES.get(kind)
        if not table:
            raise QueryCoreError(f"unknown entity kind: {kind}")
        rows = self._rows(f"SELECT * FROM {table} WHERE id=?", (entity_id,))
        return rows[0] if rows else None

    def get_source_model(self, model_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM source_models WHERE id=?", (model_id,))
        if not rows:
            return None
        row = rows[0]
        transform = row.pop("transform_to_host_json")
        row["transform_to_host"] = json.loads(transform) if transform else None
        return row

    def get_appearances(self, entity_kind: str, entity_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM entity_appearances WHERE entity_kind=? AND entity_id=? ORDER BY pdf_page,id",
            (entity_kind, entity_id),
        )

    def get_annotation_segments(self, annotation_id: str) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT * FROM annotation_segments WHERE annotation_id=? ORDER BY segment_index",
            (annotation_id,),
        )
        for row in rows:
            for key in ("origin", "text_position"):
                value = row.pop(f"{key}_json")
                row[key] = json.loads(value) if value else None
        return rows

    def get_annotation_references(self, annotation_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM annotation_references WHERE annotation_id=? ORDER BY reference_index",
            (annotation_id,),
        )

    def get_spatial_boundaries(self, space_id: str) -> list[dict[str, Any]]:
        boundaries = self._rows(
            "SELECT * FROM spatial_boundaries WHERE space_id=? ORDER BY loop_index",
            (space_id,),
        )
        for boundary in boundaries:
            boundary["segments"] = self._rows(
                "SELECT * FROM spatial_boundary_segments WHERE boundary_id=? ORDER BY segment_index",
                (boundary["id"],),
            )
        return boundaries

    def find_entities(self, text: str, kind: str | None = None) -> list[dict[str, Any]]:
        kinds = [kind] if kind else list(ENTITY_TABLES)
        output = []
        for entity_kind in kinds:
            table = ENTITY_TABLES.get(entity_kind)
            if not table:
                raise QueryCoreError(f"unknown entity kind: {entity_kind}")
            name_column = "type_name" if table == "element_types" else "name"
            for row in self._rows(
                f"SELECT * FROM {table} WHERE instr(lower({name_column}),lower(?))>0 ORDER BY id",
                (text,),
            ):
                output.append({"kind": entity_kind, **row})
        return output

    def get_pdf_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        rows = self._rows(
            "SELECT e.*,d.identity AS document_identity,d.source_filename,d.source_sha256,s.number AS sheet_number,s.name AS sheet_name,v.name AS view_name FROM evidence e JOIN documents d ON d.id=e.document_id LEFT JOIN sheets s ON s.id=e.sheet_id LEFT JOIN views v ON v.id=e.view_id WHERE e.id=?",
            (evidence_id,),
        )
        return rows[0] if rows else None

    def _with_evidence(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in rows:
            row["evidence"] = (
                self.get_pdf_evidence(row["evidence_id"])
                if row.get("evidence_id")
                else None
            )
        return rows

    def get_numeric_facts(
        self, entity_kind: str, entity_id: str
    ) -> list[dict[str, Any]]:
        return self._with_evidence(
            self._rows(
                "SELECT * FROM parameters WHERE entity_kind=? AND entity_id=? AND numeric_value IS NOT NULL ORDER BY definition_name,id",
                (entity_kind, entity_id),
            )
        )

    def _annotations(
        self, kind: str, entity_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM annotations WHERE kind=?"
        values: tuple[Any, ...] = (kind,)
        if entity_id is not None:
            sql += " AND related_entity_id=?"
            values += (entity_id,)
        return self._with_evidence(self._rows(sql + " ORDER BY id", values))

    def get_dimensions(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        return self._annotations("dimension", entity_id)

    def get_spot_elevations(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        return self._annotations("spot_elevation", entity_id)

    def get_related_entities(
        self, entity_kind: str, entity_id: str
    ) -> list[dict[str, Any]]:
        return self._with_evidence(
            self._rows(
                "SELECT * FROM relationships WHERE (source_kind=? AND source_id=?) OR (target_kind=? AND target_id=?) ORDER BY id",
                (entity_kind, entity_id, entity_kind, entity_id),
            )
        )

    def get_spatial_candidates(
        self, bounds: tuple[float, float, float, float, float, float]
    ) -> list[dict[str, Any]]:
        min_x, max_x, min_y, max_y, min_z, max_z = bounds
        if min_x > max_x or min_y > max_y or min_z > max_z:
            raise QueryCoreError("malformed spatial query bounds")
        rows = self._rows(
            "SELECT g.* FROM geometry_rtree r JOIN geometries g ON g.rowid=r.rowid WHERE r.max_x>=? AND r.min_x<=? AND r.max_y>=? AND r.min_y<=? AND r.max_z>=? AND r.min_z<=? ORDER BY g.id",
            (min_x, max_x, min_y, max_y, min_z, max_z),
        )
        for row in rows:
            row["geometry"] = json.loads(row.pop("geometry_json"))
        return self._with_evidence(rows)
