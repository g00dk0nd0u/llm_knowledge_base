from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools.query_core.build import build_database
from tools.query_core.errors import QueryCoreError
from tools.query_core.fixtures import create_synthetic_pdf, synthetic_records
from tools.query_core.geometry import point_segment_distance, segment_distance
from tools.query_core.query import QueryCore


def test_pure_finite_geometry_math() -> None:
    assert point_segment_distance((5, 3, 0), (0, 0, 0), (10, 0, 0)) == 3
    assert point_segment_distance((15, 4, 0), (0, 0, 0), (10, 0, 0)) == pytest.approx(41**0.5)
    assert segment_distance((0, 0, 0), (10, 0, 0), (0, 3, 4), (10, 3, 4)) == pytest.approx(5)
    assert segment_distance((0, 0, 0), (0, 0, 0), (3, 0, 0), (3, 4, 0)) == pytest.approx(3)
    assert segment_distance((0, 0, 0), (10, 0, 0), (5, -5, 2), (5, 5, 2)) == pytest.approx(2)


def _database(tmp_path: Path) -> Path:
    drawing = create_synthetic_pdf(tmp_path / "drawing.pdf")
    records = synthetic_records(drawing)
    template = next(row for row in records["elements"] if row["id"] == "wall-north")
    records["geometries"] = []
    for index, entity_id in enumerate(("a", "b", "c", "box")):
        element = copy.deepcopy(template)
        element.update(id=entity_id, source_unique_id=f"geometry-test-{index}", name=entity_id)
        records["elements"].append(element)

    def geometry(identifier: str, entity_id: str, kind: str, value: dict, bounds: tuple[float, ...], link: str | None = None) -> dict:
        return {"id": identifier, "entity_kind": "element", "entity_id": entity_id,
                "link_instance_id": link, "geometry_type": kind, "geometry": value,
                "coordinate_system": "host_revit_internal_origin", "unit": "mm",
                "min_x": bounds[0], "max_x": bounds[1], "min_y": bounds[2], "max_y": bounds[3],
                "min_z": bounds[4], "max_z": bounds[5], "provenance": "synthetic:test",
                "confidence": None, "evidence_id": None}

    records["geometries"].extend([
        geometry("point-a", "a", "point", {"point": [0, 0, 0]}, (0, 0, 0, 0, 0, 0)),
        geometry("bbox-a", "a", "bbox3d", {"min": [0, 0, 0], "max": [0, 0, 0]}, (0, 0, 0, 0, 0, 0)),
        geometry("line-b", "b", "line", {"start": [3, 0, 0], "end": [3, 4, 0]}, (3, 3, 0, 4, 0, 0)),
        # Its broad-phase box intersects a radius-5 query, but its exact line does not.
        geometry("line-c", "c", "line", {"start": [4, 4, 4], "end": [4, 4, 8]}, (4, 4, 4, 4, 4, 8)),
        geometry("box", "box", "bbox3d", {"min": [-1, -1, 1], "max": [1, 1, 2]}, (-1, 1, -1, 1, 1, 2)),
        geometry("linked-a", "element-linked-collision", "point", {"point": [10, 0, 0]}, (10, 10, 0, 0, 0, 0), "link-instance-a"),
        geometry("linked-b", "element-linked-collision", "point", {"point": [10010, 0, 0]}, (10010, 10010, 0, 0, 0, 0), "link-instance-b"),
    ])
    return build_database(records, tmp_path / "geometry.sqlite")


def test_location_distance_occurrences_and_bbox_exclusion(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as query:
        result = query.get_location_distance("element", "a", "element", "b")
        assert result["status"] == "ok" and result["distance"] == 3
        assert query.get_location_distance("element", "a", "element", "box")["status"] == "insufficient_data"
        ambiguous = query.get_location_distance("element", "a", "element", "element-linked-collision")
        assert ambiguous["status"] == "ambiguous_occurrence"
        assert ambiguous["available_occurrence_ids"] == ["link-instance-a", "link-instance-b"]
        selected = query.get_location_distance("element", "a", "element", "element-linked-collision", link_instance_id_b="link-instance-a")
        assert selected["distance"] == 10
        assert query.get_location_distance("element", "a", "element", "b") == result


def test_indexed_nearby_and_bounded_nearest(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as query:
        nearby = query.find_nearby_by_location("element", "a", 5)
        assert [(item["entity"]["id"], item["distance"]) for item in nearby["results"]] == [("b", 3)]
        assert any(item["occurrence"]["entity_id"] == "box" for item in nearby["coverage"]["skipped"])
        nearest = query.get_nearest_by_location("element", "a", 5)
        assert nearest["status"] == "ok" and nearest["nearest"]["entity"]["id"] == "b"
        assert query.get_nearest_by_location("element", "a", 2)["status"] == "insufficient_data"
        with pytest.raises(QueryCoreError):
            query.find_nearby_by_location("element", "a", float("inf"))


def test_conservative_vertical_relation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with QueryCore(database) as query:
        # Point A has Z range 0; bbox is strictly above it.
        above = query.get_vertical_relation("element", "box", "element", "a")
        assert above["status"] == "ok" and above["relation"] == "above" and above["z_separation"] == 1
        below = query.get_vertical_relation("element", "a", "element", "box")
        assert below["relation"] == "below"
        assert query.get_vertical_relation("element", "element-linked-collision", "element", "box")["status"] == "ambiguous_occurrence"
