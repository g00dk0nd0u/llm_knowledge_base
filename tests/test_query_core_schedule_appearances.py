from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.query_core.fixtures import (
    create_synthetic_pdf,
    write_synthetic_revit_snapshot,
)
from tools.query_core.query import QueryCore
from tools.query_core.revit_snapshot import import_snapshot


def test_schedule_appearances_reuse_drawing_context_without_relationships(
    tmp_path: Path,
) -> None:
    drawing = create_synthetic_pdf(tmp_path / "drawing.pdf")
    snapshot = write_synthetic_revit_snapshot(drawing, tmp_path / "snapshot.json")
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    records = payload["records"]
    schedule_view = {
        **records["views"][0],
        "id": "view-door-schedule",
        "name": "Door Schedule",
        "view_type": "Schedule",
        "source_unique_id": "schedule-view-uid",
    }
    records["views"].append(schedule_view)
    second_sheet = {
        **records["sheets"][0],
        "id": "sheet-schedule-2",
        "number": "A-602",
        "name": "Door Schedule 2",
        "pdf_page": 4,
        "export_order": 4,
        "source_unique_id": "sheet-2-uid",
    }
    records["sheets"].append(second_sheet)
    base_viewport = records["viewports"][0]
    records["viewports"].extend([
        {
            **base_viewport,
            "id": "schedule-instance-1",
            "sheet_id": records["sheets"][0]["id"],
            "view_id": schedule_view["id"],
            "placement_kind": "schedule",
        },
        {
            **base_viewport,
            "id": "schedule-instance-2",
            "sheet_id": second_sheet["id"],
            "view_id": schedule_view["id"],
            "placement_kind": "schedule",
        },
    ])

    element = {**records["elements"][0], "id": "element-schedule-only",
               "source_unique_id": "scheduled-door-uid"}
    element_type = {**records["element_types"][0], "id": "type-scheduled-door",
                    "source_unique_id": "scheduled-door-type-uid"}
    element["type_id"] = element_type["id"]
    space = {**records["spaces"][0], "id": "space-scheduled-room",
             "source_unique_id": "scheduled-room-uid"}
    records["elements"].append(element)
    records["element_types"].append(element_type)
    records["spaces"].append(space)

    members = (
        ("element", element["id"]),
        ("element_type", element_type["id"]),
        ("space", space["id"]),
    )
    for kind, entity_id in members:
        for suffix, sheet, viewport, page in (
            ("1", records["sheets"][0], "schedule-instance-1", 1),
            ("2", second_sheet, "schedule-instance-2", 4),
        ):
            records["entity_appearances"].append({
                "id": f"schedule-appearance-{kind}-{suffix}",
                "entity_kind": kind,
                "entity_id": entity_id,
                "sheet_id": sheet["id"],
                "view_id": schedule_view["id"],
                "viewport_id": viewport,
                "link_instance_id": None,
                "pdf_page": page,
                "x_min": None,
                "y_min": None,
                "x_max": None,
                "y_max": None,
                "coordinate_space": "pdf_points_top_left",
                "appearance_kind": "schedule",
                "bbox_quality": "page_only",
                "provenance": "revit_api",
            })

    model_type_appearance = next(
        row for row in records["entity_appearances"]
        if row["appearance_kind"] == "model"
    )
    records["entity_appearances"].append({
        **model_type_appearance,
        "id": "model-appearance-scheduled-door-type",
        "entity_kind": "element_type",
        "entity_id": element_type["id"],
    })

    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    database = import_snapshot(snapshot, tmp_path / "schedule.sqlite")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO semantic_entities "
            "(id,entity_class,instance_or_type,resolution_state,provenance) "
            "VALUES ('semantic-door-type','DoorType','type','exact','test')"
        )
        connection.execute(
            "INSERT INTO semantic_bindings "
            "(id,semantic_entity_id,source_kind,source_id,resolution_state,provenance) "
            "VALUES ('binding-door-type','semantic-door-type','element_type',?,"
            "'exact','test')",
            (element_type["id"],),
        )
    with QueryCore(database) as query:
        element_occurrences = query.get_occurrence_evidence(
            "element", element["id"]
        )
        navigation = query.get_navigation_targets("element", element["id"])
        semantic = query.get_semantic_drawing_context("semantic-door-type")
        architectural = query.get_architectural_evidence_context("semantic-door-type")
        relationship_count = query.connection.execute(
            "SELECT count(*) FROM semantic_relationships"
        ).fetchone()[0]

    schedule_occurrences = [
        row for row in element_occurrences if row["appearance_kind"] == "schedule"
    ]
    assert len(schedule_occurrences) == 2
    assert {row["pdf_page"] for row in schedule_occurrences} == {1, 4}
    assert {row["view_id"] for row in schedule_occurrences} == {schedule_view["id"]}
    assert {row["bbox_quality"] for row in schedule_occurrences} == {"page_only"}
    assert all(row["bbox"] is None for row in schedule_occurrences)
    assert {row["pdf_page"] for row in navigation
            if row["appearance_id"].startswith("schedule-appearance-")} == {1, 4}
    assert {row["appearance_kind"] for row in semantic["occurrences"]} == {"model", "schedule"}
    assert {row["pdf_page"] for row in semantic["occurrences"]} == {1, 4}
    assert architectural is not None
    occurrences = architectural["drawing_occurrences"]
    assert {row["appearance_kind"] for row in occurrences} == {"model", "schedule"}
    schedule_context = [row for row in occurrences if row["appearance_kind"] == "schedule"]
    assert all(row["bbox_quality"] == "page_only" for row in schedule_context)
    assert all(row["bbox"] is None for row in schedule_context)
    assert len([row for row in occurrences if row["appearance_kind"] == "model"]) == 1
    assert architectural["relationships"] == []
    assert relationship_count == 0
    assert not any(
        row["entity_kind"] == "element" and row["entity_id"] == space["id"]
        for row in records["entity_appearances"]
    )
    assert not any(
        row["entity_id"] == element["id"] and row["appearance_kind"] == "model"
        for row in records["entity_appearances"]
    )
