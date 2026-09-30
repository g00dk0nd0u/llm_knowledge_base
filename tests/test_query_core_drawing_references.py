from __future__ import annotations

import sqlite3

import pytest

from tools.query_core.build import SCHEMA_VERSION, build_database
from tools.query_core.errors import QueryCoreError
from tools.query_core.fixtures import create_synthetic_pdf, synthetic_records
from tools.query_core.query import (
    QueryCore,
    _DRAWING_REFERENCE_CONTEXT_QUERIES,
    _load_drawing_reference_validation_context,
)


def _records(tmp_path):
    drawing = create_synthetic_pdf(tmp_path / "drawing.pdf")
    return synthetic_records(drawing)


def _reference(reference_id, **values):
    return {
        "id": reference_id,
        "source_evidence_id": "ev-corridor",
        "relation_type": "section_cut_to",
        "printed_reference": "2/A-301",
        "target_view_id": None,
        "target_sheet_id": None,
        "target_evidence_id": None,
        "resolution_state": "unresolved",
        "provenance": "explicit_test_fact",
        **values,
    }


def _add_page_only_target_evidence(records, *, pdf_page=2):
    records["evidence"].append({
        "id": "ev-target-page", "document_id": "doc-drawings",
        "sheet_id": None, "view_id": None, "pdf_page": pdf_page,
        "x_min": 10, "y_min": 20, "x_max": 30, "y_max": 40,
        "coordinate_space": "pdf_points_top_left",
    })


def _replace_reference_table_without_constraints(database):
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE unchecked_references AS SELECT * FROM drawing_references"
    )
    connection.execute("DROP TABLE drawing_references")
    connection.execute("ALTER TABLE unchecked_references RENAME TO drawing_references")
    connection.commit()
    return connection


def test_empty_reference_validation_context_has_fast_path(tmp_path):
    database = build_database(_records(tmp_path), tmp_path / "empty.sqlite")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.set_authorizer(lambda action, *args: (
        sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ
        and args[0] in {"evidence", "views", "sheets", "viewports"}
        else sqlite3.SQLITE_OK
    ))

    assert _load_drawing_reference_validation_context(connection) is None
    connection.close()
    with QueryCore(database):
        pass


def test_reference_validation_context_loads_only_reachable_rows(tmp_path):
    records = _records(tmp_path)
    records["drawing_references"] = [_reference(
        "resolved", resolution_state="exact", target_view_id="view-roof",
        target_sheet_id="sheet-a421", target_evidence_id="ev-roof",
    )]
    records["viewports"].append({
        **records["viewports"][0], "id": "viewport-roof",
        "sheet_id": "sheet-a421", "view_id": "view-roof",
    })
    records["viewports"].append({
        **records["viewports"][0], "id": "viewport-level2",
        "sheet_id": "sheet-a421", "view_id": "view-level2",
    })
    database = build_database(records, tmp_path / "selective.sqlite")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row

    context = _load_drawing_reference_validation_context(connection)

    assert context is not None
    references, evidence, views, sheets, viewports = context
    assert {row["id"] for row in references} == {"resolved"}
    assert {row["id"] for row in evidence} == {"ev-corridor", "ev-roof"}
    assert {row["id"] for row in views} == {"view-roof"}
    assert {row["id"] for row in sheets} == {"sheet-a421"}
    assert {row["id"] for row in viewports} == {"viewport-roof"}
    connection.close()


def test_reference_validation_queries_use_noncorrelated_access_paths(tmp_path):
    records = _records(tmp_path)
    records["drawing_references"] = [_reference(
        "resolved", resolution_state="exact", target_view_id="view-roof",
        target_sheet_id="sheet-a421", target_evidence_id="ev-roof",
    )]
    database = build_database(records, tmp_path / "query-plans.sqlite")
    connection = sqlite3.connect(database)

    plans = {
        name: [row[3] for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}")]
        for name, sql in _DRAWING_REFERENCE_CONTEXT_QUERIES.items()
    }

    for plan in plans.values():
        assert not any("CORRELATED SCALAR SUBQUERY" in detail for detail in plan)
    for table in ("evidence", "views", "sheets"):
        assert any(f"SEARCH {table[0]} USING" in detail for detail in plans[table])
        assert any("SCAN r" in detail for detail in plans[table])
        assert not any(f"SCAN {table[0]}" in detail for detail in plans[table])
    connection.close()


def test_resolved_unresolved_ambiguous_and_extensible_references(tmp_path):
    records = _records(tmp_path)
    records["drawing_references"] = [
        _reference(
            "resolved", resolution_state="exact", target_view_id="view-roof",
            target_sheet_id="sheet-a421",
        ),
        _reference("unresolved", printed_reference="2 / A-999"),
        _reference("ambiguous", printed_reference="2/A-???", resolution_state="ambiguous"),
        _reference("generic", relation_type="future_relation_vocabulary"),
    ]
    database = build_database(records, tmp_path / "references.sqlite")

    with QueryCore(database) as core:
        assert core.has_drawing_reference_capability() is True
        resolved = core.get_drawing_reference("resolved")
        assert resolved["source"]["navigation"]["pdf_page"] == 3
        assert resolved["target"]["view"]["id"] == "view-roof"
        assert resolved["target"]["sheet"]["number"] == "A-421"
        assert resolved["target"]["navigation"]["pdf_page"] == 2
        assert core.get_drawing_reference("unresolved")["target"] is None
        assert core.get_drawing_reference("unresolved")["printed_reference"] == "2 / A-999"
        assert core.get_drawing_reference("ambiguous")["target"] is None
        assert core.get_drawing_reference("generic")["relation_type"] == "future_relation_vocabulary"
        assert [row["id"] for row in core.get_drawing_references_for_view("view-level2")] == [
            "generic", "ambiguous", "resolved", "unresolved"
        ]


@pytest.mark.parametrize(
    "reference,error",
    [
        (_reference("bad-source", source_evidence_id="missing"), "source evidence"),
        (_reference("bad-resolved", resolution_state="exact"), "requires a target"),
        (_reference("bad-type", relation_type="   "), "relation_type"),
    ],
)
def test_invalid_reference_facts_are_rejected(tmp_path, reference, error):
    records = _records(tmp_path)
    records["drawing_references"] = [reference]
    with pytest.raises(QueryCoreError, match=error):
        build_database(records, tmp_path / "invalid.sqlite")


def test_cross_document_and_provable_placement_mismatch_are_rejected(tmp_path):
    records = _records(tmp_path)
    records["documents"].append({
        "id": "doc-other", "identity": "other", "title": "Other",
        "source_filename": "other.pdf", "source_sha256": "a" * 64,
    })
    records["sheets"].append({
        "id": "sheet-other", "document_id": "doc-other", "number": "X-1",
        "name": "Other", "pdf_page": 1,
    })
    records["drawing_references"] = [_reference(
        "cross", resolution_state="exact", target_view_id="view-roof",
        target_sheet_id="sheet-other",
    )]
    with pytest.raises(QueryCoreError, match="different documents"):
        build_database(records, tmp_path / "cross.sqlite")

    records = _records(tmp_path)
    records["drawing_references"] = [_reference(
        "mismatch", resolution_state="exact", target_view_id="view-dock",
        target_sheet_id="sheet-a421",
    )]
    with pytest.raises(QueryCoreError, match="not placed"):
        build_database(records, tmp_path / "mismatch.sqlite")


def test_target_sheet_and_evidence_pages_must_agree_at_build_time(tmp_path):
    records = _records(tmp_path)
    _add_page_only_target_evidence(records, pdf_page=99)
    records["drawing_references"] = [_reference(
        "page-mismatch", resolution_state="exact",
        target_sheet_id="sheet-a421", target_evidence_id="ev-target-page",
    )]
    with pytest.raises(QueryCoreError, match="evidence/sheet page mismatch"):
        build_database(records, tmp_path / "page-mismatch.sqlite")

    records["evidence"][-1]["pdf_page"] = 2
    database = build_database(records, tmp_path / "page-match.sqlite")
    with QueryCore(database) as core:
        assert core.get_drawing_reference("page-mismatch")["target"][
            "navigation"
        ]["pdf_page"] == 2


@pytest.mark.parametrize(
    "sql,params,error",
    [
        ("UPDATE drawing_references SET target_view_id=NULL, target_sheet_id=NULL, "
         "target_evidence_id=NULL", (), "requires a target"),
        ("UPDATE drawing_references SET resolution_state='unresolved'", (),
         "must not select a target"),
        ("UPDATE drawing_references SET source_evidence_id='missing'", (),
         "source evidence"),
        ("UPDATE drawing_references SET target_view_id='missing'", (),
         "target view"),
        ("UPDATE drawing_references SET target_sheet_id='sheet-other'", (),
         "different documents"),
        ("UPDATE drawing_references SET target_view_id='view-dock'", (),
         "evidence/view mismatch"),
        ("UPDATE drawing_references SET target_sheet_id='sheet-a312'", (),
         "evidence/sheet mismatch"),
        ("UPDATE evidence SET sheet_id=NULL, pdf_page=99 WHERE id='ev-roof'", (),
         "evidence/sheet page mismatch"),
        ("UPDATE drawing_references SET target_evidence_id=NULL, "
         "target_view_id='view-dock'", (), "not placed"),
    ],
)
def test_open_rejects_semantically_invalid_unchecked_capability(
    tmp_path, sql, params, error,
):
    records = _records(tmp_path)
    records["documents"].append({
        "id": "doc-other", "identity": "other", "title": "Other",
        "source_filename": "other.pdf", "source_sha256": "a" * 64,
    })
    records["sheets"].append({
        "id": "sheet-other", "document_id": "doc-other", "number": "X-1",
        "name": "Other", "pdf_page": 1, "export_order": 4,
        "source_model_id": "model-host", "source_unique_id": "sheet-other",
    })
    records["drawing_references"] = [_reference(
        "resolved", resolution_state="exact", target_view_id="view-roof",
        target_sheet_id="sheet-a421", target_evidence_id="ev-roof",
    )]
    database = build_database(records, tmp_path / "unchecked.sqlite")
    connection = _replace_reference_table_without_constraints(database)
    connection.execute(sql, params)
    connection.commit()
    connection.close()

    with pytest.raises(QueryCoreError, match=error):
        QueryCore(database)


def test_resolved_target_shapes_keep_resolution_separate_from_navigation(tmp_path):
    records = _records(tmp_path)
    records["drawing_references"] = [
        _reference("view-only", resolution_state="exact", target_view_id="view-roof"),
        _reference("sheet-only", resolution_state="exact", target_sheet_id="sheet-a421"),
        _reference("evidence-only", resolution_state="exact", target_evidence_id="ev-roof"),
        _reference(
            "full", resolution_state="resolved_deterministically",
            target_view_id="view-roof", target_sheet_id="sheet-a421",
            target_evidence_id="ev-roof",
        ),
    ]
    database = build_database(records, tmp_path / "target-shapes.sqlite")

    with QueryCore(database) as core:
        view_only = core.get_drawing_reference("view-only")
        assert view_only["resolution_state"] == "exact"
        assert view_only["target"]["view"]["id"] == "view-roof"
        assert view_only["target"]["sheet"] is None
        assert view_only["target"]["evidence"] is None
        assert view_only["target"]["navigation"] is None

        sheet_only = core.get_drawing_reference("sheet-only")["target"]
        assert sheet_only["sheet"]["id"] == "sheet-a421"
        assert sheet_only["navigation"]["pdf_page"] == 2
        assert sheet_only["navigation"]["can_zoom"] is False

        evidence_only = core.get_drawing_reference("evidence-only")["target"]
        assert evidence_only["view"] is None and evidence_only["sheet"] is None
        assert evidence_only["evidence"]["id"] == "ev-roof"
        assert evidence_only["navigation"]["pdf_page"] == 2

        full = core.get_drawing_reference("full")["target"]
        assert full["view"]["id"] == "view-roof"
        assert full["sheet"]["id"] == "sheet-a421"
        assert full["evidence"]["id"] == "ev-roof"
        assert full["navigation"]["bbox"] == [410.0, 110.0, 560.0, 180.0]

        for reference_id in ("view-only", "sheet-only", "evidence-only", "full"):
            result = core.get_drawing_reference(reference_id)
            for navigation in (
                result["source"]["navigation"], result["target"]["navigation"],
            ):
                if navigation is not None:
                    assert isinstance(navigation["pdf_page"], int)
                    assert navigation["pdf_page"] >= 1


def test_pdf_only_capability_and_legacy_v2_compatibility(tmp_path):
    records = {
        "project_id": "portable", "created_from": "test", "binding_mode": "project",
        "documents": [{
            "id": "doc", "identity": "portable", "title": "Portable",
            "source_filename": "portable.pdf", "source_sha256": "b" * 64,
        }],
        "evidence": [{
            "id": "source", "document_id": "doc", "sheet_id": None,
            "view_id": None, "pdf_page": 1, "x_min": 1, "y_min": 2,
            "x_max": 3, "y_max": 4, "coordinate_space": "pdf_points_top_left",
        }],
        "drawing_references": [{
            **_reference("portable-reference"), "source_evidence_id": "source",
        }],
    }
    database = build_database(records, tmp_path / "portable.sqlite")
    with QueryCore(database) as core:
        assert core.get_drawing_reference("portable-reference")["target"] is None

    connection = sqlite3.connect(database)
    connection.execute("DROP TABLE drawing_references")
    connection.commit()
    connection.close()
    with QueryCore(database) as legacy:
        assert legacy.has_drawing_reference_capability() is False
        assert legacy.get_pdf_evidence("source")["pdf_page"] == 1
    assert SCHEMA_VERSION == 2
