from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.query_core.errors import QueryCoreError
from tools.query_core.fixtures import build_synthetic_fixture
from tools.query_core.query import QueryCore


def _database(tmp_path: Path) -> Path:
    _, database = build_synthetic_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO semantic_entities "
            "(id,entity_class,label,number,instance_or_type,resolution_state,"
            "provenance,evidence_id) VALUES (?,?,?,?,?,?,?,?)",
            [
                ("door-a", "Door", "Main Door D-101", "D-101", "instance",
                 "exact", "test", "ev-shutter"),
                ("door-b", "Door", "Rear Door", "D-101", "instance",
                 "ambiguous", "test", None),
                ("room-305", "Room", "Room 305", "305", "instance",
                 "unresolved", "test", None),
                ("equipment-a", "Equipment", "Pump", "P-1", "instance",
                 "resolved_deterministically", "test", None),
            ],
        )
        connection.executemany(
            "INSERT INTO semantic_bindings "
            "(id,semantic_entity_id,source_kind,source_id,resolution_state,provenance) "
            "VALUES (?,?,?,NULL,'unresolved','test')",
            [(f"binding-{entity}", entity, "element") for entity in
             ("door-a", "door-b", "room-305", "equipment-a")],
        )
        connection.executemany(
            "INSERT INTO semantic_properties "
            "(id,semantic_entity_id,source_name,source_value,value_type,scope,"
            "provenance,source_binding_id,evidence_id) "
            "VALUES (?,?,?,?,?,'instance','test',?,?)",
            [
                ("prop-a", "door-a", "Head Height", "EL560", "text",
                 "binding-door-a", "ev-shutter"),
                ("prop-b", "door-b", "Sill Elevation", "EL560", "text",
                 "binding-door-b", None),
                ("prop-c", "equipment-a", "Finish", "Stone-03", "text",
                 "binding-equipment-a", None),
            ],
        )
    return database


def test_unique_number_and_duplicate_number_are_factual(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with QueryCore(database) as core:
        unique = core.search_semantic_entities("305")
        duplicate = core.search_semantic_entities("D-101")
        limited = core.search_semantic_entities("D-101", limit=1)
    assert unique["status"] == "exact_unique"
    assert unique["candidates"][0]["matches"][0]["match_kind"] == "number_exact"
    assert duplicate["status"] == "multiple_exact"
    assert [candidate["semantic_entity"]["id"] for candidate in
            duplicate["candidates"]] == ["door-a", "door-b"]
    assert duplicate["candidates"][1]["semantic_entity"]["resolution_state"] == "ambiguous"
    assert limited["status"] == "multiple_exact"
    assert len(limited["candidates"]) == 1


def test_property_matches_preserve_sparse_fact_and_ambiguity(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as core:
        result = core.search_semantic_entities("EL560")
    assert result["status"] == "multiple_exact"
    assert [candidate["semantic_entity"]["id"] for candidate in
            result["candidates"]] == ["door-a", "door-b"]
    first = result["candidates"][0]["matches"][0]
    assert first == {
        "match_kind": "property_value_exact",
        "property_id": "prop-a",
        "source_name": "Head Height",
        "source_value": "EL560",
        "scope": "instance",
        "evidence_refs": ["ev-shutter"],
    }


def test_contains_class_filter_resolution_state_and_determinism(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as core:
        first = core.search_semantic_entities("Room", entity_class="room")
        second = core.search_semantic_entities("Room", entity_class="room")
    assert first == second
    assert first["status"] == "candidates"
    candidate = first["candidates"][0]
    assert candidate["matches"][0]["match_kind"] == "label_contains"
    assert candidate["semantic_entity"]["resolution_state"] == "unresolved"


def test_missing_properties_keeps_identity_lookup_and_no_match(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE semantic_properties")
    with QueryCore(database) as core:
        statements: list[str] = []
        core.connection.set_trace_callback(statements.append)
        found = core.search_semantic_entities("D-101", entity_class="Door")
        missing = core.search_semantic_entities("EL560")
    assert found["status"] == "multiple_exact"
    assert found["capability"]["semantic_properties"] is False
    assert missing["status"] == "no_match"
    assert missing["candidates"] == []
    assert not any("FROM semantic_properties" in sql for sql in statements)


def test_match_precedence(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE semantic_entities SET number='door-a',label='door-a' "
            "WHERE id='door-a'"
        )
        connection.execute(
            "UPDATE semantic_properties SET source_value='door-a' WHERE id='prop-a'"
        )
        connection.execute(
            "UPDATE semantic_entities SET number='prefix door-a',label='prefix door-a' "
            "WHERE id='door-b'"
        )
        connection.execute(
            "UPDATE semantic_properties SET source_value='prefix door-a' WHERE id='prop-b'"
        )
    with QueryCore(database) as core:
        result = core.search_semantic_entities("DOOR-A")
    assert result["status"] == "exact_unique"
    assert [match["match_kind"] for candidate in result["candidates"]
            for match in candidate["matches"]] == [
        "entity_id_exact", "number_exact", "label_exact", "property_value_exact",
        "number_contains", "label_contains", "property_value_contains",
    ]


@pytest.mark.parametrize("query", ["é", "pré"])
def test_matching_keeps_sqlite_ascii_case_semantics(tmp_path: Path, query: str) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE semantic_entities SET number='É',label='PRÉ' WHERE id='door-a'"
        )
        connection.execute(
            "UPDATE semantic_properties SET source_value='É' WHERE id='prop-a'"
        )
    with QueryCore(database) as core:
        result = core.search_semantic_entities(query)
        exact = core.search_semantic_entities("É")
    assert result["status"] == "no_match"
    assert result["candidates"] == []
    assert exact["status"] == "exact_unique"
    assert exact["candidates"][0]["matches"][0]["source_value"] == "É"


def test_limit_validation(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as core:
        with pytest.raises(QueryCoreError, match="positive integer"):
            core.search_semantic_entities("D-101", limit=0)


@pytest.mark.parametrize("query", ["", " ", "   ", "\t"])
def test_blank_query_returns_no_match(tmp_path: Path, query: str) -> None:
    with QueryCore(_database(tmp_path)) as core:
        result = core.search_semantic_entities(query)
    assert result["status"] == "no_match"
    assert result["candidates"] == []
    assert result["query"] == query
    assert result["capability"]["semantic_projection"] is True


def test_nonblank_query_preserves_literal_whitespace(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as core:
        result = core.search_semantic_entities(" D-101 ")
    assert result["query"] == " D-101 "
    assert result["status"] == "no_match"
    assert result["candidates"] == []


def test_semantic_search_sql_statement_count_is_bounded(tmp_path: Path) -> None:
    database = _database(tmp_path)
    counts = []
    for start, stop in [(0, 10), (10, 500)]:
        with sqlite3.connect(database) as connection:
            connection.executemany(
                "INSERT INTO semantic_entities "
                "(id,entity_class,label,number,instance_or_type,resolution_state,"
                "provenance) VALUES (?,'Door','Bulk bulk-match','bulk-match',"
                "'instance','ambiguous','test')",
                [(f"bulk-{index:04d}",) for index in range(start, stop)],
            )
            connection.executemany(
                "INSERT INTO semantic_properties "
                "(id,semantic_entity_id,source_name,source_value,scope,provenance,"
                "evidence_id) VALUES (?,?,'Bulk Property','bulk-match','instance',"
                "'test','ev-shutter')",
                [(f"bulk-prop-{index:04d}-{prop}", f"bulk-{index:04d}")
                 for index in range(start, stop) for prop in range(3)],
            )
        with QueryCore(database) as core:
            statements: list[str] = []
            core.connection.set_trace_callback(statements.append)
            result = core.search_semantic_entities(
                "bulk-match", entity_class="dOoR", limit=5
            )
            core.connection.set_trace_callback(None)
        counts.append(len(statements))
        assert result["status"] == "multiple_exact"
        assert [candidate["semantic_entity"]["id"] for candidate in
                result["candidates"]] == [f"bulk-{index:04d}" for index in range(5)]
        candidate = result["candidates"][0]
        assert candidate["semantic_entity"]["resolution_state"] == "ambiguous"
        assert [match["match_kind"] for match in candidate["matches"]] == [
            "number_exact", *(["property_value_exact"] * 3), "label_contains",
        ]
        assert candidate["matches"][1]["evidence_refs"] == ["ev-shutter"]
    # Allow implementation changes while excluding per-record SQL comparisons.
    assert max(counts) <= 12, counts
    assert abs(counts[1] - counts[0]) <= 2, counts


def test_missing_semantic_capability_is_explicit(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE semantic_relationships")
        connection.execute("DROP TABLE semantic_properties")
        connection.execute("DROP TABLE semantic_bindings")
        connection.execute("DROP TABLE semantic_entities")
    with QueryCore(database) as core:
        result = core.search_semantic_entities("D-101")
    assert result["status"] == "capability_unavailable"
    assert result["capability"]["semantic_projection"] is False
    assert result["candidates"] == []


def test_semantic_find_cli_is_stdlib_safe(tmp_path: Path) -> None:
    database = _database(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-S", "-m", "tools.query_core", "semantic-find",
         str(database), "D-101", "--entity-class", "Door", "--limit", "20"],
        check=True, capture_output=True, text=True,
    )
    result = json.loads(completed.stdout)
    assert result["status"] == "multiple_exact"
    assert [candidate["semantic_entity"]["id"] for candidate in
            result["candidates"]] == ["door-a", "door-b"]
