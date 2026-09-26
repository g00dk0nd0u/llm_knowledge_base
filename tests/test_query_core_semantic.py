from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.query_core.build import build_database
from tools.query_core.errors import QueryCoreError
from tools.query_core.query import QueryCore, validate_database


@pytest.fixture
def semantic_contract_records() -> dict:
    """Focused Door contract: source facts stay in parameters/relationships."""
    return {
        "project_id": "semantic-contract",
        "created_from": "semantic contract fixture",
        "binding_mode": "project",
        "documents": [{
            "id": "document-door-schedule", "identity": "source/door-schedule.pdf",
            "title": "Door Schedule", "source_filename": "door-schedule.pdf",
            "source_sha256": "a" * 64,
        }],
        "evidence": [
            {
                "id": "evidence-door-101-schedule",
                "document_id": "document-door-schedule", "pdf_page": 1,
                "x_min": 10.0, "y_min": 20.0, "x_max": 200.0, "y_max": 80.0,
                "coordinate_space": "pdf_points_top_left",
            },
            {
                "id": "evidence-door-102-schedule",
                "document_id": "document-door-schedule", "pdf_page": 1,
                "x_min": 10.0, "y_min": 80.0, "x_max": 200.0, "y_max": 120.0,
                "coordinate_space": "pdf_points_top_left",
            },
        ],
        "source_models": [{
            "id": "model-host", "role": "host", "title": "Contract Model",
            "revit_version": "2026", "model_identity_kind": "path",
            "model_identity": "contract.rvt",
        }],
        "levels": [{
            "id": "level-01", "name": "Level 01", "elevation": 0.0,
            "unit": "mm", "source_model_id": "model-host",
            "source_unique_id": "level-01", "provenance": "revit_snapshot",
            "confidence": 1.0,
        }],
        "spaces": [{
            "id": "space-101", "kind": "Room", "name": "Office 101",
            "number": "101", "level_id": "level-01",
            "source_model_id": "model-host", "source_unique_id": "space-101",
            "phase_source_unique_id": None, "provenance": "revit_snapshot",
            "confidence": 1.0,
        }],
        "element_types": [{
            "id": "type-door-a", "family_name": "Single Door",
            "type_name": "D-A", "category": "Door",
            "source_model_id": "model-host", "source_unique_id": "type-a",
            "provenance": "revit_snapshot", "confidence": 1.0,
        }],
        "elements": [
            {
                "id": "door-101", "name": "Door 101", "category": "Door",
                "type_id": "type-door-a", "space_id": None, "level_id": None,
                "source_model_id": "model-host", "source_unique_id": "door-101",
                "provenance": "revit_snapshot", "confidence": 1.0,
            },
            {
                "id": "door-102", "name": "Door 102", "category": "Door",
                "type_id": "type-door-a", "space_id": None, "level_id": None,
                "source_model_id": "model-host", "source_unique_id": "door-102",
                "provenance": "revit_snapshot", "confidence": 1.0,
            },
        ],
        "parameters": [
            {
                "id": "parameter-door-101-mark", "entity_kind": "element",
                "entity_id": "door-101", "scope": "instance",
                "definition_name": "Mark", "raw_value_text": "D-101",
                "value_text": "D-101", "provenance": "revit_snapshot",
            },
            {
                "id": "parameter-door-101-lock", "entity_kind": "element",
                "entity_id": "door-101", "scope": "instance",
                "definition_name": "電気錠", "raw_value_text": "EL560",
                "value_text": "EL560", "provenance": "revit_snapshot",
            },
            {
                "id": "parameter-door-102-mark", "entity_kind": "element",
                "entity_id": "door-102", "scope": "instance",
                "definition_name": "Mark", "raw_value_text": "D-102",
                "value_text": "D-102", "provenance": "revit_snapshot",
            },
            {
                "id": "parameter-type-fire", "entity_kind": "element_type",
                "entity_id": "type-door-a", "scope": "type",
                "definition_name": "Fire Rating", "raw_value_text": "60 min",
                "value_text": "60 min", "provenance": "revit_snapshot",
            },
        ],
        "relationships": [
            {
                "id": "relationship-instance-of", "source_kind": "element",
                "source_id": "door-101", "relation_type": "instance_of",
                "target_kind": "element_type", "target_id": "type-door-a",
                "provenance": "revit_snapshot",
            },
            {
                "id": "relationship-space-level", "source_kind": "space",
                "source_id": "space-101", "relation_type": "belongs_to_level",
                "target_kind": "level", "target_id": "level-01",
                "provenance": "revit_snapshot",
            },
            {
                "id": "relationship-door-space", "source_kind": "element",
                "source_id": "door-101", "relation_type": "to_space",
                "target_kind": "space", "target_id": "space-101",
                "provenance": "revit_snapshot",
            },
        ],
        "semantic_entities": [
            {
                "id": "semantic-door-101", "entity_class": "Door",
                "label": "Door D-101", "number": "D-101",
                "instance_or_type": "instance", "resolution_state": "exact",
                "provenance": "semantic_contract_fixture",
            },
            {
                "id": "semantic-door-102", "entity_class": "Door",
                "label": None, "number": "D-102", "instance_or_type": "instance",
                "resolution_state": "ambiguous",
                "provenance": "semantic_contract_fixture",
            },
            {
                "id": "semantic-space-101", "entity_class": "Space",
                "label": "Office 101", "number": "101",
                "instance_or_type": "instance", "resolution_state": "exact",
                "provenance": "semantic_contract_fixture",
            },
        ],
        "semantic_bindings": [
            {
                "id": "binding-door-101", "semantic_entity_id": "semantic-door-101",
                "source_kind": "element", "source_id": "door-101",
                "resolution_state": "exact", "provenance": "stable_source_identity",
            },
            {
                "id": "binding-door-type-a", "semantic_entity_id": "semantic-door-101",
                "source_kind": "element_type", "source_id": "type-door-a",
                "resolution_state": "resolved_deterministically",
                "provenance": "stored_type_id",
            },
            {
                "id": "binding-door-102-mention",
                "semantic_entity_id": "semantic-door-102",
                "source_kind": "pdf_table_cell", "source_id": None,
                "resolution_state": "ambiguous", "provenance": "fixture_only",
            },
            {
                "id": "binding-space-101",
                "semantic_entity_id": "semantic-space-101",
                "source_kind": "space", "source_id": "space-101",
                "resolution_state": "exact", "provenance": "stable_source_identity",
            },
            {
                "id": "binding-door-101-schedule",
                "semantic_entity_id": "semantic-door-101",
                "source_kind": "evidence", "source_id": "evidence-door-101-schedule",
                "resolution_state": "exact", "provenance": "fixture_schedule_row",
                "evidence_id": "evidence-door-101-schedule",
            },
        ],
        "semantic_properties": [
            {
                "id": "property-door-101-lock", "semantic_entity_id": "semantic-door-101",
                "source_name": "電気錠", "source_value": "EL160", "scope": "schedule",
                "canonical_name": None, "provenance": "door_schedule",
                "source_binding_id": "binding-door-101-schedule",
                "evidence_id": "evidence-door-101-schedule",
            },
            {
                "id": "property-door-101-fire", "semantic_entity_id": "semantic-door-101",
                "source_name": "防火", "source_value": "60分", "scope": "schedule",
                "provenance": "door_schedule",
                "source_binding_id": "binding-door-101-schedule",
            },
            {
                "id": "property-door-101-glass", "semantic_entity_id": "semantic-door-101",
                "source_name": "ガラス厚", "source_value": "8mm", "scope": "schedule",
                "provenance": "door_schedule",
                "source_binding_id": "binding-door-101-schedule",
            },
            {
                "id": "property-door-101-threshold", "semantic_entity_id": "semantic-door-101",
                "source_name": "靴ずり", "source_value": "SUS HL 1.5", "scope": "schedule",
                "provenance": "door_schedule",
                "source_binding_id": "binding-door-101-schedule",
            },
            {
                "id": "property-door-102-finish", "semantic_entity_id": "semantic-door-102",
                "source_name": "仕上", "source_value": "SOP", "scope": "schedule",
                "provenance": "door_schedule", "evidence_id": "evidence-door-102-schedule",
            },
        ],
    }


def test_semantic_projection_round_trip_without_duplicate_facts(
    tmp_path: Path, semantic_contract_records: dict
) -> None:
    database = build_database(semantic_contract_records, tmp_path / "semantic.sqlite")
    with QueryCore(database) as query:
        assert query.has_semantic_capability()
        assert query.has_semantic_property_capability()
        view = query.get_semantic_entity("semantic-door-101")
        assert view is not None
        assert view["capability"] == {"semantic_projection": True, "schema_version": 2}
        assert [(p["source_name"], p["source_value"], p["scope"]) for p in view["properties"][:3]] == [
            ("Mark", "D-101", "instance"),
            ("電気錠", "EL560", "instance"),
            ("Fire Rating", "60 min", "type"),
        ]
        persisted = [p for p in view["properties"] if p["fact_kind"] == "semantic_property"]
        assert {(p["source_name"], p["source_value"]) for p in persisted} == {
            ("電気錠", "EL160"), ("防火", "60分"), ("ガラス厚", "8mm"),
            ("靴ずり", "SUS HL 1.5"),
        }
        assert all(p["canonical_name"] is None for p in persisted)
        assert {p["source_value"] for p in view["properties"] if p["source_name"] == "電気錠"} == {
            "EL560", "EL160",
        }
        assert [row["id"] for row in view["relationships"]] == [
            "relationship-door-space", "relationship-instance-of"
        ]
        ambiguous = query.get_semantic_entity("semantic-door-102")
        assert ambiguous is not None
        assert ambiguous["entity"]["resolution_state"] == "ambiguous"
        assert ambiguous["bindings"][0]["resolution_state"] == "ambiguous"
        assert [(p["source_name"], p["source_value"]) for p in ambiguous["properties"]] == [
            ("仕上", "SOP")
        ]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM parameters").fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM relationships").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM semantic_properties").fetchone()[0] == 5
        assert connection.execute(
            "SELECT count(*) FROM semantic_properties WHERE provenance='revit_snapshot'"
        ).fetchone()[0] == 0


def test_space_binding_projects_source_and_target_relationships_without_parameters(
    tmp_path: Path, semantic_contract_records: dict
) -> None:
    database = build_database(semantic_contract_records, tmp_path / "space.sqlite")
    with QueryCore(database) as query:
        view = query.get_semantic_entity("semantic-space-101")
        assert view is not None
        assert view["properties"] == []
        assert [row["id"] for row in view["relationships"]] == [
            "relationship-door-space", "relationship-space-level"
        ]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM parameters WHERE entity_kind='space'"
        ).fetchone()[0] == 0
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='semantic_relationships'"
        ).fetchone()


def test_semantics_are_source_neutral_and_optional(
    tmp_path: Path, semantic_contract_records: dict
) -> None:
    generic = {
        "project_id": "generic-semantic", "created_from": "fixture",
        "binding_mode": "project",
        "semantic_entities": [{
            "id": "concept-1", "entity_class": "project_note", "label": "Note",
            "number": None, "instance_or_type": "unknown",
            "resolution_state": "unresolved", "provenance": "source_fact",
        }],
        "semantic_bindings": [{
            "id": "binding-1", "semantic_entity_id": "concept-1",
            "source_kind": "pdf_text_span", "source_id": None,
            "resolution_state": "unresolved", "provenance": "source_fact",
        }],
    }
    build_database(generic, tmp_path / "generic.sqlite")

    current = build_database(semantic_contract_records, tmp_path / "current.sqlite")
    legacy = tmp_path / "legacy.sqlite"
    legacy.write_bytes(current.read_bytes())
    with sqlite3.connect(legacy) as connection:
        connection.execute("DROP TABLE semantic_properties")
        connection.execute("DROP TABLE semantic_bindings")
        connection.execute("DROP TABLE semantic_entities")
    assert validate_database(legacy)["schema_version"] == "2"
    with QueryCore(legacy) as query:
        assert not query.has_semantic_capability()
        assert not query.has_semantic_property_capability()
        assert query.get_semantic_entity("semantic-door-101") is None

    phase_1a = tmp_path / "phase-1a.sqlite"
    phase_1a.write_bytes(current.read_bytes())
    with sqlite3.connect(phase_1a) as connection:
        connection.execute("DROP TABLE semantic_properties")
    assert validate_database(phase_1a)["schema_version"] == "2"
    with QueryCore(phase_1a) as query:
        assert query.has_semantic_capability()
        assert not query.has_semantic_property_capability()
        assert [p["source_value"] for p in query.get_semantic_entity(
            "semantic-door-101"
        )["properties"]] == ["D-101", "EL560", "60 min"]


@pytest.mark.parametrize(
    "source_kind", [
        "element", "element_type", "space", "level", "evidence",
        "pdf_table_cell", "pdf_text_span",
    ],
)
def test_resolved_known_binding_rejects_missing_source(
    tmp_path: Path, source_kind: str
) -> None:
    records = {
        "project_id": "bad-binding", "created_from": "fixture",
        "binding_mode": "project",
        "semantic_entities": [{
            "id": "entity", "entity_class": "Door", "instance_or_type": "instance",
            "resolution_state": "exact", "provenance": "fixture",
        }],
        "semantic_bindings": [{
            "id": "binding", "semantic_entity_id": "entity",
            "source_kind": source_kind, "source_id": "missing",
            "resolution_state": "exact", "provenance": "fixture",
        }],
    }
    with pytest.raises(QueryCoreError, match=f"nonexistent {source_kind} record"):
        build_database(records, tmp_path / "bad.sqlite")


def test_unknown_resolved_binding_kind_remains_extensible(tmp_path: Path) -> None:
    records = {
        "project_id": "future-binding", "created_from": "fixture",
        "binding_mode": "project",
        "semantic_entities": [{
            "id": "entity", "entity_class": "future", "instance_or_type": "unknown",
            "resolution_state": "exact", "provenance": "fixture",
        }],
        "semantic_bindings": [{
            "id": "binding", "semantic_entity_id": "entity",
            "source_kind": "future_adapter_record", "source_id": "future-1",
            "resolution_state": "exact", "provenance": "fixture",
        }],
    }
    build_database(records, tmp_path / "future.sqlite")


def test_semantic_projection_is_stdlib_only(
    tmp_path: Path, semantic_contract_records: dict
) -> None:
    database = build_database(semantic_contract_records, tmp_path / "semantic.sqlite")
    code = (
        "import json,sys; from tools.query_core import QueryCore; "
        f"q=QueryCore({str(database)!r}); "
        "v=q.get_semantic_entity('semantic-door-101'); q.close(); "
        "assert 'fitz' not in sys.modules and 'jsonschema' not in sys.modules; "
        "print(json.dumps(v,ensure_ascii=False,sort_keys=True))"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code], capture_output=True, text=True,
        encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["properties"][1]["source_name"] == "電気錠"
