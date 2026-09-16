from __future__ import annotations

import hashlib
import json
from pathlib import Path

import fitz

from .build import build_database


def create_synthetic_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    for title in ("A-312 Loading Dock", "A-421 Office Roof", "A-201 Level 2 Data Hall"):
        page = document.new_page(width=842, height=595)
        page.insert_text((72, 72), title)
    document.save(path)
    document.close()
    return path


def synthetic_records(source_pdf: Path) -> dict:
    source_hash = hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    provenance = "synthetic_fixture"
    records = {
        "project_id": "synthetic-architecture",
        "created_from": "programmatic synthetic fixture",
        "source_document_identity": "synthetic-drawings-v1",
        "source_document_sha256": source_hash,
        "documents": [
            {
                "id": "doc-drawings",
                "identity": "synthetic-drawings-v1",
                "title": "Synthetic Architectural Drawings",
                "source_filename": source_pdf.name,
                "source_sha256": source_hash,
            }
        ],
        "sheets": [
            {
                "id": "sheet-a312",
                "document_id": "doc-drawings",
                "number": "A-312",
                "name": "Loading Dock Details",
                "pdf_page": 1,
            },
            {
                "id": "sheet-a421",
                "document_id": "doc-drawings",
                "number": "A-421",
                "name": "Roof Details",
                "pdf_page": 2,
            },
            {
                "id": "sheet-a201",
                "document_id": "doc-drawings",
                "number": "A-201",
                "name": "Level 2 Plan データホール",
                "pdf_page": 3,
            },
        ],
        "views": [
            {
                "id": "view-dock",
                "document_id": "doc-drawings",
                "name": "Loading Dock Elevation",
                "view_type": "elevation",
            },
            {
                "id": "view-roof",
                "document_id": "doc-drawings",
                "name": "Office Roof Section",
                "view_type": "section",
            },
            {
                "id": "view-level2",
                "document_id": "doc-drawings",
                "name": "Level 2 Data Hall Plan",
                "view_type": "plan",
            },
        ],
        "viewports": [
            {
                "id": "vp-dock",
                "sheet_id": "sheet-a312",
                "view_id": "view-dock",
                "x_min": 50,
                "y_min": 80,
                "x_max": 780,
                "y_max": 540,
                "coordinate_space": "pdf_points_top_left",
            }
        ],
        "levels": [
            {
                "id": "level-2",
                "name": "Level 2",
                "elevation": 6000,
                "unit": "mm",
                "source_id": "synthetic-level-2",
                "provenance": provenance,
                "confidence": None,
            }
        ],
        "spaces": [
            {
                "id": "space-hall-a",
                "kind": "Space",
                "name": "Data Hall A",
                "number": "201",
                "level_id": "level-2",
                "source_id": "synthetic-space-a",
                "provenance": provenance,
                "confidence": None,
            },
            {
                "id": "space-hall-b",
                "kind": "Space",
                "name": "Data Hall B",
                "number": "202",
                "level_id": "level-2",
                "source_id": "synthetic-space-b",
                "provenance": provenance,
                "confidence": None,
            },
            {
                "id": "space-corridor",
                "kind": "Corridor",
                "name": "Level 2 Data-hall Corridor",
                "number": "C-2",
                "level_id": "level-2",
                "source_id": "synthetic-corridor",
                "provenance": provenance,
                "confidence": None,
            },
        ],
        "element_types": [
            {
                "id": "type-shutter",
                "name": "Industrial Roller Shutter",
                "category": "Door",
                "source_id": "synthetic-type-shutter",
                "provenance": provenance,
                "confidence": None,
            }
        ],
        "elements": [
            {
                "id": "element-dl03",
                "name": "Dock Leveler DL-03",
                "category": "Dock Equipment",
                "type_id": None,
                "space_id": None,
                "level_id": "level-2",
                "source_id": "synthetic-dl03",
                "provenance": provenance,
                "confidence": None,
            },
            {
                "id": "element-sd03",
                "name": "Shutter SD-03",
                "category": "Door",
                "type_id": "type-shutter",
                "space_id": None,
                "level_id": "level-2",
                "source_id": "synthetic-sd03",
                "provenance": provenance,
                "confidence": None,
            },
            {
                "id": "element-roof",
                "name": "Office Roof",
                "category": "Roof",
                "type_id": None,
                "space_id": None,
                "level_id": None,
                "source_id": "synthetic-office-roof",
                "provenance": provenance,
                "confidence": None,
            },
            {
                "id": "wall-north",
                "name": "Corridor North Boundary",
                "category": "Wall",
                "type_id": None,
                "space_id": None,
                "level_id": "level-2",
                "source_id": "synthetic-wall-n",
                "provenance": provenance,
                "confidence": None,
            },
            {
                "id": "wall-south",
                "name": "Corridor South Boundary",
                "category": "Wall",
                "type_id": None,
                "space_id": None,
                "level_id": "level-2",
                "source_id": "synthetic-wall-s",
                "provenance": provenance,
                "confidence": None,
            },
        ],
        "evidence": [
            {
                "id": "ev-shutter",
                "document_id": "doc-drawings",
                "sheet_id": "sheet-a312",
                "view_id": "view-dock",
                "pdf_page": 1,
                "x_min": 120,
                "y_min": 150,
                "x_max": 330,
                "y_max": 240,
                "coordinate_space": "pdf_points_top_left",
            },
            {
                "id": "ev-roof",
                "document_id": "doc-drawings",
                "sheet_id": "sheet-a421",
                "view_id": "view-roof",
                "pdf_page": 2,
                "x_min": 410,
                "y_min": 110,
                "x_max": 560,
                "y_max": 180,
                "coordinate_space": "pdf_points_top_left",
            },
            {
                "id": "ev-corridor",
                "document_id": "doc-drawings",
                "sheet_id": "sheet-a201",
                "view_id": "view-level2",
                "pdf_page": 3,
                "x_min": 100,
                "y_min": 100,
                "x_max": 700,
                "y_max": 500,
                "coordinate_space": "pdf_points_top_left",
            },
        ],
        "parameters": [
            {
                "id": "param-shutter-width",
                "entity_kind": "element",
                "entity_id": "element-sd03",
                "name": "opening_width",
                "value_text": "4500 mm",
                "numeric_value": 4500,
                "unit": "mm",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-shutter",
            }
        ],
        "relationships": [
            {
                "id": "rel-dock-shutter",
                "source_kind": "element",
                "source_id": "element-dl03",
                "relation_type": "served_by",
                "target_kind": "element",
                "target_id": "element-sd03",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-shutter",
            },
            {
                "id": "rel-corridor-a",
                "source_kind": "space",
                "source_id": "space-corridor",
                "relation_type": "between",
                "target_kind": "space",
                "target_id": "space-hall-a",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-corridor",
            },
            {
                "id": "rel-corridor-b",
                "source_kind": "space",
                "source_id": "space-corridor",
                "relation_type": "between",
                "target_kind": "space",
                "target_id": "space-hall-b",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-corridor",
            },
            {
                "id": "rel-corridor-n",
                "source_kind": "space",
                "source_id": "space-corridor",
                "relation_type": "bounded_by",
                "target_kind": "element",
                "target_id": "wall-north",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-corridor",
            },
            {
                "id": "rel-corridor-s",
                "source_kind": "space",
                "source_id": "space-corridor",
                "relation_type": "bounded_by",
                "target_kind": "element",
                "target_id": "wall-south",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-corridor",
            },
        ],
        "annotations": [
            {
                "id": "ann-opening-width",
                "kind": "dimension",
                "semantic_type": "opening_width",
                "display_text": "4500",
                "numeric_value": 4500,
                "unit": "mm",
                "related_entity_kind": "element",
                "related_entity_id": "element-sd03",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-shutter",
            },
            {
                "id": "ann-roof-rfl",
                "kind": "spot_elevation",
                "semantic_type": "relative_finish_level",
                "display_text": "RFL + 1250 mm",
                "numeric_value": 1250,
                "unit": "mm",
                "related_entity_kind": "element",
                "related_entity_id": "element-roof",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-roof",
            },
        ],
        "geometries": [
            {
                "id": "geo-corridor",
                "entity_kind": "space",
                "entity_id": "space-corridor",
                "geometry_type": "footprint",
                "geometry": {
                    "points": [[0, 0, 0], [10000, 0, 0], [10000, 1800, 0], [0, 1800, 0]]
                },
                "coordinate_system": "project_local",
                "unit": "mm",
                "min_x": 0,
                "max_x": 10000,
                "min_y": 0,
                "max_y": 1800,
                "min_z": 0,
                "max_z": 0,
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-corridor",
            },
            {
                "id": "geo-wall-n",
                "entity_kind": "element",
                "entity_id": "wall-north",
                "geometry_type": "line",
                "geometry": {"start": [0, 1800, 0], "end": [10000, 1800, 0]},
                "coordinate_system": "project_local",
                "unit": "mm",
                "min_x": 0,
                "max_x": 10000,
                "min_y": 1800,
                "max_y": 1800,
                "min_z": 0,
                "max_z": 3000,
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-corridor",
            },
            {
                "id": "geo-wall-s",
                "entity_kind": "element",
                "entity_id": "wall-south",
                "geometry_type": "line",
                "geometry": {"start": [0, 0, 0], "end": [10000, 0, 0]},
                "coordinate_system": "project_local",
                "unit": "mm",
                "min_x": 0,
                "max_x": 10000,
                "min_y": 0,
                "max_y": 0,
                "min_z": 0,
                "max_z": 3000,
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-corridor",
            },
        ],
        "search_content": [
            {
                "record_kind": "element",
                "record_id": "element-sd03",
                "content": "Shutter SD-03 Industrial Roller Shutter opening width 4500 mm",
            },
            {
                "record_kind": "element",
                "record_id": "element-roof",
                "content": "Office Roof RFL + 1250 mm spot elevation",
            },
            {
                "record_kind": "space",
                "record_id": "space-corridor",
                "content": "Level 2 Data-hall Corridor データホール 廊下",
            },
            {
                "record_kind": "sheet",
                "record_id": "sheet-a312",
                "content": "A-312 Loading Dock Details Loading Dock Elevation",
            },
            {
                "record_kind": "sheet",
                "record_id": "sheet-a421",
                "content": "A-421 Roof Details Office Roof Section",
            },
        ],
    }
    records["source_models"] = [
        {
            "id": "model-host",
            "role": "host",
            "title": "Host Architectural Model",
            "revit_version": "2026",
            "model_identity_kind": "explicit",
            "model_identity": "synthetic-host",
            "snapshot_version_guid": "snapshot-host-1",
            "snapshot_save_number": 7,
        },
        {
            "id": "model-link",
            "role": "link",
            "title": "Linked Model MEP",
            "revit_version": "2026",
            "model_identity_kind": "explicit",
            "model_identity": "synthetic-link",
            "snapshot_version_guid": "snapshot-link-1",
            "snapshot_save_number": 3,
        },
    ]
    records["link_instances"] = [
        {
            "id": "link-instance-a",
            "host_source_model_id": "model-host",
            "linked_source_model_id": "model-link",
            "source_unique_id": "revit-link-instance-a",
            "name": "MEP Instance A",
            "transform_to_host": {
                "basis_x": [0, 1, 0],
                "basis_y": [-1, 0, 0],
                "basis_z": [0, 0, 1],
                "origin": [100, 200, 0],
                "source_unit": "revit_internal",
            },
            "provenance": provenance,
        },
        {
            "id": "link-instance-b",
            "host_source_model_id": "model-host",
            "linked_source_model_id": "model-link",
            "source_unique_id": "revit-link-instance-b",
            "name": "MEP Instance B",
            "transform_to_host": {
                "basis_x": [0, 1, 0],
                "basis_y": [-1, 0, 0],
                "basis_z": [0, 0, 1],
                "origin": [10100, 200, 0],
                "source_unit": "revit_internal",
            },
            "provenance": provenance,
        },
    ]
    for index, row in enumerate(records["sheets"], 1):
        row.update(
            export_order=index,
            source_model_id="model-host",
            source_unique_id=f"sheet-{index}",
        )
    for index, row in enumerate(records["views"], 1):
        row.update(
            export_order=index,
            source_model_id="model-host",
            source_unique_id=f"view-{index}",
        )
    viewport = records["viewports"][0]
    for old, new in (
        ("x_min", "pdf_x_min"),
        ("y_min", "pdf_y_min"),
        ("x_max", "pdf_x_max"),
        ("y_max", "pdf_y_max"),
        ("coordinate_space", "pdf_coordinate_space"),
    ):
        viewport[new] = viewport.pop(old)
    viewport.update(
        placement_kind="viewport",
        sheet_x_min=0.1,
        sheet_y_min=0.1,
        sheet_x_max=0.8,
        sheet_y_max=0.5,
        sheet_coordinate_unit="ft",
        sheet_to_pdf_transform=[72, 0, 0, -72, 0, 595],
        mapping_quality="calibrated",
    )
    for table in ("levels", "spaces", "elements"):
        for row in records[table]:
            row["source_model_id"] = "model-host"
            row["source_unique_id"] = row.pop("source_id")
    for row in records["spaces"]:
        row["phase_source_unique_id"] = "phase-new-construction"
    element_type = records["element_types"][0]
    element_type.update(
        family_name="Industrial Shutters",
        type_name=element_type.pop("name"),
        source_model_id="model-host",
        source_unique_id=element_type.pop("source_id"),
    )
    # Same UniqueId in a different owning model is deliberately valid.
    records["elements"].append(
        {
            "id": "element-linked-collision",
            "name": "Linked Equipment",
            "category": "Fixed Equipment",
            "type_id": None,
            "space_id": None,
            "level_id": None,
            "source_model_id": "model-link",
            "source_unique_id": "synthetic-sd03",
            "provenance": provenance,
            "confidence": None,
        }
    )
    parameter = records["parameters"][0]
    parameter["definition_name"] = parameter.pop("name")
    parameter.update(
        scope="instance",
        definition_key="builtin:DOOR_WIDTH",
        storage_type="Double",
        data_type_id="autodesk.spec.aec:length-2.0.0",
        parameter_type_id=None,
        shared_parameter_guid=None,
        unit_type_id="autodesk.unit.unit:millimeters-1.0.1",
        raw_value_text="14.7638 ft",
        raw_numeric_value=14.7637795276,
    )
    for row in records["relationships"]:
        row["phase_source_unique_id"] = (
            "phase-new-construction" if row["id"] == "rel-dock-shutter" else None
        )
    records["relationships"].extend(
        [
            {
                "id": "rel-shutter-from",
                "source_kind": "element",
                "source_id": "element-sd03",
                "relation_type": "from_space",
                "target_kind": "space",
                "target_id": "space-hall-a",
                "phase_source_unique_id": "phase-new-construction",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-shutter",
            },
            {
                "id": "rel-shutter-to",
                "source_kind": "element",
                "source_id": "element-sd03",
                "relation_type": "to_space",
                "target_kind": "space",
                "target_id": "space-hall-b",
                "phase_source_unique_id": "phase-new-construction",
                "provenance": provenance,
                "confidence": None,
                "evidence_id": "ev-shutter",
            },
        ]
    )
    for index, row in enumerate(records["annotations"]):
        row.update(
            source_model_id="model-host",
            source_unique_id=f"annotation-{index}",
            view_id="view-dock" if index == 0 else "view-roof",
        )
    records["annotation_segments"] = [
        {
            "id": "seg-width-0",
            "annotation_id": "ann-opening-width",
            "segment_index": 0,
            "numeric_value": 2100,
            "unit": "mm",
            "display_text": "2100",
            "value_override": None,
            "prefix": None,
            "suffix": None,
            "above": None,
            "below": None,
            "origin": [1, 2, 0],
            "text_position": [1, 2.1, 0],
        },
        {
            "id": "seg-width-1",
            "annotation_id": "ann-opening-width",
            "segment_index": 1,
            "numeric_value": 2400,
            "unit": "mm",
            "display_text": "2400",
            "value_override": None,
            "prefix": None,
            "suffix": None,
            "above": None,
            "below": None,
            "origin": [3, 2, 0],
            "text_position": [3, 2.1, 0],
        },
    ]
    records["annotation_references"] = [
        {
            "id": "ref-width-0",
            "annotation_id": "ann-opening-width",
            "reference_index": 0,
            "target_source_model_id": "model-host",
            "target_source_unique_id": "synthetic-dl03",
            "target_link_instance_id": None,
            "stable_reference": "stable:host:dl03",
            "reference_type": "surface",
            "is_linked": 0,
            "resolution_state": "resolved",
        },
        {
            "id": "ref-width-1",
            "annotation_id": "ann-opening-width",
            "reference_index": 1,
            "target_source_model_id": "model-link",
            "target_source_unique_id": "synthetic-sd03",
            "target_link_instance_id": "link-instance-a",
            "stable_reference": "stable:link:sd03",
            "reference_type": "surface",
            "is_linked": 1,
            "resolution_state": "resolved",
        },
        {
            "id": "ref-roof-0",
            "annotation_id": "ann-roof-rfl",
            "reference_index": 0,
            "target_source_model_id": "model-host",
            "target_source_unique_id": "synthetic-office-roof",
            "target_link_instance_id": None,
            "stable_reference": "stable:roof",
            "reference_type": "face",
            "is_linked": 0,
            "resolution_state": "resolved",
        },
    ]
    records["entity_appearances"] = [
        {
            "id": f"appearance-sd03-{i}",
            "entity_kind": "element",
            "entity_id": "element-sd03",
            "sheet_id": sheet,
            "view_id": view,
            "viewport_id": "vp-dock" if i == 0 else None,
            "link_instance_id": None,
            "pdf_page": page,
            "x_min": 120 + i * 10,
            "y_min": 150,
            "x_max": 330 + i * 10,
            "y_max": 240,
            "coordinate_space": "pdf_points_top_left",
            "appearance_kind": kind,
            "bbox_quality": quality,
            "provenance": provenance,
        }
        for i, (sheet, view, page, kind, quality) in enumerate(
            (
                ("sheet-a312", "view-dock", 1, "model", "projected_bbox"),
                ("sheet-a421", "view-roof", 2, "detail", "view_bbox"),
                ("sheet-a201", "view-level2", 3, "schedule", "page_only"),
            )
        )
    ]
    # Page-only occurrences have no bbox.
    records["entity_appearances"][2].update(
        x_min=None, y_min=None, x_max=None, y_max=None
    )
    for suffix, instance_id, offset in (
        ("a", "link-instance-a", 0),
        ("b", "link-instance-b", 300),
    ):
        records["entity_appearances"].append(
            {
                "id": f"appearance-linked-{suffix}",
                "entity_kind": "element",
                "entity_id": "element-linked-collision",
                "sheet_id": "sheet-a201",
                "view_id": "view-level2",
                "viewport_id": None,
                "link_instance_id": instance_id,
                "pdf_page": 3,
                "x_min": 100 + offset,
                "y_min": 200,
                "x_max": 150 + offset,
                "y_max": 250,
                "coordinate_space": "pdf_points_top_left",
                "appearance_kind": "model",
                "bbox_quality": "projected_bbox",
                "provenance": provenance,
            }
        )
    points = [(0, 0, 0), (10000, 0, 0), (10000, 1800, 0), (0, 1800, 0)]
    records["spatial_boundaries"] = [
        {
            "id": "boundary-corridor-outer",
            "space_id": "space-corridor",
            "loop_index": 0,
            "loop_kind": "outer",
            "coordinate_system": "host_revit_internal_origin",
            "unit": "mm",
            "provenance": provenance,
        }
    ]
    records["spatial_boundary_segments"] = [
        {
            "id": f"boundary-segment-{i}",
            "boundary_id": "boundary-corridor-outer",
            "segment_index": i,
            "start_x": a[0],
            "start_y": a[1],
            "start_z": a[2],
            "end_x": b[0],
            "end_y": b[1],
            "end_z": b[2],
            "source_model_id": "model-host",
            "source_unique_id": f"boundary-wall-{i}",
        }
        for i, (a, b) in enumerate(zip(points, points[1:] + points[:1]))
    ]
    for row in records["geometries"]:
        row["coordinate_system"] = "host_revit_internal_origin"
        row["link_instance_id"] = None
    for suffix, instance_id, x in (
        ("a", "link-instance-a", 100),
        ("b", "link-instance-b", 10100),
    ):
        records["geometries"].append(
            {
                "id": f"geo-linked-{suffix}",
                "entity_kind": "element",
                "entity_id": "element-linked-collision",
                "link_instance_id": instance_id,
                "geometry_type": "bbox3d",
                "geometry": {"min": [x, 200, 0], "max": [x + 500, 700, 1000]},
                "coordinate_system": "host_revit_internal_origin",
                "unit": "mm",
                "min_x": x,
                "max_x": x + 500,
                "min_y": 200,
                "max_y": 700,
                "min_z": 0,
                "max_z": 1000,
                "provenance": provenance,
                "confidence": None,
                "evidence_id": None,
            }
        )
    return records


def build_synthetic_fixture(directory: Path) -> tuple[Path, Path]:
    drawing = create_synthetic_pdf(Path(directory) / "drawing.pdf")
    database = build_database(
        synthetic_records(drawing), Path(directory) / "project.sqlite"
    )
    return drawing, database


def write_synthetic_revit_snapshot(source_pdf: Path, output: Path) -> Path:
    """Write export-time JSON shaped like the future Revit 2026 adapter output."""
    records = synthetic_records(source_pdf)
    project_keys = (
        "project_id",
        "created_from",
        "source_document_identity",
        "source_document_sha256",
    )
    snapshot = {
        "snapshot_version": 1,
        "coordinate_system": "host_revit_internal_origin",
        "unit": "mm",
        "project": {key: records.pop(key) for key in project_keys},
        "records": records,
    }
    output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
