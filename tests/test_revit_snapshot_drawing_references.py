from __future__ import annotations

import json

import pytest

from tools.query_core.errors import QueryCoreError
from tools.query_core.fixtures import (
    create_synthetic_pdf,
    write_synthetic_revit_snapshot,
)
from tools.query_core.query import QueryCore
from tools.query_core.revit_snapshot import import_snapshot, load_snapshot


def _snapshot(tmp_path):
    drawing = create_synthetic_pdf(tmp_path / "drawing.pdf")
    path = write_synthetic_revit_snapshot(drawing, tmp_path / "snapshot.json")
    return path, json.loads(path.read_text(encoding="utf-8"))


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _add_reference(payload, *, target_view_id, target_sheet_id):
    payload["records"]["evidence"].append({
        "id": "ev-reference-marker", "document_id": "doc-drawings",
        "sheet_id": "sheet-a201", "view_id": "view-level2", "pdf_page": 3,
        "x_min": None, "y_min": None, "x_max": None, "y_max": None,
        "coordinate_space": None,
    })
    payload["records"]["drawing_references"] = [{
        "id": "reference-marker", "source_evidence_id": "ev-reference-marker",
        "relation_type": "section_reference_to", "printed_reference": None,
        "target_view_id": target_view_id, "target_sheet_id": target_sheet_id,
        "target_evidence_id": None, "resolution_state": "exact",
        "provenance": "revit_api",
    }]


def test_old_v1_without_drawing_references_remains_importable(tmp_path):
    path, payload = _snapshot(tmp_path)
    assert "drawing_references" not in payload["records"]

    assert load_snapshot(path)["snapshot_version"] == 1
    database = import_snapshot(path, tmp_path / "old.sqlite")
    with QueryCore(database) as core:
        assert core.has_drawing_reference_capability() is True
        assert core.get_drawing_reference("missing") is None


def test_new_v1_empty_drawing_reference_capability_imports(tmp_path):
    path, payload = _snapshot(tmp_path)
    payload["records"]["drawing_references"] = []
    _write(path, payload)

    assert load_snapshot(path)["records"]["drawing_references"] == []
    import_snapshot(path, tmp_path / "empty.sqlite")


@pytest.mark.parametrize(
    ("target_view_id", "target_sheet_id", "expected_sheet", "expected_page"),
    [
        ("view-roof", None, None, None),
        ("view-dock", "sheet-a312", "sheet-a312", 1),
    ],
)
def test_resolved_revit_reference_keeps_view_and_optional_navigation(
    tmp_path, target_view_id, target_sheet_id, expected_sheet, expected_page,
):
    path, payload = _snapshot(tmp_path)
    _add_reference(
        payload, target_view_id=target_view_id, target_sheet_id=target_sheet_id,
    )
    _write(path, payload)

    database = import_snapshot(path, tmp_path / "resolved.sqlite")
    with QueryCore(database) as core:
        assert core.has_drawing_reference_capability() is True
        reference = core.get_drawing_reference("reference-marker")
        assert reference["resolution_state"] == "exact"
        assert reference["target"]["view"]["id"] == target_view_id
        assert (
            reference["target"]["sheet"]["id"]
            if reference["target"]["sheet"] else None
        ) == expected_sheet
        assert (
            reference["target"]["navigation"]["pdf_page"]
            if reference["target"]["navigation"] else None
        ) == expected_page


def test_relationally_malformed_reference_fails_query_core_build(tmp_path):
    path, payload = _snapshot(tmp_path)
    _add_reference(payload, target_view_id=None, target_sheet_id=None)
    _write(path, payload)

    load_snapshot(path)
    with pytest.raises(QueryCoreError, match="requires a target"):
        import_snapshot(path, tmp_path / "malformed.sqlite")
