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
            "title",
            "revit_version",
            "model_identity_kind",
            "model_identity",
            "snapshot_version_guid",
            "snapshot_save_number",
        },
        "link_instances": {
            "id",
            "host_source_model_id",
            "linked_source_model_id",
            "source_unique_id",
            "name",
            "transform_to_host_json",
            "provenance",
        },
        "entity_appearances": {
            "id",
            "entity_kind",
            "entity_id",
            "sheet_id",
            "view_id",
            "viewport_id",
            "link_instance_id",
            "pdf_page",
            "x_min",
            "y_min",
            "x_max",
            "y_max",
            "coordinate_space",
            "appearance_kind",
            "bbox_quality",
            "provenance",
        },
        "annotation_segments": {
            "id",
            "annotation_id",
            "segment_index",
            "numeric_value",
            "display_text",
            "unit",
            "value_override",
            "prefix",
            "suffix",
            "above",
            "below",
            "origin_json",
            "text_position_json",
        },
        "annotation_references": {
            "id",
            "annotation_id",
            "reference_index",
            "target_source_model_id",
            "target_source_unique_id",
            "target_link_instance_id",
            "stable_reference",
            "reference_type",
            "is_linked",
            "resolution_state",
        },
        "spatial_boundaries": {
            "id",
            "space_id",
            "link_instance_id",
            "loop_index",
            "loop_kind",
            "coordinate_system",
            "unit",
            "provenance",
        },
        "spatial_boundary_segments": {
            "id",
            "boundary_id",
            "link_instance_id",
            "segment_index",
            "start_x",
            "start_y",
            "start_z",
            "end_x",
            "end_y",
            "end_z",
            "source_model_id",
            "source_unique_id",
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
    "sheet_y_min",
    "sheet_x_max",
    "sheet_y_max",
    "sheet_coordinate_unit",
    "pdf_x_min",
    "pdf_y_min",
    "pdf_x_max",
    "pdf_y_max",
    "pdf_coordinate_space",
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
REQUIRED_COLUMNS["geometries"].add("link_instance_id")
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
        return rows[0]

    def get_link_instance(self, link_instance_id: str) -> dict[str, Any] | None:
        rows = self._rows(
            "SELECT * FROM link_instances WHERE id=?", (link_instance_id,)
        )
        return self._decode_link_instance(rows[0]) if rows else None

    def get_link_instances(
        self, linked_source_model_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM link_instances"
        values: tuple[Any, ...] = ()
        if linked_source_model_id is not None:
            sql += " WHERE linked_source_model_id=?"
            values = (linked_source_model_id,)
        return [
            self._decode_link_instance(row)
            for row in self._rows(sql + " ORDER BY id", values)
        ]

    @staticmethod
    def _decode_link_instance(row: dict[str, Any]) -> dict[str, Any]:
        transform = row.pop("transform_to_host_json")
        row["transform_to_host"] = json.loads(transform)
        return row

    def get_appearances(self, entity_kind: str, entity_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM entity_appearances WHERE entity_kind=? AND entity_id=? ORDER BY pdf_page,id",
            (entity_kind, entity_id),
        )

    def get_occurrence_evidence(
        self, entity_kind: str, entity_id: str
    ) -> list[dict[str, Any]]:
        """Return drawing occurrences with resolved document, sheet, and view data."""
        rows = self._rows(
            "SELECT a.*,d.id AS document_id,d.identity AS document_identity,"
            "d.source_filename,s.number AS sheet_number,s.name AS sheet_name,"
            "v.name AS view_name FROM entity_appearances a "
            "JOIN sheets s ON s.id=a.sheet_id "
            "JOIN documents d ON d.id=s.document_id "
            "LEFT JOIN views v ON v.id=a.view_id "
            "WHERE a.entity_kind=? AND a.entity_id=? "
            "ORDER BY d.identity,a.pdf_page,s.number,v.name,a.id",
            (entity_kind, entity_id),
        )
        for row in rows:
            row["appearance_id"] = row.pop("id")
            row["document"] = {
                "id": row.pop("document_id"),
                "identity": row.pop("document_identity"),
                "source_filename": row.pop("source_filename"),
            }
            row["bbox"] = (
                [row.pop(key) for key in ("x_min", "y_min", "x_max", "y_max")]
                if all(row[key] is not None for key in ("x_min", "y_min", "x_max", "y_max"))
                else None
            )
            for key in ("x_min", "y_min", "x_max", "y_max"):
                row.pop(key, None)
        return rows

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
            "SELECT * FROM spatial_boundaries WHERE space_id=? ORDER BY link_instance_id,loop_index",
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
            matches = []
            for entity_kind in ENTITY_TABLES:
                entity = self.get_entity(entity_kind, entity_id)
                if entity is not None:
                    matches.append(entity)
            if len(matches) != 1:
                return []
            entity = matches[0]
            sql += (
                " AND (related_entity_id=? OR EXISTS ("
                "SELECT 1 FROM annotation_references r WHERE r.annotation_id=annotations.id "
                "AND r.resolution_state='resolved' AND r.target_source_model_id=? "
                "AND r.target_source_unique_id=?))"
            )
            values += (
                entity_id,
                entity.get("source_model_id"),
                entity.get("source_unique_id"),
            )
        rows = self._with_evidence(self._rows(sql + " ORDER BY id", values))
        for row in rows:
            row["annotation_references"] = self.get_annotation_references(row["id"])
        return rows

    def get_dimensions(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        return self._annotations("dimension", entity_id)

    def get_spot_elevations(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        return self._annotations("spot_elevation", entity_id)

    def get_opening_width(self, entity_id: str) -> dict[str, Any]:
        """Return an explicit opening-width fact and its compact PDF evidence."""
        from .practical import get_opening_width

        return get_opening_width(self, entity_id)

    def get_relative_elevation(
        self, entity_id: str, reference: str | None = None
    ) -> dict[str, Any]:
        """Return an explicit relative elevation and its compact PDF evidence."""
        from .practical import get_relative_elevation

        return get_relative_elevation(self, entity_id, reference)

    def get_related_entities(
        self, entity_kind: str, entity_id: str
    ) -> list[dict[str, Any]]:
        return self._with_evidence(
            self._rows(
                "SELECT * FROM relationships WHERE (source_kind=? AND source_id=?) OR (target_kind=? AND target_id=?) ORDER BY id",
                (entity_kind, entity_id, entity_kind, entity_id),
            )
        )

    def get_type_instances(self, type_id: str) -> list[dict[str, Any]]:
        """Return all elements that explicitly reference an element type."""
        return self._rows(
            "SELECT * FROM elements WHERE type_id=? ORDER BY id", (type_id,)
        )

    def get_related_spaces(self, entity_id: str) -> list[dict[str, Any]]:
        """Return explicit spatial relationships for an element, without inference."""
        rows = self._rows(
            "SELECT r.*,s.kind AS space_kind,s.name AS space_name,"
            "s.number AS space_number,s.level_id AS space_level_id,"
            "s.source_model_id AS space_source_model_id,"
            "s.source_unique_id AS space_source_unique_id,"
            "s.provenance AS space_provenance,s.confidence AS space_confidence "
            "FROM relationships r JOIN spaces s ON s.id=r.target_id "
            "WHERE r.source_kind='element' AND r.source_id=? "
            "AND r.target_kind='space' "
            "AND r.relation_type IN ('from_space','to_space','contained_in') "
            "ORDER BY r.relation_type,s.id,r.id",
            (entity_id,),
        )
        output: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (row["relation_type"], row["target_id"])
            if key in seen:
                continue
            seen.add(key)
            output.append(
                {
                    "instance_id": entity_id,
                    "relation_type": row["relation_type"],
                    "space": {
                        "id": row["target_id"],
                        "kind": row.pop("space_kind"),
                        "name": row.pop("space_name"),
                        "number": row.pop("space_number"),
                        "level_id": row.pop("space_level_id"),
                        "source_model_id": row.pop("space_source_model_id"),
                        "source_unique_id": row.pop("space_source_unique_id"),
                        "provenance": row.pop("space_provenance"),
                        "confidence": row.pop("space_confidence"),
                    },
                    "relationship": {
                        "id": row["id"],
                        "phase_source_unique_id": row.get("phase_source_unique_id"),
                        "provenance": row["provenance"],
                        "confidence": row["confidence"],
                        "evidence_id": row["evidence_id"],
                    },
                }
            )
        return output

    def get_change_impact(self, entity_id: str) -> dict[str, Any]:
        """Resolve a type and return its stored instance, space, and drawing impact."""
        subject = self.get_entity("element", entity_id)
        subject_kind = "element"
        if subject is None:
            subject = self.get_entity("element_type", entity_id)
            subject_kind = "element_type"
        if subject is None:
            return {
                "status": "not_found",
                "subject": None,
                "element_type": None,
                "affected_instances": [],
                "related_spaces": [],
                "drawing_occurrences": [],
                "coverage": {
                    "total_instances": 0,
                    "instances_with_spatial_context": 0,
                    "instances_with_drawing_occurrence": 0,
                },
                "warnings": ["entity_not_found"],
            }

        type_id = subject["type_id"] if subject_kind == "element" else subject["id"]
        element_type = self.get_entity("element_type", type_id) if type_id else None
        if element_type is None:
            return {
                "status": "insufficient_data",
                "subject": {"kind": subject_kind, **subject},
                "element_type": None,
                "affected_instances": [],
                "related_spaces": [],
                "drawing_occurrences": [],
                "coverage": {
                    "total_instances": 0,
                    "instances_with_spatial_context": 0,
                    "instances_with_drawing_occurrence": 0,
                },
                "warnings": ["element_type_not_stored"],
            }

        instances = self.get_type_instances(element_type["id"])
        related_spaces: list[dict[str, Any]] = []
        occurrences: list[dict[str, Any]] = []
        occurrence_keys: set[str] = set()
        spatial_instance_ids: set[str] = set()
        drawing_instance_ids: set[str] = set()
        for instance in instances:
            spatial = self.get_related_spaces(instance["id"])
            if spatial:
                spatial_instance_ids.add(instance["id"])
                related_spaces.extend(spatial)
            instance_occurrences = self.get_occurrence_evidence(
                "element", instance["id"]
            )
            if instance_occurrences:
                drawing_instance_ids.add(instance["id"])
            for occurrence in instance_occurrences:
                occurrence = {"instance_id": instance["id"], **occurrence}
                key = json.dumps(
                    {
                        name: value
                        for name, value in occurrence.items()
                        if name != "appearance_id"
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if key in occurrence_keys:
                    continue
                occurrence_keys.add(key)
                occurrences.append(occurrence)

        total = len(instances)
        warnings = []
        if len(spatial_instance_ids) < total:
            warnings.append("spatial_context_partial")
        if len(drawing_instance_ids) < total:
            warnings.append("drawing_occurrence_partial")
        return {
            "status": "ok",
            "subject": {"kind": subject_kind, **subject},
            "element_type": element_type,
            "affected_instances": instances,
            "related_spaces": related_spaces,
            "drawing_occurrences": occurrences,
            "coverage": {
                "total_instances": total,
                "instances_with_spatial_context": len(spatial_instance_ids),
                "instances_with_drawing_occurrence": len(drawing_instance_ids),
            },
            "warnings": warnings,
        }

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
