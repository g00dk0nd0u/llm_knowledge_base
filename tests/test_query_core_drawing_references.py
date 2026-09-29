from __future__ import annotations

import sqlite3

import pytest

from tools.query_core.build import SCHEMA_VERSION, build_database
from tools.query_core.errors import QueryCoreError
from tools.query_core.fixtures import create_synthetic_pdf, synthetic_records
from tools.query_core.query import QueryCore


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
