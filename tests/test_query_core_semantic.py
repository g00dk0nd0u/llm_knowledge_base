from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.query_core.build import build_database
from tools.query_core.query import QueryCore, validate_database


@pytest.fixture
def semantic_contract_records() -> dict:
    """Focused Door contract: source facts stay in parameters/relationships."""
    return {
        "project_id": "semantic-contract",
        "created_from": "semantic contract fixture",
        "binding_mode": "project",
        "source_models": [{
            "id": "model-host", "role": "host", "title": "Contract Model",
            "revit_version": "2026", "model_identity_kind": "path",
            "model_identity": "contract.rvt",
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
        "relationships": [{
            "id": "relationship-instance-of", "source_kind": "element",
            "source_id": "door-101", "relation_type": "instance_of",
            "target_kind": "element_type", "target_id": "type-door-a",
            "provenance": "revit_snapshot",
        }],
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
        ],
    }


def test_semantic_projection_round_trip_without_duplicate_facts(
    tmp_path: Path, semantic_contract_records: dict
) -> None:
    database = build_database(semantic_contract_records, tmp_path / "semantic.sqlite")
    with QueryCore(database) as query:
        assert query.has_semantic_capability()
        view = query.get_semantic_entity("semantic-door-101")
        assert view is not None
        assert view["capability"] == {"semantic_projection": True, "schema_version": 2}
        assert [(p["source_name"], p["source_value"], p["scope"]) for p in view["properties"]] == [
            ("Mark", "D-101", "instance"),
            ("電気錠", "EL560", "instance"),
            ("Fire Rating", "60 min", "type"),
        ]
        assert [row["id"] for row in view["relationships"]] == [
            "relationship-instance-of"
        ]
        ambiguous = query.get_semantic_entity("semantic-door-102")
        assert ambiguous is not None
        assert ambiguous["entity"]["resolution_state"] == "ambiguous"
        assert ambiguous["bindings"][0]["resolution_state"] == "ambiguous"
        assert ambiguous["properties"] == []
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM parameters").fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM relationships").fetchone()[0] == 1
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='semantic_properties'"
        ).fetchone()
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
        connection.execute("DROP TABLE semantic_bindings")
        connection.execute("DROP TABLE semantic_entities")
    assert validate_database(legacy)["schema_version"] == "2"
    with QueryCore(legacy) as query:
        assert not query.has_semantic_capability()
        assert query.get_semantic_entity("semantic-door-101") is None


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
