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


def test_ordinary_callout_same_parent_multiple_occurrences_remain_distinct(tmp_path):
    path, payload = _snapshot(tmp_path)
    parent_view_id = "view-level2"
    base_viewport = payload["records"]["viewports"][0]

    for suffix, sheet_id, page in (
        ("a", "sheet-a201", 3),
        ("b", "sheet-a312", 1),
    ):
        placement = dict(base_viewport)
        placement.update(
            id=f"vp-level2-{suffix}", sheet_id=sheet_id, view_id=parent_view_id,
        )
        payload["records"]["viewports"].append(placement)

        evidence_id = f"ev-ordinary-callout-{suffix}"
        payload["records"]["evidence"].append({
            "id": evidence_id, "document_id": "doc-drawings",
            "sheet_id": sheet_id, "view_id": parent_view_id, "pdf_page": page,
            "x_min": None, "y_min": None, "x_max": None, "y_max": None,
            "coordinate_space": None,
        })
        payload["records"].setdefault("drawing_references", []).append({
            "id": f"ordinary-callout-{suffix}",
            "source_evidence_id": evidence_id, "relation_type": "callout_to",
            "printed_reference": None, "target_view_id": "view-roof",
            "target_sheet_id": None, "target_evidence_id": None,
            "resolution_state": "exact", "provenance": "revit_api",
        })

    assert {
        item["view_id"]
        for item in payload["records"]["evidence"]
        if item["id"].startswith("ev-ordinary-callout-")
    } == {parent_view_id}
    _write(path, payload)

    database = import_snapshot(path, tmp_path / "ordinary-callouts.sqlite")
    with QueryCore(database) as core:
        references = [
            core.get_drawing_reference("ordinary-callout-a"),
            core.get_drawing_reference("ordinary-callout-b"),
        ]
        assert {item["source"]["navigation"]["pdf_page"] for item in references} == {1, 3}
        assert {item["id"] for item in references} == {
            "ordinary-callout-a", "ordinary-callout-b",
        }
        assert all(item["relation_type"] == "callout_to" for item in references)
        assert all(item["resolution_state"] == "exact" for item in references)
        assert all(item["target"]["view"]["id"] == "view-roof" for item in references)
        assert all(item["target"]["navigation"] is None for item in references)


def test_relationally_malformed_reference_fails_query_core_build(tmp_path):
    path, payload = _snapshot(tmp_path)
    _add_reference(payload, target_view_id=None, target_sheet_id=None)
    _write(path, payload)

    load_snapshot(path)
    with pytest.raises(QueryCoreError, match="requires a target"):
        import_snapshot(path, tmp_path / "malformed.sqlite")
