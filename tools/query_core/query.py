from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from .errors import QueryCoreError
from .drawing_references import validate_drawing_references
from .geometry import (
    decode_location_primitive,
    longitudinal_overlap,
    primitive_bounds,
    primitive_distance,
)
from .navigation import deduplicate_navigation, navigation_target

SCHEMA_VERSION = 2
REQUIRED_METADATA = {
    "schema_version",
    "generator_version",
    "project_id",
    "created_from",
}
REQUIRED_VIRTUAL_TABLES = {"geometry_rtree", "search_fts"}
PDF_TEXT_TABLES = {"pdf_text_blocks", "pdf_text_lines", "pdf_text_spans"}
PDF_TABLE_TABLES = {"pdf_tables", "pdf_table_cells", "pdf_table_cell_spans"}
SEMANTIC_TABLES = {"semantic_entities", "semantic_bindings"}
SEMANTIC_PROPERTY_TABLE = "semantic_properties"
SEMANTIC_RELATIONSHIP_TABLE = "semantic_relationships"
DRAWING_REFERENCE_TABLE = "drawing_references"
DRAWING_REFERENCE_REQUIRED_COLUMNS = {
    "id", "source_evidence_id", "relation_type", "printed_reference",
    "target_view_id", "target_sheet_id", "target_evidence_id",
    "resolution_state", "provenance",
}
_APPEARANCE_CONTEXT_SELECT = (
    "SELECT CASE WHEN ds.document_id=d.id THEN a.sheet_id "
    "WHEN vps.document_id=d.id THEN vp.sheet_id END AS sheet_id,"
    "CASE WHEN dv.document_id=d.id THEN a.view_id "
    "WHEN vpv.document_id=d.id THEN vp.view_id END AS view_id,"
    "a.sheet_id AS appearance_sheet_id,a.view_id AS appearance_view_id,a.*,"
    "d.id AS document_id,d.identity AS document_identity,d.source_filename,"
    "s.number AS sheet_number,s.name AS sheet_name,"
    "v.name AS view_name,v.view_type FROM entity_appearances a "
    "LEFT JOIN viewports vp ON vp.id=a.viewport_id "
    "LEFT JOIN sheets ds ON ds.id=a.sheet_id "
    "LEFT JOIN views dv ON dv.id=a.view_id "
    "LEFT JOIN sheets vps ON vps.id=vp.sheet_id "
    "LEFT JOIN views vpv ON vpv.id=vp.view_id "
    "JOIN documents d ON d.id=COALESCE(ds.document_id,dv.document_id,"
    "vps.document_id,vpv.document_id) "
    "LEFT JOIN sheets s ON s.id=CASE WHEN ds.document_id=d.id THEN a.sheet_id "
    "WHEN vps.document_id=d.id THEN vp.sheet_id END "
    "LEFT JOIN views v ON v.id=CASE WHEN dv.document_id=d.id THEN a.view_id "
    "WHEN vpv.document_id=d.id THEN vp.view_id END "
)
SEMANTIC_REQUIRED_COLUMNS = {
    "semantic_entities": {
        "id", "entity_class", "label", "number", "instance_or_type",
        "resolution_state", "provenance", "evidence_id",
    },
    "semantic_bindings": {
        "id", "semantic_entity_id", "source_kind", "source_id",
        "resolution_state", "provenance", "evidence_id",
    },
}
SEMANTIC_PROPERTY_REQUIRED_COLUMNS = {
    "id", "semantic_entity_id", "source_name", "source_value", "value_type",
    "raw_numeric_value", "numeric_value", "unit", "scope", "canonical_name",
    "provenance", "source_binding_id", "evidence_id",
}
SEMANTIC_RELATIONSHIP_REQUIRED_COLUMNS = {
    "id", "subject_semantic_entity_id", "relation_type",
    "object_semantic_entity_id", "provenance", "evidence_id",
    "source_binding_id",
}
PDF_TABLE_REQUIRED_COLUMNS = {
    "pdf_tables": {"id", "page_id", "order_index", "row_count", "column_count",
        "x_min", "y_min", "x_max", "y_max", "coordinate_space", "provenance",
        "detection_method", "algorithm_version", "library", "library_version"},
    "pdf_table_cells": {"id", "table_id", "row_index", "column_index", "row_span",
        "column_span", "text", "x_min", "y_min", "x_max", "y_max",
        "coordinate_space", "provenance"},
    "pdf_table_cell_spans": {"cell_id", "span_id", "order_index"},
}
PDF_TEXT_REQUIRED_COLUMNS = {
    "pdf_text_blocks": {
        "id", "page_id", "order_index", "text", "x_min", "y_min", "x_max",
        "y_max", "coordinate_space", "provenance", "evidence_id",
    },
    "pdf_text_lines": {
        "id", "block_id", "order_index", "text", "x_min", "y_min", "x_max",
        "y_max", "coordinate_space", "provenance",
    },
    "pdf_text_spans": {
        "id", "line_id", "order_index", "text", "x_min", "y_min", "x_max",
        "y_max", "coordinate_space", "provenance", "font_name", "font_size",
        "font_flags",
    },
}
PDF_TEXT_PAGE_COLUMNS = {
    "media_x_min", "media_y_min", "media_x_max", "media_y_max",
    "crop_x_min", "crop_y_min", "crop_x_max", "crop_y_max", "coordinate_space",
}
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

_DRAWING_REFERENCE_CONTEXT_QUERIES = {
    "evidence": (
        "SELECT e.* FROM drawing_references r "
        "JOIN evidence e ON e.id=r.source_evidence_id "
        "UNION "
        "SELECT e.* FROM drawing_references r "
        "JOIN evidence e ON e.id=r.target_evidence_id "
        "WHERE r.target_evidence_id IS NOT NULL"
    ),
    "views": (
        "SELECT DISTINCT v.* FROM drawing_references r "
        "JOIN views v ON v.id=r.target_view_id "
        "WHERE r.target_view_id IS NOT NULL"
    ),
    "sheets": (
        "SELECT DISTINCT s.* FROM drawing_references r "
        "JOIN sheets s ON s.id=r.target_sheet_id "
        "WHERE r.target_sheet_id IS NOT NULL"
    ),
    "viewports": (
        "WITH target_views AS ("
        "SELECT DISTINCT target_view_id AS view_id FROM drawing_references "
        "WHERE target_view_id IS NOT NULL) "
        "SELECT vp.* FROM target_views tv "
        "JOIN viewports vp ON vp.view_id=tv.view_id"
    ),
}


def _load_drawing_reference_validation_context(
    connection: sqlite3.Connection,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
] | None:
    references = [
        dict(row) for row in connection.execute("SELECT * FROM drawing_references")
    ]
    if not references:
        return None

    evidence = [dict(row) for row in connection.execute(
        _DRAWING_REFERENCE_CONTEXT_QUERIES["evidence"]
    )]
    views = [dict(row) for row in connection.execute(
        _DRAWING_REFERENCE_CONTEXT_QUERIES["views"]
    )]
    sheets = [dict(row) for row in connection.execute(
        _DRAWING_REFERENCE_CONTEXT_QUERIES["sheets"]
    )]
    viewports = [dict(row) for row in connection.execute(
        _DRAWING_REFERENCE_CONTEXT_QUERIES["viewports"]
    )]
    return references, evidence, views, sheets, viewports


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
        present_pdf_text_tables = PDF_TEXT_TABLES & objects.keys()
        if present_pdf_text_tables and present_pdf_text_tables != PDF_TEXT_TABLES:
            missing = sorted(PDF_TEXT_TABLES - present_pdf_text_tables)
            raise QueryCoreError(
                "incomplete PDF text capability; missing tables: " + ", ".join(missing)
            )
        if present_pdf_text_tables:
            if "pdf_pages" not in objects:
                raise QueryCoreError(
                    "incomplete PDF text capability; missing table: pdf_pages"
                )
            for table, required in PDF_TEXT_REQUIRED_COLUMNS.items():
                actual = {
                    row["name"]
                    for row in connection.execute(
                        f"PRAGMA table_info({json.dumps(table)})"
                    )
                }
                missing_columns = sorted(required - actual)
                if missing_columns:
                    raise QueryCoreError(
                        f"incompatible PDF text capability; {table} missing columns: "
                        + ", ".join(missing_columns)
                    )
        present_pdf_table_tables = PDF_TABLE_TABLES & objects.keys()
        if present_pdf_table_tables and present_pdf_table_tables != PDF_TABLE_TABLES:
            missing = sorted(PDF_TABLE_TABLES - present_pdf_table_tables)
            raise QueryCoreError(
                "incomplete PDF table capability; missing tables: " + ", ".join(missing)
            )
        if present_pdf_table_tables:
            if present_pdf_text_tables != PDF_TEXT_TABLES:
                raise QueryCoreError("PDF table capability requires PDF text capability")
            for table, required in PDF_TABLE_REQUIRED_COLUMNS.items():
                actual = {row["name"] for row in connection.execute(
                    f"PRAGMA table_info({json.dumps(table)})"
                )}
                missing_columns = sorted(required - actual)
                if missing_columns:
                    raise QueryCoreError(
                        f"incompatible PDF table capability; {table} missing columns: "
                        + ", ".join(missing_columns)
                    )
            _validate_pdf_table_rows(connection)
        present_semantic_tables = SEMANTIC_TABLES & objects.keys()
        if present_semantic_tables and present_semantic_tables != SEMANTIC_TABLES:
            missing = sorted(SEMANTIC_TABLES - present_semantic_tables)
            raise QueryCoreError(
                "incomplete semantic capability; missing tables: " + ", ".join(missing)
            )
        for table in present_semantic_tables:
            actual = {
                row["name"]
                for row in connection.execute(
                    f"PRAGMA table_info({json.dumps(table)})"
                )
            }
            missing_columns = sorted(SEMANTIC_REQUIRED_COLUMNS[table] - actual)
            if missing_columns:
                raise QueryCoreError(
                    f"incompatible semantic capability; {table} missing columns: "
                    + ", ".join(missing_columns)
                )
        if present_semantic_tables == SEMANTIC_TABLES:
            known_binding_sources = {
                "element": "elements",
                "element_type": "element_types",
                "space": "spaces",
                "level": "levels",
                "evidence": "evidence",
                "pdf_table_cell": "pdf_table_cells",
                "pdf_text_span": "pdf_text_spans",
            }
            for source_kind, source_table in known_binding_sources.items():
                if source_table in objects:
                    sql = (
                        "SELECT b.id FROM semantic_bindings b "
                        f"LEFT JOIN {source_table} s ON s.id=b.source_id "
                        "WHERE b.source_kind=? AND b.resolution_state IN "
                        "('exact','resolved_deterministically') AND s.id IS NULL LIMIT 1"
                    )
                else:
                    sql = (
                        "SELECT id FROM semantic_bindings WHERE source_kind=? "
                        "AND resolution_state IN "
                        "('exact','resolved_deterministically') LIMIT 1"
                    )
                missing_source = connection.execute(sql, (source_kind,)).fetchone()
                if missing_source:
                    raise QueryCoreError(
                        f"semantic binding references nonexistent {source_kind} record: "
                        f"{missing_source['id']}"
                    )
        if SEMANTIC_PROPERTY_TABLE in objects:
            if present_semantic_tables != SEMANTIC_TABLES:
                raise QueryCoreError(
                    "semantic property capability requires semantic capability"
                )
            actual = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(semantic_properties)"
                )
            }
            missing_columns = sorted(SEMANTIC_PROPERTY_REQUIRED_COLUMNS - actual)
            if missing_columns:
                raise QueryCoreError(
                    "incompatible semantic property capability; missing columns: "
                    + ", ".join(missing_columns)
                )
        if SEMANTIC_RELATIONSHIP_TABLE in objects:
            if present_semantic_tables != SEMANTIC_TABLES:
                raise QueryCoreError(
                    "semantic relationship capability requires semantic capability"
                )
            actual = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(semantic_relationships)"
                )
            }
            missing_columns = sorted(SEMANTIC_RELATIONSHIP_REQUIRED_COLUMNS - actual)
            if missing_columns:
                raise QueryCoreError(
                    "incompatible semantic relationship capability; missing columns: "
                    + ", ".join(missing_columns)
                )
            invalid = connection.execute(
                "SELECT id FROM semantic_relationships "
                "WHERE typeof(relation_type)!='text' OR trim(relation_type)='' LIMIT 1"
            ).fetchone()
            if invalid:
                raise QueryCoreError(
                    "semantic relationship relation_type must be non-empty: "
                    f"{invalid['id']}"
                )
            relationship_checks = (
                ("LEFT JOIN semantic_entities e ON e.id=r.subject_semantic_entity_id "
                 "WHERE e.id IS NULL", "subject semantic entity"),
                ("LEFT JOIN semantic_entities e ON e.id=r.object_semantic_entity_id "
                 "WHERE e.id IS NULL", "object semantic entity"),
                ("LEFT JOIN evidence e ON e.id=r.evidence_id "
                 "WHERE r.evidence_id IS NOT NULL AND e.id IS NULL", "evidence"),
                ("LEFT JOIN semantic_bindings b ON b.id=r.source_binding_id "
                 "WHERE r.source_binding_id IS NOT NULL AND b.id IS NULL",
                 "semantic binding"),
            )
            for clause, label in relationship_checks:
                invalid = connection.execute(
                    "SELECT r.id FROM semantic_relationships r " + clause + " LIMIT 1"
                ).fetchone()
                if invalid:
                    raise QueryCoreError(
                        f"semantic relationship references nonexistent {label}: "
                        f"{invalid['id']}"
                    )
        if DRAWING_REFERENCE_TABLE in objects:
            actual = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(drawing_references)"
                )
            }
            missing_columns = sorted(DRAWING_REFERENCE_REQUIRED_COLUMNS - actual)
            if missing_columns:
                raise QueryCoreError(
                    "incompatible drawing reference capability; missing columns: "
                    + ", ".join(missing_columns)
                )
            context = _load_drawing_reference_validation_context(connection)
            if context is not None:
                validate_drawing_references(*context)
        if "pdf_pages" in objects:
            required_pdf_page_columns = {
                "id",
                "document_id",
                "page_number",
                "width_points",
                "height_points",
                "rotation",
                "provenance",
            }
            actual = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(pdf_pages)")
            }
            if present_pdf_text_tables:
                required_pdf_page_columns |= PDF_TEXT_PAGE_COLUMNS
            missing_columns = sorted(required_pdf_page_columns - actual)
            if missing_columns:
                raise QueryCoreError(
                    "incompatible Query Core v2 schema; pdf_pages missing columns: "
                    + ", ".join(missing_columns)
                )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise QueryCoreError("Query Core has foreign-key violations")
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        missing_metadata = sorted(REQUIRED_METADATA - metadata.keys())
        if missing_metadata:
            raise QueryCoreError(
                f"payload missing required metadata: {', '.join(missing_metadata)}"
            )
        binding_mode = metadata.get("binding_mode", "single_document")
        if binding_mode not in {"single_document", "project"}:
            raise QueryCoreError(
                "payload binding_mode must be single_document or project"
            )
        if binding_mode == "single_document":
            missing_binding = sorted(
                {"source_document_identity", "source_document_sha256"} - metadata.keys()
            )
            if missing_binding:
                raise QueryCoreError(
                    "payload missing required metadata: " + ", ".join(missing_binding)
                )
        project_documents = (
            list(connection.execute("SELECT identity,source_sha256 FROM documents"))
            if binding_mode == "project"
            else []
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
    metadata["binding_mode"] = binding_mode
    for key in REQUIRED_METADATA - {"schema_version"}:
        if not isinstance(metadata[key], str) or not metadata[key].strip():
            raise QueryCoreError(f"payload metadata {key} must be a non-empty string")
    if binding_mode == "single_document":
        identity = metadata["source_document_identity"]
        if not isinstance(identity, str) or not identity.strip():
            raise QueryCoreError(
                "payload metadata source_document_identity must be a non-empty string"
            )
        hashes = [("source_document_sha256", metadata["source_document_sha256"])]
    else:
        hashes = []
        for identity, source_hash in project_documents:
            if not isinstance(identity, str) or not identity.strip():
                raise QueryCoreError(
                    "payload document identity must be a non-empty string"
                )
            hashes.append(("document source_sha256", source_hash))
    for label, source_hash in hashes:
        if not isinstance(source_hash, str) or len(source_hash) != 64 or any(
            character not in "0123456789abcdef" for character in source_hash
        ):
            raise QueryCoreError(f"payload {label} must be a lowercase SHA-256")
    return metadata


def _validate_pdf_table_rows(connection: sqlite3.Connection) -> None:
    """Validate persisted derived-table integrity beyond SQLite's foreign keys."""
    tables = list(connection.execute(
        "SELECT id,page_id,order_index,row_count,column_count,x_min,y_min,x_max,y_max,"
        "coordinate_space,provenance,detection_method,algorithm_version,library,library_version "
        "FROM pdf_tables ORDER BY page_id,order_index"
    ))
    expected_by_page: dict[str, int] = {}
    for table in tables:
        expected = expected_by_page.get(table["page_id"], 0)
        if table["order_index"] != expected:
            raise QueryCoreError("non-contiguous PDF table order")
        expected_by_page[table["page_id"]] = expected + 1
        if (table["row_count"] < 2 or table["column_count"] < 2
                or table["coordinate_space"] != "pdf_points_top_left"
                or table["provenance"] != "derived_pdf_table"
                or table["detection_method"] != "pymupdf_lines_strict"
                or not isinstance(table["algorithm_version"], str)
                or not table["algorithm_version"]
                or table["library"] != "PyMuPDF"
                or not isinstance(table["library_version"], str)
                or not table["library_version"]
                or not all(math.isfinite(table[k]) for k in ("x_min", "y_min", "x_max", "y_max"))
                or table["x_min"] >= table["x_max"] or table["y_min"] >= table["y_max"]):
            raise QueryCoreError("invalid persisted PDF table")
        cells = list(connection.execute(
            "SELECT * FROM pdf_table_cells WHERE table_id=? ORDER BY row_index,column_index", (table["id"],)
        ))
        expected_cells = [(r, c) for r in range(table["row_count"]) for c in range(table["column_count"])]
        if [(cell["row_index"], cell["column_index"]) for cell in cells] != expected_cells:
            raise QueryCoreError("persisted PDF table does not form a complete row-major grid")
        for cell in cells:
            if (cell["row_span"] != 1 or cell["column_span"] != 1
                    or cell["coordinate_space"] != "pdf_points_top_left"
                    or cell["provenance"] != "derived_pdf_table"
                    or not all(math.isfinite(cell[k]) for k in ("x_min", "y_min", "x_max", "y_max"))
                    or cell["x_min"] >= cell["x_max"] or cell["y_min"] >= cell["y_max"]
                    or cell["x_min"] < table["x_min"] or cell["y_min"] < table["y_min"]
                    or cell["x_max"] > table["x_max"] or cell["y_max"] > table["y_max"]):
                raise QueryCoreError("invalid persisted PDF table cell")
            links = list(connection.execute(
                "SELECT l.order_index,s.id AS span_id,s.text,s.line_id,s.x_min,s.y_min,"
                "s.x_max,s.y_max,s.coordinate_space,s.provenance,"
                "b.page_id,b.order_index AS block_order,n.order_index AS line_order,"
                "s.order_index AS span_order FROM pdf_table_cell_spans l "
                "JOIN pdf_text_spans s ON s.id=l.span_id "
                "JOIN pdf_text_lines n ON n.id=s.line_id "
                "JOIN pdf_text_blocks b ON b.id=n.block_id "
                "WHERE l.cell_id=? ORDER BY l.order_index",
                (cell["id"],),
            ))
            if [link["order_index"] for link in links] != list(range(len(links))):
                raise QueryCoreError("non-contiguous PDF table cell span order")
            source_order = [
                (link["block_order"], link["line_order"], link["span_order"])
                for link in links
            ]
            if source_order != sorted(source_order) or len(set(source_order)) != len(source_order):
                raise QueryCoreError("non-deterministic PDF table cell span order")
            for link in links:
                if (link["page_id"] != table["page_id"]
                        or link["coordinate_space"] != "pdf_points_top_left"
                        or link["provenance"] != "embedded_pdf_text"
                        or not all(math.isfinite(link[key]) for key in
                                   ("x_min", "y_min", "x_max", "y_max"))
                        or link["x_min"] >= link["x_max"]
                        or link["y_min"] >= link["y_max"]
                        or link["x_min"] < cell["x_min"]
                        or link["y_min"] < cell["y_min"]
                        or link["x_max"] > cell["x_max"]
                        or link["y_max"] > cell["y_max"]):
                    raise QueryCoreError(
                        "PDF table cell span is not authoritative for its page and bbox"
                    )
            groups: list[list[str]] = []
            previous_line = None
            for link in links:
                if link["line_id"] != previous_line:
                    groups.append([])
                    previous_line = link["line_id"]
                groups[-1].append(link["text"])
            reconstructed = "\n".join("".join(group) for group in groups)
            if reconstructed != cell["text"]:
                raise QueryCoreError(
                    "PDF table cell text does not match authoritative source spans"
                )


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

    def _has_columns(self, table: str, *columns: str) -> bool:
        """Report optional additive capabilities without modifying the payload."""
        actual = {
            row["name"]
            for row in self.connection.execute(
                f"PRAGMA table_info({json.dumps(table)})"
            )
        }
        return set(columns) <= actual

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "QueryCore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _rows(self, sql: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(sql, values)]

    def has_pdf_table_capability(self) -> bool:
        """Return whether this v2 payload includes the optional PDF table schema."""
        tables = {
            row["name"]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        return PDF_TABLE_TABLES <= tables

    def has_semantic_capability(self) -> bool:
        """Return whether this v2 payload includes the optional semantic schema."""
        tables = {
            row["name"]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        return SEMANTIC_TABLES <= tables

    def has_semantic_property_capability(self) -> bool:
        """Return whether sparse non-Revit semantic properties are available."""
        return self._has_columns(
            SEMANTIC_PROPERTY_TABLE, *SEMANTIC_PROPERTY_REQUIRED_COLUMNS
        )

    def has_semantic_relationship_capability(self) -> bool:
        """Return whether source-neutral semantic relationships are available."""
        return self._has_columns(
            SEMANTIC_RELATIONSHIP_TABLE,
            *SEMANTIC_RELATIONSHIP_REQUIRED_COLUMNS,
        )

    def has_drawing_reference_capability(self) -> bool:
        """Return whether explicit source drawing-reference facts are available."""
        return self._has_columns(
            DRAWING_REFERENCE_TABLE, *DRAWING_REFERENCE_REQUIRED_COLUMNS
        )

    def get_drawing_reference(self, reference_id: str) -> dict[str, Any] | None:
        """Return one stored drawing-reference fact and explicit navigation only."""
        if not self.has_drawing_reference_capability():
            return None
        rows = self._rows(
            "SELECT * FROM drawing_references WHERE id=?", (reference_id,)
        )
        if not rows:
            return None
        row = rows[0]
        source_evidence = self.get_pdf_evidence(row["source_evidence_id"])
        result = {
            key: row[key] for key in (
                "id", "relation_type", "printed_reference", "resolution_state",
                "provenance",
            )
        }
        result["source"] = {
            "evidence": source_evidence,
            "navigation": self.get_evidence_navigation(row["source_evidence_id"]),
        }
        if row["resolution_state"] in {"ambiguous", "unresolved"}:
            result["target"] = None
            return result
        view = self._rows("SELECT * FROM views WHERE id=?", (row["target_view_id"],))[0] if row["target_view_id"] else None
        sheet = self._rows("SELECT * FROM sheets WHERE id=?", (row["target_sheet_id"],))[0] if row["target_sheet_id"] else None
        evidence = self.get_pdf_evidence(row["target_evidence_id"]) if row["target_evidence_id"] else None
        navigation = self.get_evidence_navigation(row["target_evidence_id"]) if row["target_evidence_id"] else None
        if navigation is None and sheet is not None:
            document_id = sheet["document_id"]
            document = self._rows(
                "SELECT * FROM documents WHERE id=?", (document_id,)
            )[0]
            context = {
                "document_id": document_id,
                "document_identity": document["identity"],
                "source_filename": document["source_filename"],
                "source_sha256": document["source_sha256"],
                "sheet_id": sheet["id"] if sheet else None,
                "sheet_number": sheet["number"] if sheet else None,
                "sheet_name": sheet["name"] if sheet else None,
                "view_id": view["id"] if view else None,
                "view_name": view["name"] if view else None,
                "view_type": view["view_type"] if view else None,
                "pdf_page": sheet["pdf_page"] if sheet else None,
                "x_min": None, "y_min": None, "x_max": None, "y_max": None,
                "coordinate_space": None,
                "source_id": row["id"],
            }
            navigation = navigation_target(context, source_kind="drawing_reference_target")
        result["target"] = {
            "view": view, "sheet": sheet, "evidence": evidence,
            "navigation": navigation,
        }
        return result

    def get_drawing_references_for_view(self, view_id: str) -> list[dict[str, Any]]:
        """Return explicit references located in evidence for one source view."""
        if not self.has_drawing_reference_capability():
            return []
        ids = self._rows(
            "SELECT r.id FROM drawing_references r "
            "JOIN evidence e ON e.id=r.source_evidence_id "
            "JOIN documents d ON d.id=e.document_id WHERE e.view_id=? "
            "ORDER BY d.identity,e.pdf_page,e.x_min,e.y_min,e.x_max,e.y_max,"
            "r.relation_type,r.id",
            (view_id,),
        )
        return [self.get_drawing_reference(row["id"]) for row in ids]

    def get_semantic_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Project one semantic identity over existing source facts, without copies."""
        if not self.has_semantic_capability():
            return None
        entities = self._rows(
            "SELECT * FROM semantic_entities WHERE id=?", (entity_id,)
        )
        if not entities:
            return None
        entity = entities[0]
        bindings = self._rows(
            "SELECT * FROM semantic_bindings WHERE semantic_entity_id=? ORDER BY id",
            (entity_id,),
        )
        resolved_bindings = [
            (row["source_kind"], row["source_id"])
            for row in bindings
            if row["source_id"] is not None
            and row["resolution_state"] in {"exact", "resolved_deterministically"}
        ]
        parameter_bindings = [
            binding
            for binding in resolved_bindings
            if binding[0] in {"element", "element_type"}
        ]
        properties: list[dict[str, Any]] = []
        for source_kind, source_id in parameter_bindings:
            for row in self._rows(
                "SELECT * FROM parameters WHERE entity_kind=? AND entity_id=? "
                "ORDER BY definition_name,id",
                (source_kind, source_id),
            ):
                properties.append({
                    "fact_kind": "query_core_parameter",
                    "parameter_id": row["id"],
                    "source_entity": {"kind": source_kind, "id": source_id},
                    "source_name": row["definition_name"],
                    "source_value": row["value_text"] or row["raw_value_text"],
                    "raw_numeric_value": row["raw_numeric_value"],
                    "numeric_value": row["numeric_value"],
                    "unit": row["unit"],
                    "scope": row["scope"],
                    "provenance": row["provenance"],
                    "confidence": row["confidence"],
                    "evidence_refs": [row["evidence_id"]] if row["evidence_id"] else [],
                })
        if self.has_semantic_property_capability():
            for row in self._rows(
                "SELECT * FROM semantic_properties WHERE semantic_entity_id=? "
                "ORDER BY source_name,id",
                (entity_id,),
            ):
                properties.append({
                    "fact_kind": "semantic_property",
                    "property_id": row["id"],
                    "source_name": row["source_name"],
                    "source_value": row["source_value"],
                    "value_type": row["value_type"],
                    "raw_numeric_value": row["raw_numeric_value"],
                    "numeric_value": row["numeric_value"],
                    "unit": row["unit"],
                    "scope": row["scope"],
                    "canonical_name": row["canonical_name"],
                    "provenance": row["provenance"],
                    "source_binding_ref": row["source_binding_id"],
                    "evidence_refs": [row["evidence_id"]] if row["evidence_id"] else [],
                })
        relationship_rows: dict[tuple[str, str], dict[str, Any]] = {}
        for source_kind, source_id in resolved_bindings:
            for row in self._rows(
                "SELECT * FROM relationships WHERE "
                "(source_kind=? AND source_id=?) OR (target_kind=? AND target_id=?) "
                "ORDER BY id",
                (source_kind, source_id, source_kind, source_id),
            ):
                direction = (
                    "outgoing"
                    if (row["source_kind"], row["source_id"])
                    == (source_kind, source_id)
                    else "incoming"
                )
                projected = dict(row)
                projected.update({
                    "fact_kind": "query_core_relationship",
                    "direction": direction,
                    "evidence_refs": (
                        [row["evidence_id"]] if row["evidence_id"] else []
                    ),
                })
                key = ("query_core_relationship", row["id"])
                existing = relationship_rows.get(key)
                if existing is not None and existing["direction"] != direction:
                    existing["direction"] = "self"
                else:
                    relationship_rows[key] = projected
        if self.has_semantic_relationship_capability():
            for row in self._rows(
                "SELECT * FROM semantic_relationships WHERE "
                "subject_semantic_entity_id=? OR object_semantic_entity_id=? "
                "ORDER BY id",
                (entity_id, entity_id),
            ):
                persisted = dict(row)
                persisted.update({
                    "fact_kind": "semantic_relationship",
                    "direction": (
                        "self"
                        if row["subject_semantic_entity_id"] == entity_id
                        and row["object_semantic_entity_id"] == entity_id
                        else (
                            "outgoing"
                            if row["subject_semantic_entity_id"] == entity_id
                            else "incoming"
                        )
                    ),
                    "evidence_refs": (
                        [row["evidence_id"]] if row["evidence_id"] else []
                    ),
                })
                relationship_rows[("semantic_relationship", row["id"])] = persisted
        evidence_refs = sorted({
            ref for ref in (
                [entity.get("evidence_id")]
                + [row.get("evidence_id") for row in bindings]
            ) if ref
        })
        return {
            "capability": {"semantic_projection": True, "schema_version": 2},
            "entity": entity,
            "bindings": bindings,
            "properties": properties,
            "relationships": [relationship_rows[key] for key in sorted(relationship_rows)],
            "evidence_refs": evidence_refs,
        }


    def get_architectural_evidence_context(
        self, semantic_entity_id: str
    ) -> dict[str, Any] | None:
        """Return a deterministic, one-hop evidence envelope for one semantic entity."""
        semantic = self.get_semantic_entity(semantic_entity_id)
        if semantic is None:
            return None

        spatial = self.get_semantic_spatial_context(semantic_entity_id)
        drawing = self.get_semantic_drawing_context(semantic_entity_id)
        bindings = semantic["bindings"]
        counts: dict[str, int] = {
            "exact": 0,
            "resolved_deterministically": 0,
            "ambiguous": 0,
            "unresolved": 0,
        }
        for binding in bindings:
            state = binding["resolution_state"]
            counts[state] = counts.get(state, 0) + 1

        refs = {ref for ref in semantic["evidence_refs"] if ref}
        for fact in (*semantic["properties"], *semantic["relationships"]):
            refs.update(ref for ref in fact.get("evidence_refs", []) if ref)

        evidence = []
        for evidence_id in refs:
            row = self.get_pdf_evidence(evidence_id)
            if row is None:
                continue
            bbox = (
                [row[key] for key in ("x_min", "y_min", "x_max", "y_max")]
                if all(row.get(key) is not None for key in ("x_min", "y_min", "x_max", "y_max"))
                else None
            )
            evidence.append({
                "evidence_id": evidence_id,
                "document": {
                    "id": row["document_id"],
                    "identity": row["document_identity"],
                    "source_filename": row["source_filename"],
                    "source_sha256": row["source_sha256"],
                },
                "sheet_id": row["sheet_id"],
                "view_id": row["view_id"],
                "pdf_page": row["pdf_page"],
                "bbox": bbox,
                "coordinate_space": row["coordinate_space"],
                "navigation": self.get_evidence_navigation(evidence_id),
            })
        evidence.sort(key=lambda row: (
            row["document"]["identity"] or "", row["pdf_page"] or 0,
            tuple(row["bbox"]) if row["bbox"] is not None else (), row["evidence_id"],
        ))

        direct_document_ids = {
            item["document"]["id"] for item in evidence
        }
        for occurrence in drawing["occurrences"] if drawing else []:
            doc = occurrence.get("document")
            if doc and doc.get("id"):
                direct_document_ids.add(doc["id"])
        source_documents = []
        if direct_document_ids:
            placeholders = ",".join("?" for _ in direct_document_ids)
            source_documents = [dict(row) for row in self._rows(
                "SELECT id,identity,source_filename,source_sha256 FROM documents "
                f"WHERE id IN ({placeholders}) ORDER BY identity,id",
                tuple(sorted(direct_document_ids)),
            )]

        return {
            "capability": semantic["capability"],
            "status": "ok",
            "entity": semantic["entity"],
            "resolution": {
                "entity_state": semantic["entity"].get("resolution_state"),
                "binding_counts": counts,
                "ambiguous_binding_ids": [b["id"] for b in bindings if b["resolution_state"] == "ambiguous"],
                "unresolved_binding_ids": [b["id"] for b in bindings if b["resolution_state"] == "unresolved"],
            },
            "bindings": bindings,
            "properties": semantic["properties"],
            "relationships": semantic["relationships"],
            "spatial_contexts": spatial["spatial_contexts"] if spatial else [],
            "drawing_occurrences": drawing["occurrences"] if drawing else [],
            "evidence": evidence,
            "source_documents": source_documents,
            "coverage": {
                "binding_count": len(bindings),
                "property_count": len(semantic["properties"]),
                "relationship_count": len(semantic["relationships"]),
                "spatial_context_count": len(spatial["spatial_contexts"]) if spatial else 0,
                "drawing_occurrence_count": len(drawing["occurrences"]) if drawing else 0,
                "evidence_count": len(evidence),
                "source_document_count": len(source_documents),
            },
        }

    def _semantic_source_reference(
        self, source_kind: str, source_id: str
    ) -> dict[str, Any]:
        """Resolve only explicit deterministic bindings for one source record."""
        semantic_entities = self._rows(
            "SELECT e.id,e.entity_class,e.label,e.number,b.id AS binding_id,"
            "b.resolution_state FROM semantic_bindings b "
            "JOIN semantic_entities e ON e.id=b.semantic_entity_id "
            "WHERE b.source_kind=? AND b.source_id=? "
            "AND b.resolution_state IN ('exact','resolved_deterministically') "
            "ORDER BY e.id,b.id",
            (source_kind, source_id),
        )
        return {
            "source": {"kind": source_kind, "id": source_id},
            "semantic_entities": semantic_entities,
        }

    def _semantic_spatial_target(
        self, value: dict[str, Any] | None, source_kind: str
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            **value,
            "reference": self._semantic_source_reference(source_kind, value["id"]),
        }

    def get_semantic_spatial_context(self, entity_id: str) -> dict[str, Any] | None:
        """Project proven Query Core spatial facts for resolved semantic bindings.

        This is a read-only projection: stored facts remain ``source_fact`` and
        adjacency remains an explicitly ``deterministic_derived`` observation.
        """
        semantic = self.get_semantic_entity(entity_id)
        if semantic is None:
            return None
        contexts = []
        for binding in semantic["bindings"]:
            source_kind = binding["source_kind"]
            source_id = binding["source_id"]
            if (
                source_kind not in {"space", "element", "level"}
                or source_id is None
                or binding["resolution_state"]
                not in {"exact", "resolved_deterministically"}
            ):
                continue
            if source_kind == "level":
                level = self.get_entity("level", source_id)
                if level is None:
                    continue
                source_context = {
                    "status": "ok",
                    "entity": self._semantic_spatial_target(level, "level"),
                    "level": self._semantic_spatial_target(level, "level"),
                    "evidence_class": "source_fact",
                }
            else:
                source_context = self.get_spatial_context(source_kind, source_id)
                source_context["evidence_class"] = "source_fact"
                source_context["entity"] = self._semantic_spatial_target(
                    source_context["entity"], source_kind
                )
                source_context["level"] = self._semantic_spatial_target(
                    source_context["level"], "level"
                )
                if source_kind == "space":
                    source_context["contained_elements"] = [
                        self._semantic_spatial_target(row, "element")
                        for row in source_context["contained_elements"]
                    ]
                    for connection in source_context["connections"]:
                        connection["evidence_class"] = "source_fact"
                        for key, kind in (
                            ("connector_element", "element"),
                            ("connector_type", "element_type"),
                            ("from_space", "space"),
                            ("to_space", "space"),
                            ("connected_space", "space"),
                        ):
                            connection[key] = self._semantic_spatial_target(
                                connection[key], kind
                            )
                    adjacency = source_context["adjacency"]
                    adjacency["evidence_class"] = "deterministic_derived"
                    adjacency["space"] = self._semantic_spatial_target(
                        adjacency["space"], "space"
                    )
                    for adjacent in adjacency["adjacent_spaces"]:
                        adjacent["evidence_class"] = "deterministic_derived"
                        adjacent["space"] = self._semantic_spatial_target(
                            adjacent["space"], "space"
                        )
                else:
                    source_context["containment"] = [
                        self._semantic_spatial_target(row, "space")
                        for row in source_context["containment"]
                    ]
                    siblings = source_context["type_siblings"]
                    siblings["evidence_class"] = "deterministic_derived"
                    siblings["element"] = self._semantic_spatial_target(
                        siblings["element"], "element"
                    )
                    siblings["element_type"] = self._semantic_spatial_target(
                        siblings["element_type"], "element_type"
                    )
                    siblings["elements"] = [
                        self._semantic_spatial_target(row, "element")
                        for row in siblings["elements"]
                    ]
            contexts.append({
                "binding": binding,
                "source_reference": {"kind": source_kind, "id": source_id},
                "spatial_context": source_context,
            })
        relationships = [
            {**relationship, "evidence_class": "source_fact"}
            for relationship in semantic["relationships"]
        ]
        return {
            "capability": semantic["capability"],
            "status": "ok" if contexts else "insufficient_data",
            "semantic_entity": semantic["entity"],
            "relationships": relationships,
            "spatial_contexts": contexts,
        }

    def get_semantic_drawing_context(self, entity_id: str) -> dict[str, Any] | None:
        """Project stored drawing/PDF occurrences for resolved semantic bindings."""
        semantic = self.get_semantic_entity(entity_id)
        if semantic is None:
            return None

        occurrences = []
        for binding in semantic["bindings"]:
            source_kind, source_id = binding["source_kind"], binding["source_id"]
            if (
                source_id is None
                or binding["resolution_state"]
                not in {"exact", "resolved_deterministically"}
            ):
                continue

            projected = []
            if source_kind in {"element", "element_type", "space", "level"}:
                navigation = {
                    row["appearance_id"]: row
                    for row in self.get_navigation_targets(source_kind, source_id)
                }
                for row in self.get_occurrence_evidence(source_kind, source_id):
                    target = navigation.get(row["appearance_id"])
                    if target is None:
                        # Navigation intentionally deduplicates identical locations;
                        # the semantic projection must retain every appearance row.
                        target = self._get_appearance_navigation(row["appearance_id"])
                    projected.append({**row, "navigation": target})
            elif source_kind == "pdf_table_cell":
                cell = self.get_pdf_table_cell(source_id)
                if cell is not None:
                    projected.append(cell)
            elif source_kind == "pdf_text_span":
                span = self.get_pdf_text_span(source_id)
                if span is not None:
                    projected.append(span)
            elif source_kind == "evidence":
                evidence = self.get_pdf_evidence(source_id)
                if evidence is not None:
                    evidence["evidence_id"] = evidence.pop("id")
                    evidence["bbox"] = (
                        [
                            evidence.pop(key)
                            for key in ("x_min", "y_min", "x_max", "y_max")
                        ]
                        if all(
                            evidence[key] is not None
                            for key in ("x_min", "y_min", "x_max", "y_max")
                        )
                        else None
                    )
                    for key in ("x_min", "y_min", "x_max", "y_max"):
                        evidence.pop(key, None)
                    evidence["document"] = {
                        "id": evidence.pop("document_id"),
                        "identity": evidence.pop("document_identity"),
                        "source_filename": evidence.pop("source_filename"),
                        "source_sha256": evidence.pop("source_sha256"),
                    }
                    evidence["navigation"] = self.get_evidence_navigation(source_id)
                    projected.append(evidence)

            for occurrence in projected:
                occurrence.update({
                    "binding": binding,
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "occurrence_id": (
                        occurrence.get("appearance_id")
                        or occurrence.get("cell_id")
                        or occurrence.get("span_id")
                        or occurrence.get("evidence_id")
                    ),
                    "evidence_class": "source_fact",
                })
                occurrences.append(occurrence)

        def ordering(row: dict[str, Any]) -> tuple[Any, ...]:
            document = row.get("document") or {}
            bbox = row.get("bbox")
            return (
                document.get("identity") or "",
                row.get("pdf_page") or 0,
                row.get("sheet_number") or "",
                row.get("view_name") or "",
                tuple(bbox) if bbox is not None else (),
                row["occurrence_id"],
                row["binding"]["id"],
            )

        occurrences.sort(key=ordering)
        return {
            "capability": semantic["capability"],
            "status": "ok" if occurrences else "insufficient_data",
            "semantic_entity": semantic["entity"],
            "occurrences": occurrences,
        }

    @staticmethod
    def _pdf_table_navigation(
        row: dict[str, Any], *, source_kind: str, source_id: str
    ) -> dict[str, Any]:
        bbox = [row[key] for key in ("x_min", "y_min", "x_max", "y_max")]
        return {
            "document": {
                "id": row["document_id"],
                "identity": row["document_identity"],
                "source_filename": row["source_filename"],
            },
            "sheet_id": None,
            "sheet_number": None,
            "sheet_name": None,
            "pdf_page": row["pdf_page"],
            "view_id": None,
            "view_name": None,
            "bbox": bbox,
            "coordinate_space": row["coordinate_space"],
            "bbox_quality": None,
            "link_instance_id": None,
            "provenance": row["provenance"],
            "source_kind": source_kind,
            "source_id": source_id,
            "can_zoom": True,
        }

    @staticmethod
    def _pdf_document(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["document_id"],
            "identity": row["document_identity"],
            "source_filename": row["source_filename"],
            "source_sha256": row["source_sha256"],
        }

    def _pdf_table_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "table_id": row["table_id"],
            "document": self._pdf_document(row),
            "pdf_page": row["pdf_page"],
            "order_index": row["order_index"],
            "row_count": row["row_count"],
            "column_count": row["column_count"],
            "bbox": [row[key] for key in ("x_min", "y_min", "x_max", "y_max")],
            "coordinate_space": row["coordinate_space"],
            "provenance": row["provenance"],
            "extraction": {
                "detection_method": row["detection_method"],
                "algorithm_version": row["algorithm_version"],
                "library": row["library"],
                "library_version": row["library_version"],
            },
            "navigation": self._pdf_table_navigation(
                row, source_kind="pdf_table", source_id=row["table_id"]
            ),
        }

    def _pdf_table_rows(
        self, where: str = "", values: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT t.id AS table_id,t.*,p.page_number AS pdf_page,"
            "d.id AS document_id,d.identity AS document_identity,"
            "d.source_filename,d.source_sha256 FROM pdf_tables t "
            "JOIN pdf_pages p ON p.id=t.page_id "
            "JOIN documents d ON d.id=p.document_id "
            + where
            + " ORDER BY d.identity,p.page_number,t.order_index,t.id",
            values,
        )

    def list_pdf_tables(
        self, document_identity: str | None = None, pdf_page: int | None = None
    ) -> list[dict[str, Any]]:
        """List accepted PDF tables in deterministic document reading order."""
        if pdf_page is not None and (
            isinstance(pdf_page, bool) or not isinstance(pdf_page, int) or pdf_page < 1
        ):
            raise QueryCoreError("pdf_page must be a positive integer")
        if not self.has_pdf_table_capability():
            return []
        clauses, values = [], []
        if document_identity is not None:
            clauses.append("d.identity=?")
            values.append(document_identity)
        if pdf_page is not None:
            clauses.append("p.page_number=?")
            values.append(pdf_page)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [
            self._pdf_table_summary(row)
            for row in self._pdf_table_rows(where, tuple(values))
        ]

    def _pdf_cell_source_spans(self, cell_id: str) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT s.id AS span_id,s.text,s.x_min,s.y_min,s.x_max,s.y_max,"
            "s.coordinate_space,s.provenance,s.order_index AS span_index,"
            "l.order_index AS line_index,b.order_index AS block_index "
            "FROM pdf_table_cell_spans cs JOIN pdf_text_spans s ON s.id=cs.span_id "
            "JOIN pdf_text_lines l ON l.id=s.line_id "
            "JOIN pdf_text_blocks b ON b.id=l.block_id WHERE cs.cell_id=? "
            "ORDER BY cs.order_index",
            (cell_id,),
        )
        return [
            {
                "span_id": row["span_id"],
                "text": row["text"],
                "source_ref": {
                    key: row[key] for key in ("block_index", "line_index", "span_index")
                },
                "bbox": [row[key] for key in ("x_min", "y_min", "x_max", "y_max")],
                "coordinate_space": row["coordinate_space"],
                "provenance": row["provenance"],
            }
            for row in rows
        ]

    def _pdf_cell(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "cell_id": row["cell_id"],
            "row_index": row["row_index"],
            "column_index": row["column_index"],
            "row_span": row["row_span"],
            "column_span": row["column_span"],
            "text": row["text"],
            "bbox": [row[key] for key in ("x_min", "y_min", "x_max", "y_max")],
            "coordinate_space": row["coordinate_space"],
            "provenance": row["provenance"],
            "source_spans": self._pdf_cell_source_spans(row["cell_id"]),
            "navigation": self._pdf_table_navigation(
                row, source_kind="pdf_table_cell", source_id=row["cell_id"]
            ),
        }

    def _pdf_cell_rows(
        self, where: str, values: tuple[Any, ...], *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT c.id AS cell_id,c.*,t.id AS table_id,t.order_index AS table_order_index,"
            "t.row_count,t.column_count,t.x_min AS table_x_min,t.y_min AS table_y_min,"
            "t.x_max AS table_x_max,t.y_max AS table_y_max,t.coordinate_space AS table_coordinate_space,"
            "t.provenance AS table_provenance,t.detection_method,t.algorithm_version,t.library,t.library_version,"
            "p.page_number AS pdf_page,d.id AS document_id,d.identity AS document_identity,"
            "d.source_filename,d.source_sha256 FROM pdf_table_cells c "
            "JOIN pdf_tables t ON t.id=c.table_id JOIN pdf_pages p ON p.id=t.page_id "
            "JOIN documents d ON d.id=p.document_id WHERE "
            + where
            + " ORDER BY d.identity,p.page_number,t.order_index,c.row_index,c.column_index,c.id"
        )
        if limit is not None:
            sql += " LIMIT ?"
            values += (limit,)
        return self._rows(
            sql,
            values,
        )

    def _table_row_from_cell(self, row: dict[str, Any]) -> dict[str, Any]:
        table = dict(row)
        table.update(
            order_index=row["table_order_index"],
            x_min=row["table_x_min"],
            y_min=row["table_y_min"],
            x_max=row["table_x_max"],
            y_max=row["table_y_max"],
            coordinate_space=row["table_coordinate_space"],
            provenance=row["table_provenance"],
        )
        return table

    def get_pdf_table(self, table_id: str) -> dict[str, Any] | None:
        """Return one accepted PDF table and its row-major cells."""
        if not self.has_pdf_table_capability():
            return None
        rows = self._pdf_table_rows(" WHERE t.id=?", (table_id,))
        if not rows:
            return None
        result = self._pdf_table_summary(rows[0])
        result["cells"] = [
            self._pdf_cell(row) for row in self._pdf_cell_rows("t.id=?", (table_id,))
        ]
        return result

    def get_pdf_table_cell(self, cell_id: str) -> dict[str, Any] | None:
        """Return one table cell, its source spans, and containing table summary."""
        if not self.has_pdf_table_capability():
            return None
        rows = self._pdf_cell_rows("c.id=?", (cell_id,))
        if not rows:
            return None
        row = rows[0]
        result = self._pdf_cell(row)
        result.update(
            document=self._pdf_document(row),
            pdf_page=row["pdf_page"],
            table=self._pdf_table_summary(self._table_row_from_cell(row)),
        )
        return result

    def get_pdf_text_span(self, span_id: str) -> dict[str, Any] | None:
        """Return one persisted PDF text span with its exact source location."""
        if not self._has_columns(
            "pdf_text_spans", *PDF_TEXT_REQUIRED_COLUMNS["pdf_text_spans"]
        ):
            return None
        rows = self._rows(
            "SELECT s.*,l.id AS line_id,l.order_index AS line_index,"
            "b.id AS block_id,b.order_index AS block_index,b.evidence_id,"
            "p.page_number AS pdf_page,d.id AS document_id,"
            "d.identity AS document_identity,d.source_filename,d.source_sha256 "
            "FROM pdf_text_spans s JOIN pdf_text_lines l ON l.id=s.line_id "
            "JOIN pdf_text_blocks b ON b.id=l.block_id "
            "JOIN pdf_pages p ON p.id=b.page_id "
            "JOIN documents d ON d.id=p.document_id WHERE s.id=?",
            (span_id,),
        )
        if not rows:
            return None
        row = rows[0]
        result = {
            "span_id": row["id"],
            "text": row["text"],
            "source_ref": {
                "block_id": row["block_id"],
                "block_index": row["block_index"],
                "line_id": row["line_id"],
                "line_index": row["line_index"],
                "span_index": row["order_index"],
            },
            "evidence_id": row["evidence_id"],
            "document": self._pdf_document(row),
            "pdf_page": row["pdf_page"],
            "bbox": [row[key] for key in ("x_min", "y_min", "x_max", "y_max")],
            "coordinate_space": row["coordinate_space"],
            "provenance": row["provenance"],
        }
        result["navigation"] = self._pdf_table_navigation(
            row, source_kind="pdf_text_span", source_id=span_id
        )
        return result

    def search_pdf_table_cells(
        self, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Search cell text directly; ``limit`` must be a positive integer."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise QueryCoreError("limit must be a positive integer")
        if not query.strip() or not self.has_pdf_table_capability():
            return []
        rows = self._pdf_cell_rows("instr(c.text,?)>0", (query,), limit=limit)
        results = []
        for row in rows:
            result = self._pdf_cell(row)
            table = self._pdf_table_summary(self._table_row_from_cell(row))
            result.update(
                document=self._pdf_document(row), pdf_page=row["pdf_page"], table=table
            )
            results.append(result)
        return results

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

    def search_pdf_text(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search source PDF blocks and return their exact stored navigation."""
        tables = {
            row["name"]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "pdf_text_blocks" not in tables or not query.strip():
            return []
        try:
            hits = self._rows(
                "SELECT s.record_id FROM search_fts f JOIN search_content s "
                "ON s.rowid=f.rowid WHERE search_fts MATCH ? "
                "AND s.record_kind='pdf_text_block' ORDER BY rank,s.record_id LIMIT ?",
                (query, limit),
            )
        except sqlite3.OperationalError:
            hits = []
        if not hits:
            hits = self._rows(
                "SELECT record_id FROM search_content "
                "WHERE record_kind='pdf_text_block' "
                "AND instr(lower(content),lower(?))>0 ORDER BY record_id LIMIT ?",
                (query, limit),
            )
        results = []
        for hit in hits:
            rows = self._rows(
                "SELECT b.*,p.page_number,d.id AS document_id,d.identity AS document_identity,"
                "d.source_filename,d.source_sha256,e.id AS evidence_id,e.pdf_page "
                "FROM pdf_text_blocks b JOIN pdf_pages p ON p.id=b.page_id "
                "JOIN documents d ON d.id=p.document_id JOIN evidence e ON e.id=b.evidence_id "
                "WHERE b.id=?",
                (hit["record_id"],),
            )
            if not rows:
                continue
            row = rows[0]
            navigation_row = dict(row)
            navigation_row.update(sheet_id=None, view_id=None)
            navigation = navigation_target(navigation_row, source_kind="evidence")
            results.append(
                {
                    "block_id": row["id"],
                    "text": row["text"],
                    "provenance": row["provenance"],
                    "document": {
                        "id": row["document_id"],
                        "identity": row["document_identity"],
                        "source_filename": row["source_filename"],
                        "source_sha256": row["source_sha256"],
                    },
                    "pdf_page": row["pdf_page"],
                    "bbox": [row[k] for k in ("x_min", "y_min", "x_max", "y_max")],
                    "coordinate_space": row["coordinate_space"],
                    "navigation": navigation,
                }
            )
        return results

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
            _APPEARANCE_CONTEXT_SELECT
            + "WHERE a.entity_kind=? AND a.entity_id=? "
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

    def get_navigation_targets(
        self, entity_kind: str, entity_id: str
    ) -> list[dict[str, Any]]:
        """Return deterministic PDF navigation descriptors for an entity."""
        rows = self._rows(
            _APPEARANCE_CONTEXT_SELECT
            + "WHERE a.entity_kind=? AND a.entity_id=?",
            (entity_kind, entity_id),
        )
        for row in rows:
            row["appearance_id"] = row["id"]
        return deduplicate_navigation(
            navigation_target(row, source_kind="entity_appearance") for row in rows
        )

    def _get_appearance_navigation(
        self, appearance_id: str
    ) -> dict[str, Any] | None:
        rows = self._rows(
            _APPEARANCE_CONTEXT_SELECT + "WHERE a.id=?",
            (appearance_id,),
        )
        if rows:
            rows[0]["appearance_id"] = rows[0]["id"]
        return (
            navigation_target(rows[0], source_kind="entity_appearance")
            if rows
            else None
        )

    def get_evidence_navigation(
        self, evidence_id: str
    ) -> dict[str, Any] | None:
        """Return a viewer-neutral descriptor for one explicit evidence record."""
        row = self.get_pdf_evidence(evidence_id)
        if row is None:
            return None
        row["evidence_id"] = row.pop("id")
        return navigation_target(row, source_kind="evidence")

    def _navigation_from_evidence(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        targets = []
        for item in items:
            if item.get("source_kind") == "evidence":
                target = self.get_evidence_navigation(item["evidence_id"])
                if target is not None:
                    targets.append(target)
            elif item.get("source_kind") == "entity_appearance":
                target = self._get_appearance_navigation(item["appearance_id"])
                if target is not None:
                    targets.append(target)
        return deduplicate_navigation(targets)

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

    def get_adjacent_spaces(
        self, space_id: str, link_instance_id: str | None = None
    ) -> dict[str, Any]:
        """Prove adjacency for one spatial occurrence from stored outer linework."""
        space = self.get_entity("space", space_id)
        occurrence = {"space_id": space_id, "link_instance_id": link_instance_id}
        empty_coverage = {
            "total_subject_outer_boundary_segments": 0,
            "usable_line_segments": 0,
            "nonlinear_segments_skipped": 0,
            "degenerate_line_segments_skipped": 0,
            "unresolved_source_segments_skipped": 0,
            "candidate_segment_pairs_evaluated": 0,
        }

        def result(status: str, warnings: list[str], **extra: Any) -> dict[str, Any]:
            return {
                "status": status,
                "space": space,
                "occurrence": occurrence,
                "adjacent_spaces": [],
                "coverage": dict(empty_coverage),
                "warnings": warnings,
                **extra,
            }

        if space is None:
            return result("not_found", [])
        occurrences = [
            row["link_instance_id"]
            for row in self._rows(
                "SELECT DISTINCT link_instance_id FROM spatial_boundaries "
                "WHERE space_id=? ORDER BY link_instance_id",
                (space_id,),
            )
        ]
        if not occurrences:
            return result("insufficient_data", ["no_spatial_boundaries"])
        if link_instance_id is None and len(occurrences) > 1:
            return result(
                "ambiguous_occurrence",
                ["multiple_spatial_occurrences"],
                available_occurrences=[
                    {"space_id": space_id, "link_instance_id": value}
                    for value in occurrences
                ],
                available_occurrence_ids=occurrences,
            )
        selected = link_instance_id if link_instance_id is not None else occurrences[0]
        occurrence["link_instance_id"] = selected
        if selected not in occurrences:
            return result("insufficient_data", ["spatial_occurrence_not_found"])
        if not self._has_columns(
            "spatial_boundary_segments", "source_link_instance_id", "curve_kind"
        ):
            return result(
                "insufficient_data", ["boundary_provenance_capability_unavailable"]
            )
        if space.get("source_model_id") is None:
            return result("insufficient_data", ["subject_source_model_unavailable"])
        if space.get("level_id") is None:
            return result("insufficient_data", ["subject_level_unavailable"])
        if space.get("phase_source_unique_id") is None:
            return result("insufficient_data", ["subject_phase_unavailable"])

        subject_segments = self._rows(
            "SELECT s.* FROM spatial_boundary_segments s "
            "JOIN spatial_boundaries b ON b.id=s.boundary_id "
            "WHERE b.space_id=? AND b.loop_kind='outer' "
            "AND b.link_instance_id IS ? ORDER BY s.id",
            (space_id, selected),
        )
        coverage = dict(empty_coverage)
        coverage["total_subject_outer_boundary_segments"] = len(subject_segments)
        usable = []
        for segment in subject_segments:
            if segment["curve_kind"] != "line":
                coverage["nonlinear_segments_skipped"] += 1
            elif not segment["source_model_id"] or not segment["source_unique_id"]:
                coverage["unresolved_source_segments_skipped"] += 1
            elif longitudinal_overlap(
                (segment["start_x"], segment["start_y"], segment["start_z"]),
                (segment["end_x"], segment["end_y"], segment["end_z"]),
                (segment["start_x"], segment["start_y"], segment["start_z"]),
                (segment["end_x"], segment["end_y"], segment["end_z"]),
            ) is None:
                coverage["degenerate_line_segments_skipped"] += 1
            else:
                usable.append(segment)
        coverage["usable_line_segments"] = len(usable)
        if not usable:
            output = result("insufficient_data", ["no_usable_outer_line_segments"])
            output["coverage"] = coverage
            return output

        discoveries: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        seen_pairs: set[tuple[str, str]] = set()
        for subject in usable:
            candidates = self._rows(
                "SELECT c.*,b.space_id,b.link_instance_id AS occurrence_link_instance_id "
                "FROM spatial_boundary_segments c "
                "JOIN spatial_boundaries b ON b.id=c.boundary_id "
                "JOIN spaces p ON p.id=b.space_id "
                "WHERE b.loop_kind='outer' AND c.curve_kind='line' "
                "AND b.space_id<>? AND b.link_instance_id IS ? "
                "AND p.source_model_id=? AND p.level_id=? "
                "AND p.phase_source_unique_id=? "
                "AND c.source_model_id=? AND c.source_unique_id=? "
                "AND c.source_link_instance_id IS ? ORDER BY b.space_id,c.id",
                (
                    space_id,
                    selected,
                    space["source_model_id"],
                    space["level_id"],
                    space["phase_source_unique_id"],
                    subject["source_model_id"],
                    subject["source_unique_id"],
                    subject["source_link_instance_id"],
                ),
            )
            for candidate in candidates:
                pair = (subject["id"], candidate["id"])
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                coverage["candidate_segment_pairs_evaluated"] += 1
                overlap = longitudinal_overlap(
                    (subject["start_x"], subject["start_y"], subject["start_z"]),
                    (subject["end_x"], subject["end_y"], subject["end_z"]),
                    (candidate["start_x"], candidate["start_y"], candidate["start_z"]),
                    (candidate["end_x"], candidate["end_y"], candidate["end_z"]),
                )
                if overlap is None:
                    continue
                key = (candidate["space_id"], candidate["occurrence_link_instance_id"])
                discoveries.setdefault(key, []).append(
                    {
                        "source_model_id": subject["source_model_id"],
                        "source_unique_id": subject["source_unique_id"],
                        "source_link_instance_id": subject["source_link_instance_id"],
                        "subject_segment_id": subject["id"],
                        "adjacent_segment_id": candidate["id"],
                        "overlap_length_mm": overlap,
                    }
                )
        adjacent = []
        for key in sorted(discoveries, key=lambda item: (item[0], item[1] or "")):
            shared = sorted(
                discoveries[key],
                key=lambda row: (
                    row["source_model_id"],
                    row["source_unique_id"],
                    row["source_link_instance_id"] or "",
                    row["subject_segment_id"],
                    row["adjacent_segment_id"],
                ),
            )
            adjacent.append(
                {
                    "space": self.get_entity("space", key[0]),
                    "occurrence": {"space_id": key[0], "link_instance_id": key[1]},
                    "relation": "adjacent",
                    "basis": "shared_boundary_source_longitudinal_overlap",
                    "shared_boundaries": shared,
                }
            )
        skipped = (
            coverage["nonlinear_segments_skipped"]
            + coverage["degenerate_line_segments_skipped"]
            + coverage["unresolved_source_segments_skipped"]
        )
        output = result(
            "partial" if skipped else "ok",
            [] if not skipped else ["incomplete_boundary_coverage"],
        )
        output["coverage"] = coverage
        output["adjacent_spaces"] = adjacent
        return output

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
            "SELECT e.*,d.identity AS document_identity,d.source_filename,d.source_sha256,s.number AS sheet_number,s.name AS sheet_name,v.name AS view_name,v.view_type FROM evidence e JOIN documents d ON d.id=e.document_id LEFT JOIN sheets s ON s.id=e.sheet_id LEFT JOIN views v ON v.id=e.view_id WHERE e.id=?",
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

        result = get_opening_width(self, entity_id)
        result["navigation"] = self._navigation_from_evidence(result.get("evidence", []))
        return result

    def get_relative_elevation(
        self, entity_id: str, reference: str | None = None
    ) -> dict[str, Any]:
        """Return an explicit relative elevation and its compact PDF evidence."""
        from .practical import get_relative_elevation

        result = get_relative_elevation(self, entity_id, reference)
        result["navigation"] = self._navigation_from_evidence(result.get("evidence", []))
        return result

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

    @staticmethod
    def _containment_context_key(context: dict[str, Any]) -> str:
        """Identify equivalent explicit containment without relying on row IDs."""
        return json.dumps(
            {key: value for key, value in context.items() if key != "relationship_id"},
            sort_keys=True,
            separators=(",", ":"),
        )

    def get_containing_spaces(self, element_id: str) -> list[dict[str, Any]]:
        """Return spaces explicitly containing an element (not From/To rooms)."""
        element = self.get_entity("element", element_id)
        if element is None:
            return []
        rows = self._rows(
            "SELECT r.* FROM relationships r JOIN spaces s ON s.id=r.target_id "
            "WHERE r.source_kind='element' AND r.source_id=? "
            "AND r.relation_type='contained_in' AND r.target_kind='space' "
            "ORDER BY r.target_id,r.phase_source_unique_id,r.id",
            (element_id,),
        )
        result: dict[str, dict[str, Any]] = {}
        seen: dict[str, set[str]] = {}

        def add(space_id: str, context: dict[str, Any]) -> None:
            if space_id not in result:
                space = self.get_entity("space", space_id)
                if space is None:
                    return
                result[space_id] = {**space, "contexts": []}
                seen[space_id] = set()
            key = self._containment_context_key(context)
            if key not in seen[space_id]:
                seen[space_id].add(key)
                result[space_id]["contexts"].append(context)

        if element.get("space_id"):
            add(
                element["space_id"],
                {
                    "relation_type": "space_id",
                    "relationship_id": None,
                    "phase_source_unique_id": None,
                    "provenance": element["provenance"],
                    "confidence": element["confidence"],
                    "evidence_id": None,
                },
            )
        for row in rows:
            add(
                row["target_id"],
                {
                    "relation_type": "contained_in",
                    "relationship_id": row["id"],
                    "phase_source_unique_id": row["phase_source_unique_id"],
                    "provenance": row["provenance"],
                    "confidence": row["confidence"],
                    "evidence_id": row["evidence_id"],
                },
            )
        return [result[key] for key in sorted(result)]

    def get_contained_elements(self, space_id: str) -> list[dict[str, Any]]:
        """Return elements explicitly assigned to or contained in a space."""
        if self.get_entity("space", space_id) is None:
            return []
        ids = {
            row["id"]
            for row in self._rows(
                "SELECT id FROM elements WHERE space_id=? ORDER BY id", (space_id,)
            )
        }
        ids.update(
            row["source_id"]
            for row in self._rows(
                "SELECT r.source_id FROM relationships r JOIN elements e ON e.id=r.source_id "
                "WHERE r.source_kind='element' AND r.target_kind='space' "
                "AND r.target_id=? AND r.relation_type='contained_in' ORDER BY r.source_id",
                (space_id,),
            )
        )
        output = []
        for element_id in sorted(ids):
            element = self.get_entity("element", element_id)
            contexts = next(
                (
                    space["contexts"]
                    for space in self.get_containing_spaces(element_id)
                    if space["id"] == space_id
                ),
                [],
            )
            if element is not None and contexts:
                output.append({**element, "contexts": contexts})
        return output

    def get_same_type_elements(self, element_id: str) -> dict[str, Any]:
        """Resolve stored type membership and deterministically return peer instances."""
        element = self.get_entity("element", element_id)
        if element is None:
            return {
                "status": "not_found",
                "element": None,
                "element_type": None,
                "elements": [],
            }
        type_id = element.get("type_id")
        element_type = self.get_entity("element_type", type_id) if type_id else None
        if element_type is None:
            return {
                "status": "insufficient_data",
                "element": element,
                "element_type": None,
                "elements": [],
            }
        return {
            "status": "ok",
            "element": element,
            "element_type": element_type,
            "elements": [
                sibling
                for sibling in self.get_type_instances(type_id)
                if sibling["id"] != element_id
            ],
        }

    def get_same_space_elements(self, element_id: str) -> dict[str, Any]:
        """Return membership per explicit containment context, preserving ambiguity."""
        element = self.get_entity("element", element_id)
        if element is None:
            return {"status": "not_found", "element": None, "spaces": []}
        spaces = []
        for space in self.get_containing_spaces(element_id):
            spaces.append(
                {
                    "space": {
                        key: value for key, value in space.items() if key != "contexts"
                    },
                    "contexts": space["contexts"],
                    "elements": self.get_contained_elements(space["id"]),
                }
            )
        return {
            "status": "ok" if spaces else "insufficient_data",
            "element": element,
            "spaces": spaces,
        }

    def get_space_connections(self, space_id: str) -> list[dict[str, Any]]:
        """Return phase-safe connections represented by stored From/To relationships."""
        if self.get_entity("space", space_id) is None:
            return []
        rows = self._rows(
            "SELECT r.* FROM relationships r JOIN elements e ON e.id=r.source_id "
            "WHERE r.source_kind='element' AND r.target_kind='space' "
            "AND r.relation_type IN ('from_space','to_space') "
            "AND (r.target_id=? OR EXISTS (SELECT 1 FROM relationships peer "
            "WHERE peer.source_kind='element' AND peer.source_id=r.source_id "
            "AND peer.target_kind='space' AND peer.target_id=? "
            "AND peer.relation_type IN ('from_space','to_space') "
            "AND r.phase_source_unique_id IS NOT NULL "
            "AND peer.phase_source_unique_id=r.phase_source_unique_id)) "
            "ORDER BY r.source_id,r.phase_source_unique_id,r.relation_type,r.id",
            (space_id, space_id),
        )
        groups: dict[tuple[str, str | None, str], list[dict[str, Any]]] = {}
        for row in rows:
            # An unknown phase is not evidence that two sides share a phase. Keep
            # each such relationship as its own traceable partial connection.
            unknown_phase_key = row["id"] if row["phase_source_unique_id"] is None else ""
            groups.setdefault(
                (
                    row["source_id"],
                    row["phase_source_unique_id"],
                    unknown_phase_key,
                ),
                [],
            ).append(row)
        output = []
        for (connector_id, phase_id, unknown_phase_key), relations in sorted(
            groups.items(),
            key=lambda item: (item[0][0], item[0][1] or "", item[0][2]),
        ):
            sides: dict[str, dict[str, Any] | None] = {
                "from_space": None,
                "to_space": None,
            }
            for side in sides:
                candidates = sorted(
                    {
                        row["target_id"]
                        for row in relations
                        if row["relation_type"] == side
                    }
                )
                if len(candidates) == 1:
                    sides[side] = self.get_entity("space", candidates[0])
            connector = self.get_entity("element", connector_id)
            connector_type = (
                self.get_entity("element_type", connector.get("type_id"))
                if connector and connector.get("type_id")
                else None
            )
            complete = phase_id is not None and all(sides.values()) and all(
                len({r["target_id"] for r in relations if r["relation_type"] == side})
                == 1
                for side in sides
            )
            connected = None
            if complete:
                if sides["from_space"]["id"] == space_id:
                    connected = sides["to_space"]
                elif sides["to_space"]["id"] == space_id:
                    connected = sides["from_space"]
            connection = {
                "status": "complete" if complete else "partial",
                "connector_element": connector,
                "connector_type": connector_type,
                "from_space": sides["from_space"],
                "to_space": sides["to_space"],
                "connected_space": connected,
                "phase_source_unique_id": phase_id,
                "relationship_ids": sorted(row["id"] for row in relations),
                "relationships": [
                    {
                        "id": row["id"],
                        "relation_type": row["relation_type"],
                        "provenance": row["provenance"],
                        "confidence": row["confidence"],
                    }
                    for row in relations
                ],
                "navigation": self.get_navigation_targets("element", connector_id),
            }
            if unknown_phase_key:
                connection["warnings"] = ["phase_context_missing"]
            output.append(connection)
        return output

    def get_level_difference(
        self, kind_a: str, id_a: str, kind_b: str, id_b: str
    ) -> dict[str, Any]:
        """Subtract two explicitly stored level elevations (A minus B)."""
        entity_a = self.get_entity(kind_a, id_a)
        entity_b = self.get_entity(kind_b, id_b)
        level_a = (
            entity_a
            if kind_a == "level"
            else (
                self.get_entity("level", entity_a.get("level_id"))
                if entity_a and entity_a.get("level_id")
                else None
            )
        )
        level_b = (
            entity_b
            if kind_b == "level"
            else (
                self.get_entity("level", entity_b.get("level_id"))
                if entity_b and entity_b.get("level_id")
                else None
            )
        )
        valid = (
            level_a is not None
            and level_b is not None
            and level_a.get("elevation") is not None
            and level_b.get("elevation") is not None
            and level_a.get("source_model_id") is not None
            and level_a.get("source_model_id") == level_b.get("source_model_id")
            and level_a.get("unit") is not None
            and level_a.get("unit") == level_b.get("unit")
        )
        if not valid:
            return {
                "status": "insufficient_data",
                "level_a": level_a,
                "level_b": level_b,
                "signed_difference": None,
                "absolute_difference": None,
                "unit": None,
            }
        difference = level_a["elevation"] - level_b["elevation"]
        return {
            "status": "ok",
            "level_a": level_a,
            "level_b": level_b,
            "signed_difference": difference,
            "absolute_difference": abs(difference),
            "unit": level_a["unit"],
        }

    def get_spatial_context(self, entity_kind: str, entity_id: str) -> dict[str, Any]:
        """Compose the explicit application-facing context from focused APIs."""
        entity = self.get_entity(entity_kind, entity_id)
        if entity is None:
            return {"status": "not_found", "entity": None}
        level = (
            self.get_entity("level", entity.get("level_id"))
            if entity.get("level_id")
            else None
        )
        if entity_kind == "element":
            return {
                "status": "ok",
                "entity": entity,
                "containment": self.get_containing_spaces(entity_id),
                "type_siblings": self.get_same_type_elements(entity_id),
                "level": level,
            }
        if entity_kind == "space":
            return {
                "status": "ok",
                "entity": entity,
                "contained_elements": self.get_contained_elements(entity_id),
                "connections": self.get_space_connections(entity_id),
                "adjacency": self.get_adjacent_spaces(entity_id),
                "level": level,
            }
        raise QueryCoreError(
            f"spatial context is unsupported for entity kind: {entity_kind}"
        )

    def get_related_spaces(self, entity_id: str) -> list[dict[str, Any]]:
        """Return unique spaces explicitly stored for an element, without inference."""
        element = self.get_entity("element", entity_id)
        if element is None:
            return []
        rows = self._rows(
            "SELECT r.* FROM relationships r JOIN spaces s ON s.id=r.target_id "
            "WHERE r.source_kind='element' AND r.source_id=? "
            "AND r.target_kind='space' "
            "AND r.relation_type IN ('from_space','to_space','contained_in') "
            "ORDER BY r.target_id,r.relation_type,r.phase_source_unique_id,r.id",
            (entity_id,),
        )
        spaces: dict[str, dict[str, Any]] = {}
        context_keys: dict[str, set[str]] = {}

        def add_context(space_id: str, context: dict[str, Any]) -> None:
            space = spaces.get(space_id)
            if space is None:
                stored_space = self.get_entity("space", space_id)
                if stored_space is None:
                    return
                space = {**stored_space, "contexts": []}
                spaces[space_id] = space
                context_keys[space_id] = set()
            key = json.dumps(
                {key: value for key, value in context.items() if key != "relationship_id"},
                sort_keys=True,
                separators=(",", ":"),
            )
            if key not in context_keys[space_id]:
                context_keys[space_id].add(key)
                space["contexts"].append(context)

        if element.get("space_id"):
            add_context(
                element["space_id"],
                {
                    "instance_id": entity_id,
                    "relation_type": "space_id",
                    "relationship_id": None,
                    "phase_source_unique_id": None,
                    "provenance": element["provenance"],
                    "confidence": element["confidence"],
                    "evidence_id": None,
                },
            )
        for row in rows:
            add_context(
                row["target_id"],
                {
                    "instance_id": entity_id,
                    "relation_type": row["relation_type"],
                    "relationship_id": row["id"],
                    "phase_source_unique_id": row.get("phase_source_unique_id"),
                    "provenance": row["provenance"],
                    "confidence": row["confidence"],
                    "evidence_id": row["evidence_id"],
                },
            )
        return [spaces[space_id] for space_id in sorted(spaces)]

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
                "navigation": [],
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
                "navigation": [],
                "coverage": {
                    "total_instances": 0,
                    "instances_with_spatial_context": 0,
                    "instances_with_drawing_occurrence": 0,
                },
                "warnings": ["element_type_not_stored"],
            }

        instances = self.get_type_instances(element_type["id"])
        spaces_by_id: dict[str, dict[str, Any]] = {}
        space_context_keys: dict[str, set[str]] = {}
        occurrences: list[dict[str, Any]] = []
        occurrence_keys: set[str] = set()
        spatial_instance_ids: set[str] = set()
        drawing_instance_ids: set[str] = set()
        for instance in instances:
            spatial = self.get_related_spaces(instance["id"])
            if spatial:
                spatial_instance_ids.add(instance["id"])
                for space in spatial:
                    aggregate = spaces_by_id.setdefault(
                        space["id"],
                        {key: value for key, value in space.items() if key != "contexts"}
                        | {"contexts": []},
                    )
                    seen = space_context_keys.setdefault(space["id"], set())
                    for context in space["contexts"]:
                        key = json.dumps(
                            {
                                name: value
                                for name, value in context.items()
                                if name != "relationship_id"
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if key not in seen:
                            seen.add(key)
                            aggregate["contexts"].append(context)
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
            "related_spaces": [spaces_by_id[key] for key in sorted(spaces_by_id)],
            "drawing_occurrences": occurrences,
            "navigation": self._navigation_from_evidence(
                [
                    {
                        "source_kind": "entity_appearance",
                        "appearance_id": occurrence["appearance_id"],
                    }
                    for occurrence in occurrences
                ]
            ),
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

    def get_entity_geometries(
        self, entity_kind: str, entity_id: str, link_instance_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return stored geometry verbatim (apart from decoding its JSON value)."""
        sql = "SELECT * FROM geometries WHERE entity_kind=? AND entity_id=?"
        values: tuple[Any, ...] = (entity_kind, entity_id)
        if link_instance_id is not None:
            sql += " AND link_instance_id=?"
            values += (link_instance_id,)
        rows = self._rows(sql + " ORDER BY link_instance_id,geometry_type,id", values)
        for row in rows:
            row["geometry"] = json.loads(row.pop("geometry_json"))
        return self._with_evidence(rows)

    @staticmethod
    def _occurrence(kind: str, entity_id: str, link_instance_id: str | None) -> dict[str, Any]:
        return {"entity_kind": kind, "entity_id": entity_id, "link_instance_id": link_instance_id}

    def _resolve_geometry_occurrence(
        self, kind: str, entity_id: str, link_instance_id: str | None, geometry_type: str,
        occurrence_is_selected: bool = False,
    ) -> dict[str, Any]:
        entity = self.get_entity(kind, entity_id)
        if entity is None:
            return {"status": "not_found", "entity": {"kind": kind, "id": entity_id}}
        all_rows = self.get_entity_geometries(kind, entity_id)
        occurrence_ids = sorted({row["link_instance_id"] for row in all_rows}, key=lambda value: (value is not None, value or ""))
        if link_instance_id is None and not occurrence_is_selected and len(occurrence_ids) > 1:
            return {"status": "ambiguous_occurrence", "available_occurrence_ids": occurrence_ids}
        selected_id = link_instance_id if link_instance_id is not None else (occurrence_ids[0] if occurrence_ids else None)
        rows = [row for row in all_rows if row["link_instance_id"] == selected_id]
        occurrence = self._occurrence(kind, entity_id, selected_id)
        if geometry_type == "location":
            usable = [(row, decode_location_primitive(row["geometry_type"], row["geometry"])) for row in rows]
            usable = [(row, primitive) for row, primitive in usable if primitive is not None]
            if len(usable) > 1:
                return {"status": "ambiguous_geometry", "occurrence": occurrence, "geometry_ids": [row["id"] for row, _ in usable]}
        else:
            usable = [(row, row["geometry"]) for row in rows if row["geometry_type"] == "bbox3d" and isinstance(row["geometry"], dict)]
            if len(usable) > 1:
                return {"status": "ambiguous_geometry", "occurrence": occurrence, "geometry_ids": [row["id"] for row, _ in usable]}
        if not usable:
            return {"status": "insufficient_data", "occurrence": occurrence}
        row, primitive = usable[0]
        return {"status": "ok", "occurrence": occurrence, "row": row, "primitive": primitive}

    def get_location_distance(self, kind_a: str, id_a: str, kind_b: str, id_b: str,
                              link_instance_id_a: str | None = None,
                              link_instance_id_b: str | None = None) -> dict[str, Any]:
        a = self._resolve_geometry_occurrence(kind_a, id_a, link_instance_id_a, "location")
        b = self._resolve_geometry_occurrence(kind_b, id_b, link_instance_id_b, "location")
        base = {"entity_a": {"kind": kind_a, "id": id_a}, "entity_b": {"kind": kind_b, "id": id_b}}
        for label, resolved in (("a", a), ("b", b)):
            if resolved["status"] != "ok":
                return {"status": resolved["status"], **base, f"occurrence_{label}": resolved.get("occurrence"), **{key: value for key, value in resolved.items() if key not in {"status", "occurrence"}}}
        if a["row"]["coordinate_system"] != b["row"]["coordinate_system"] or a["row"]["unit"] != b["row"]["unit"]:
            return {"status": "insufficient_data", **base, "occurrence_a": a["occurrence"], "occurrence_b": b["occurrence"], "reason": "incompatible_coordinate_system_or_unit"}
        return {"status": "ok", **base, "occurrence_a": a["occurrence"], "occurrence_b": b["occurrence"],
                "distance": primitive_distance(a["primitive"], b["primitive"]), "unit": a["row"]["unit"],
                "geometry_basis": f'{a["row"]["geometry_type"]}_to_{b["row"]["geometry_type"]}',
                "geometry_ids": [a["row"]["id"], b["row"]["id"]],
                "provenance": [a["row"]["provenance"], b["row"]["provenance"]]}

    def find_nearby_by_location(self, entity_kind: str, entity_id: str, radius_mm: float,
                                link_instance_id: str | None = None,
                                target_kind: str | None = None) -> dict[str, Any]:
        if isinstance(radius_mm, bool) or not isinstance(radius_mm, (int, float)) or not math.isfinite(radius_mm) or radius_mm < 0:
            raise QueryCoreError("radius_mm must be finite and >= 0")
        subject = self._resolve_geometry_occurrence(entity_kind, entity_id, link_instance_id, "location")
        if subject["status"] != "ok":
            return {key: value for key, value in subject.items() if key not in {"row", "primitive"}}
        bounds = primitive_bounds(subject["primitive"])
        expanded = tuple(value + (-radius_mm if index % 2 == 0 else radius_mm) for index, value in enumerate(bounds))
        candidates = self.get_spatial_candidates(expanded)
        identities = sorted({(row["entity_kind"], row["entity_id"], row["link_instance_id"]) for row in candidates
                             if (target_kind is None or row["entity_kind"] == target_kind)
                             and (row["entity_kind"], row["entity_id"], row["link_instance_id"]) != (entity_kind, entity_id, subject["occurrence"]["link_instance_id"])},
                            key=lambda value: (value[0], value[1], value[2] or ""))
        results, skipped = [], []
        for kind, candidate_id, instance_id in identities:
            candidate = self._resolve_geometry_occurrence(kind, candidate_id, instance_id, "location", True)
            if candidate["status"] != "ok" or candidate["row"]["coordinate_system"] != subject["row"]["coordinate_system"] or candidate["row"]["unit"] != subject["row"]["unit"]:
                skipped.append({"occurrence": self._occurrence(kind, candidate_id, instance_id), "reason": candidate["status"] if candidate["status"] != "ok" else "incompatible_coordinate_system_or_unit"})
                continue
            distance = primitive_distance(subject["primitive"], candidate["primitive"])
            if distance <= radius_mm:
                results.append({"entity": {"kind": kind, "id": candidate_id}, "occurrence": candidate["occurrence"], "distance": distance,
                                "unit": subject["row"]["unit"], "geometry_ids": [subject["row"]["id"], candidate["row"]["id"]],
                                "geometry_basis": f'{subject["row"]["geometry_type"]}_to_{candidate["row"]["geometry_type"]}',
                                "provenance": [subject["row"]["provenance"], candidate["row"]["provenance"]]})
        results.sort(key=lambda item: (item["distance"], item["entity"]["kind"], item["entity"]["id"], item["occurrence"]["link_instance_id"] or ""))
        return {"status": "ok", "subject": subject["occurrence"], "radius_mm": radius_mm, "results": results,
                "coverage": {"indexed_occurrences": len(identities), "exact_results": len(results), "skipped": skipped}}

    def get_nearest_by_location(self, entity_kind: str, entity_id: str, max_radius_mm: float,
                                link_instance_id: str | None = None, target_kind: str | None = None) -> dict[str, Any]:
        nearby = self.find_nearby_by_location(entity_kind, entity_id, max_radius_mm, link_instance_id, target_kind)
        if nearby["status"] != "ok":
            return nearby
        if nearby["results"]:
            return {"status": "ok", "subject": nearby["subject"], "nearest": nearby["results"][0], "max_radius_mm": max_radius_mm, "coverage": nearby["coverage"]}
        return {"status": "insufficient_data" if nearby["coverage"]["skipped"] else "no_match", "subject": nearby["subject"], "nearest": None, "max_radius_mm": max_radius_mm, "coverage": nearby["coverage"]}

    def get_vertical_relation(self, kind_a: str, id_a: str, kind_b: str, id_b: str,
                              link_instance_id_a: str | None = None,
                              link_instance_id_b: str | None = None) -> dict[str, Any]:
        a = self._resolve_geometry_occurrence(kind_a, id_a, link_instance_id_a, "bbox3d")
        b = self._resolve_geometry_occurrence(kind_b, id_b, link_instance_id_b, "bbox3d")
        base = {"occurrence_a": a.get("occurrence"), "occurrence_b": b.get("occurrence"), "geometry_basis": "bbox3d_separation"}
        for resolved in (a, b):
            if resolved["status"] != "ok":
                return {"status": resolved["status"], **base, **{key: value for key, value in resolved.items() if key not in {"status", "occurrence"}}}
        if a["row"]["coordinate_system"] != b["row"]["coordinate_system"] or a["row"]["unit"] != b["row"]["unit"]:
            return {"status": "insufficient_data", **base, "reason": "incompatible_coordinate_system_or_unit"}
        amin, amax, bmin, bmax = a["row"]["min_z"], a["row"]["max_z"], b["row"]["min_z"], b["row"]["max_z"]
        relation, separation = ("above", amin - bmax) if amin > bmax else (("below", bmin - amax) if amax < bmin else ("indeterminate", None))
        return {"status": "ok", **base, "relation": relation, "z_separation": separation, "unit": a["row"]["unit"], "geometry_ids": [a["row"]["id"], b["row"]["id"]]}
