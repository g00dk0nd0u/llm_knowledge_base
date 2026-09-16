from __future__ import annotations

import hashlib
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
    return {
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


def build_synthetic_fixture(directory: Path) -> tuple[Path, Path]:
    drawing = create_synthetic_pdf(Path(directory) / "drawing.pdf")
    database = build_database(
        synthetic_records(drawing), Path(directory) / "project.sqlite"
    )
    return drawing, database
