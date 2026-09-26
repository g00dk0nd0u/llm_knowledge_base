from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .errors import QueryCoreError
from .geometry import decode_location_primitive, primitive_bounds

SCHEMA_VERSION = 2
GENERATOR_VERSION = "query-core/2.0"
TABLES = (
    "documents",
    "pdf_pages",
    "source_models",
    "link_instances",
    "sheets",
    "views",
    "viewports",
    "levels",
    "spaces",
    "element_types",
    "elements",
    "evidence",
    "pdf_text_blocks",
    "pdf_text_lines",
    "pdf_text_spans",
    "pdf_tables",
    "pdf_table_cells",
    "pdf_table_cell_spans",
    "parameters",
    "relationships",
    "semantic_entities",
    "semantic_bindings",
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
    binding_mode = records.get("binding_mode", "single_document")
    if binding_mode not in {"single_document", "project"}:
        raise QueryCoreError("binding_mode must be single_document or project")
    required = {"project_id", "created_from"}
    if binding_mode == "single_document":
        required.update({"source_document_identity", "source_document_sha256"})
    missing = sorted(required - records.keys())
    if missing:
        raise QueryCoreError(f"missing metadata: {', '.join(missing)}")
    if binding_mode == "single_document":
        _validate_sha256(records["source_document_sha256"], "source_document_sha256")
    else:
        for document in records.get("documents", []):
            identity = document.get("identity")
            if not isinstance(identity, str) or not identity.strip():
                raise QueryCoreError("document identity must be a non-empty string")
            _validate_sha256(document.get("source_sha256"), "document source_sha256")
    for page in records.get("pdf_pages", []):
        for key in ("width_points", "height_points"):
            value = page.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise QueryCoreError(f"pdf page {key} must be finite and positive")
    model_rows = records.get("source_models", [])
    source_models = {row.get("id"): row for row in model_rows}
    spaces = {row.get("id"): row for row in records.get("spaces", [])}
    # Keep this allow-list explicit: optional source-neutral capabilities must not
    # make a PDF-only/project-only build look like a Revit snapshot.
    revit_tables = {
        "link_instances", "levels", "spaces", "element_types", "elements",
        "views", "sheets", "viewports", "parameters", "relationships",
        "annotations", "annotation_segments", "annotation_references",
        "entity_appearances", "spatial_boundaries",
        "spatial_boundary_segments", "geometries",
    }
    uses_revit = bool(model_rows) or any(
        records.get(table, []) for table in revit_tables
    )
    hosts = [row for row in model_rows if row.get("role") == "host"]
    if uses_revit and len(hosts) != 1:
        raise QueryCoreError("snapshot must contain exactly one host source model")
    semantic_ids = {
        row.get("id") for row in records.get("semantic_entities", [])
    }
    for binding in records.get("semantic_bindings", []):
        if binding.get("semantic_entity_id") not in semantic_ids:
            raise QueryCoreError(
                "semantic binding references nonexistent semantic entity"
            )
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
    link_instances: dict[str, dict[str, Any]] = {}
    instance_identities: set[tuple[str, str]] = set()
    for row in records.get("link_instances", []):
        host = source_models.get(row.get("host_source_model_id"))
        linked = source_models.get(row.get("linked_source_model_id"))
        if host is None or host.get("role") != "host":
            raise QueryCoreError(
                f"link instance has invalid host source model: {row.get('id')}"
            )
        if linked is None or linked.get("role") != "link":
            raise QueryCoreError(
                f"link instance has invalid linked source model: {row.get('id')}"
            )
        if (
            not isinstance(row.get("source_unique_id"), str)
            or not row["source_unique_id"].strip()
        ):
            raise QueryCoreError(
                f"link instance requires source_unique_id: {row.get('id')}"
            )
        identity = (row["host_source_model_id"], row["source_unique_id"])
        if identity in instance_identities:
            raise QueryCoreError(
                f"duplicate link instance identity: {identity[0]}/{identity[1]}"
            )
        instance_identities.add(identity)
        _validate_transform(row.get("transform_to_host"), row.get("id"))
        link_instances[row["id"]] = row
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
    _validate_order(records, "pdf_text_blocks", "page_id", "order_index")
    _validate_order(records, "pdf_text_lines", "block_id", "order_index")
    _validate_order(records, "pdf_text_spans", "line_id", "order_index")
    _validate_order(records, "pdf_tables", "page_id", "order_index")
    _validate_order(records, "pdf_table_cell_spans", "cell_id", "order_index")
    tables = {row.get("id"): row for row in records.get("pdf_tables", [])}
    cells: dict[str, list[dict[str, Any]]] = {}
    for cell in records.get("pdf_table_cells", []):
        table = tables.get(cell.get("table_id"))
        if table is None:
            raise QueryCoreError("PDF table cell references nonexistent table")
        cells.setdefault(cell["table_id"], []).append(cell)
    for table_id, table in tables.items():
        ordered = sorted(cells.get(table_id, []), key=lambda row: (row.get("row_index"), row.get("column_index")))
        expected = [(r, c) for r in range(table["row_count"]) for c in range(table["column_count"])]
        if [(row.get("row_index"), row.get("column_index")) for row in ordered] != expected:
            raise QueryCoreError("PDF table cells must form a complete row-major grid")
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
        if row.get("geometry_type") in {"point", "line"}:
            primitive = decode_location_primitive(
                row["geometry_type"], row.get("geometry")
            )
            if primitive is None:
                raise QueryCoreError(
                    f"malformed location geometry: {row.get('id')}"
                )
            keys = ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z")
            bounds = tuple(row.get(key) for key in keys)
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in bounds
            ):
                raise QueryCoreError(
                    f"malformed indexed geometry bounds: {row.get('id')}"
                )
            exact_bounds = primitive_bounds(primitive)
            if any(
                bounds[index] > bounds[index + 1]
                or bounds[index] > exact_bounds[index]
                or bounds[index + 1] < exact_bounds[index + 1]
                for index in (0, 2, 4)
            ):
                raise QueryCoreError(
                    f"indexed bounds do not enclose location geometry: {row.get('id')}"
                )
        instance_id = row.get("link_instance_id")
        if instance_id is not None:
            instance = link_instances.get(instance_id)
            if instance is None:
                raise QueryCoreError(
                    f"geometry references nonexistent link instance: {instance_id}"
                )
            if row.get("entity_kind") == "element":
                element = next(
                    (
                        item
                        for item in records.get("elements", [])
                        if item.get("id") == row.get("entity_id")
                    ),
                    None,
                )
                if (
                    element is not None
                    and element.get("source_model_id")
                    != instance["linked_source_model_id"]
                ):
                    raise QueryCoreError(
                        f"geometry link instance model mismatch: {row.get('id')}"
                    )
    for row in records.get("annotation_references", []):
        model = row.get("target_source_model_id")
        if model is not None and model not in source_models:
            raise QueryCoreError(f"reference to nonexistent source model: {model}")
        instance_id = row.get("target_link_instance_id")
        if row.get("is_linked") == 1 and row.get("resolution_state") == "resolved":
            if (
                model is None
                or not row.get("target_source_unique_id")
                or instance_id is None
            ):
                raise QueryCoreError(
                    f"resolved linked reference requires model, element, and link instance: {row.get('id')}"
                )
        if instance_id is not None:
            instance = link_instances.get(instance_id)
            if instance is None:
                raise QueryCoreError(
                    f"reference to nonexistent link instance: {instance_id}"
                )
            if model != instance["linked_source_model_id"]:
                raise QueryCoreError(
                    f"annotation reference link instance model mismatch: {row.get('id')}"
                )
    for row in records.get("entity_appearances", []):
        instance_id = row.get("link_instance_id")
        if instance_id is not None:
            instance = link_instances.get(instance_id)
            if instance is None:
                raise QueryCoreError(
                    f"appearance references nonexistent link instance: {instance_id}"
                )
            element = next(
                (
                    item
                    for item in records.get("elements", [])
                    if item.get("id") == row.get("entity_id")
                ),
                None,
            )
            if (
                row.get("entity_kind") != "element"
                or element is None
                or element.get("source_model_id")
                != instance["linked_source_model_id"]
            ):
                raise QueryCoreError(
                    f"appearance link instance model mismatch: {row.get('id')}"
                )
    _validate_order(records, "annotation_segments", "annotation_id", "segment_index")
    _validate_order(
        records, "annotation_references", "annotation_id", "reference_index"
    )
    _validate_order(
        records,
        "spatial_boundaries",
        ("space_id", "link_instance_id"),
        "loop_index",
    )
    _validate_order(
        records, "spatial_boundary_segments", "boundary_id", "segment_index"
    )
    boundaries = {
        row.get("id"): row for row in records.get("spatial_boundaries", [])
    }
    for boundary in records.get("spatial_boundaries", []):
        space = spaces.get(boundary.get("space_id"))
        if space is None:
            raise QueryCoreError(
                f"boundary references nonexistent space: {boundary.get('space_id')}"
            )
        instance_id = boundary.get("link_instance_id")
        space_model = source_models.get(space.get("source_model_id"))
        if instance_id is None:
            if space_model is not None and space_model.get("role") == "link":
                raise QueryCoreError(
                    f"linked space boundary requires link instance: {boundary.get('id')}"
                )
        else:
            instance = link_instances.get(instance_id)
            if instance is None:
                raise QueryCoreError(
                    f"boundary references nonexistent link instance: {instance_id}"
                )
            if space.get("source_model_id") != instance["linked_source_model_id"]:
                raise QueryCoreError(
                    f"boundary link instance model mismatch: {boundary.get('id')}"
                )
    boundary_segments: dict[str, list[dict[str, Any]]] = {}
    for row in records.get("spatial_boundary_segments", []):
        boundary_segments.setdefault(row["boundary_id"], []).append(row)
        boundary = boundaries.get(row["boundary_id"])
        if boundary is None:
            raise QueryCoreError(
                f"segment references nonexistent spatial boundary: {row['boundary_id']}"
            )
        if row.get("link_instance_id") != boundary.get("link_instance_id"):
            raise QueryCoreError(
                f"boundary segment link instance mismatch: {row.get('id')}"
            )
        model = row.get("source_model_id")
        if model is not None and model not in source_models:
            raise QueryCoreError(f"reference to nonexistent source model: {model}")
        if (model is None) != (row.get("source_unique_id") is None):
            raise QueryCoreError(
                f"boundary segment source identity must be complete: {row.get('id')}"
            )
        space = spaces[boundary["space_id"]]
        source_instance_id = row.get("source_link_instance_id")
        if source_instance_id is None and model is not None and model != space.get(
            "source_model_id"
        ):
            raise QueryCoreError(
                f"boundary segment source model mismatch: {row.get('id')}"
            )
        if source_instance_id is not None:
            source_instance = link_instances.get(source_instance_id)
            if source_instance is None:
                raise QueryCoreError(
                    "boundary segment references nonexistent source link instance: "
                    f"{source_instance_id}"
                )
            if model != source_instance["linked_source_model_id"]:
                raise QueryCoreError(
                    f"boundary segment source link model mismatch: {row.get('id')}"
                )
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
    records: dict[str, Any],
    table: str,
    parent_key: str | tuple[str, ...],
    index_key: str,
) -> None:
    grouped: dict[Any, list[int]] = {}
    for row in records.get(table, []):
        parent = (
            tuple(row.get(key) for key in parent_key)
            if isinstance(parent_key, tuple)
            else row[parent_key]
        )
        grouped.setdefault(parent, []).append(row[index_key])
    for parent, indexes in grouped.items():
        if sorted(indexes) != list(range(len(indexes))):
            raise QueryCoreError(f"{table} ordering error for {parent}")


def _validate_sha256(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise QueryCoreError(f"{label} must be a lowercase SHA-256")


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


def _initialize_database(
    connection: sqlite3.Connection, metadata: dict[str, str]
) -> None:
    connection.executescript(
        (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    )
    connection.executemany(
        "INSERT INTO metadata VALUES (?,?)", sorted(metadata.items())
    )
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _insert_records(connection: sqlite3.Connection, records: dict[str, Any]) -> None:
    """Validate and insert one canonical bundle into an initialized database."""
    _validate(records)
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
            elif table == "link_instances" and "transform_to_host" in values:
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


def _metadata(records: dict[str, Any]) -> dict[str, str]:
    metadata = {
        "schema_version": str(SCHEMA_VERSION),
        "generator_version": GENERATOR_VERSION,
        "project_id": records["project_id"],
        "created_from": records["created_from"],
        "binding_mode": records.get("binding_mode", "single_document"),
    }
    if metadata["binding_mode"] == "single_document":
        metadata.update(
            source_document_identity=records["source_document_identity"],
            source_document_sha256=records["source_document_sha256"],
        )
    return metadata


def build_database(records: dict[str, Any], output: Path) -> Path:
    """Atomically build schema v2 from validated canonical records."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        _initialize_database(connection, _metadata(records))
        _insert_records(connection, records)
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
