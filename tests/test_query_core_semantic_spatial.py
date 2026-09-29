from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tools.query_core.fixtures import build_synthetic_fixture
from tools.query_core.query import QueryCore


@pytest.fixture
def semantic_spatial_database(tmp_path: Path) -> Path:
    _, database = build_synthetic_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE elements SET space_id='space-corridor' WHERE id='wall-north'"
        )
        connection.execute(
            "INSERT INTO spatial_boundaries VALUES "
            "('boundary-hall-a','space-hall-a',NULL,0,'outer',"
            "'host_revit_internal_origin','mm','test')"
        )
        connection.execute(
            "INSERT INTO spatial_boundary_segments VALUES "
            "('segment-hall-a','boundary-hall-a',NULL,0,2000,0,0,8000,0,0,"
            "'model-host','boundary-wall-0',NULL,'line')"
        )
        entities = [
            ("sem-space", "Space", "exact"),
            ("sem-space-second", "Space", "exact"),
            ("sem-adjacent", "Space", "exact"),
            ("sem-element", "Door", "exact"),
            ("sem-level", "LevelSource", "exact"),
            ("sem-unresolved", "Space", "unresolved"),
        ]
        connection.executemany(
            "INSERT INTO semantic_entities "
            "(id,entity_class,instance_or_type,resolution_state,provenance) "
            "VALUES (?,?,'instance',?,'test')",
            entities,
        )
        bindings = [
            ("binding-space-z", "sem-space", "space", "space-corridor", "exact"),
            ("binding-space-a", "sem-space", "space", "space-hall-b", "exact"),
            ("binding-space-m", "sem-space", "space", "space-hall-a", "exact"),
            (
                "binding-space-second", "sem-space-second", "space",
                "space-corridor", "exact",
            ),
            ("binding-adjacent", "sem-adjacent", "space", "space-hall-a", "exact"),
            (
                "binding-element", "sem-element", "element", "element-sd03",
                "resolved_deterministically",
            ),
            ("binding-level", "sem-level", "level", "level-2", "exact"),
            ("binding-unresolved", "sem-unresolved", "space", None, "unresolved"),
        ]
        connection.executemany(
            "INSERT INTO semantic_bindings "
            "(id,semantic_entity_id,source_kind,source_id,resolution_state,provenance) "
            "VALUES (?,?,?,?,?,'test')",
            bindings,
        )
    return database


def test_semantic_space_projects_existing_topology_without_persistence(
    semantic_spatial_database: Path,
) -> None:
    with QueryCore(semantic_spatial_database) as query:
        result = query.get_semantic_spatial_context("sem-space")
        hall_a = result["spatial_contexts"][1]["spatial_context"]
        corridor = result["spatial_contexts"][2]["spatial_context"]
        assert [row["id"] for row in corridor["contained_elements"]] == ["wall-north"]
        assert hall_a["connections"][0]["connected_space"]["id"] == "space-hall-b"
        adjacency = corridor["adjacency"]
        assert adjacency["status"] == "ok"
        assert adjacency["coverage"]["candidate_segment_pairs_evaluated"] == 1
        assert adjacency["warnings"] == []
        assert adjacency["adjacent_spaces"][0]["basis"] == (
            "shared_boundary_source_longitudinal_overlap"
        )
        assert adjacency["evidence_class"] == "deterministic_derived"
        assert hall_a["connections"][0]["evidence_class"] == "source_fact"
        assert adjacency["adjacent_spaces"][0]["space"]["reference"][
            "semantic_entities"
        ][0]["id"] == "sem-adjacent"
        assert corridor["contained_elements"][0]["reference"][
            "semantic_entities"
        ] == []
        count = query.connection.execute(
            "SELECT count(*) FROM semantic_relationships"
        ).fetchone()[0]
        assert count == 0


def test_semantic_element_projects_existing_spatial_context(
    semantic_spatial_database: Path,
) -> None:
    with QueryCore(semantic_spatial_database) as query:
        context = query.get_semantic_spatial_context("sem-element")[
            "spatial_contexts"
        ][0]["spatial_context"]
    assert context["status"] == "ok"
    assert [space["id"] for space in context["containment"]] == []
    assert context["level"]["id"] == "level-2"
    assert context["type_siblings"]["evidence_class"] == "deterministic_derived"


def test_only_resolved_supported_bindings_project_in_stable_order(
    semantic_spatial_database: Path,
) -> None:
    with QueryCore(semantic_spatial_database) as query:
        result = query.get_semantic_spatial_context("sem-space")
        unresolved = query.get_semantic_spatial_context("sem-unresolved")
    assert [row["binding"]["id"] for row in result["spatial_contexts"]] == [
        "binding-space-a",
        "binding-space-m",
        "binding-space-z",
    ]
    assert [row["source_reference"]["id"] for row in result["spatial_contexts"]] == [
        "space-hall-b",
        "space-hall-a",
        "space-corridor",
    ]
    assert unresolved["status"] == "insufficient_data"
    assert unresolved["spatial_contexts"] == []


def test_level_binding_is_source_context_not_storey_conversion(
    semantic_spatial_database: Path,
) -> None:
    with QueryCore(semantic_spatial_database) as query:
        result = query.get_semantic_spatial_context("sem-level")
    assert result["semantic_entity"]["entity_class"] == "LevelSource"
    context = result["spatial_contexts"][0]["spatial_context"]
    assert context["level"]["id"] == "level-2"
    assert "Storey" not in str(result)


def test_pre_semantic_database_has_no_semantic_spatial_projection(
    semantic_spatial_database: Path, tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy.sqlite"
    legacy.write_bytes(semantic_spatial_database.read_bytes())
    with sqlite3.connect(legacy) as connection:
        connection.execute("DROP TABLE semantic_relationships")
        connection.execute("DROP TABLE semantic_properties")
        connection.execute("DROP TABLE semantic_bindings")
        connection.execute("DROP TABLE semantic_entities")
    with QueryCore(legacy) as query:
        assert query.get_semantic_spatial_context("sem-space") is None
