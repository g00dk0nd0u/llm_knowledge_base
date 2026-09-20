from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools.query_core.build import build_database
from tools.query_core.errors import QueryCoreError
from tools.query_core.fixtures import create_synthetic_pdf, synthetic_records
from tools.query_core.geometry import (
    longitudinal_overlap,
    point_segment_distance,
    segment_distance,
)
from tools.query_core.query import QueryCore


def test_pure_finite_geometry_math() -> None:
    assert point_segment_distance((5, 3, 0), (0, 0, 0), (10, 0, 0)) == 3
    assert point_segment_distance((15, 4, 0), (0, 0, 0), (10, 0, 0)) == pytest.approx(41**0.5)
    assert segment_distance((0, 0, 0), (10, 0, 0), (0, 3, 4), (10, 3, 4)) == pytest.approx(5)
    assert segment_distance((0, 0, 0), (0, 0, 0), (3, 0, 0), (3, 4, 0)) == pytest.approx(3)
    assert segment_distance((0, 0, 0), (10, 0, 0), (5, -5, 2), (5, 5, 2)) == pytest.approx(2)


def test_longitudinal_overlap_is_parallel_finite_and_direction_independent() -> None:
    assert longitudinal_overlap(
        (0, 0, 0), (10, 0, 0), (8, 2, 0), (3, 2, 0)
    ) == pytest.approx(5)
    assert longitudinal_overlap(
        (0, 0, 0), (10, 0, 0), (10, 4, 0), (15, 4, 0)
    ) is None
    assert longitudinal_overlap(
        (0, 0, 0), (10, 0, 0), (11, 4, 0), (15, 4, 0)
    ) is None
    assert longitudinal_overlap(
        (0, 0, 0), (10, 0, 0), (3, 2, 0), (3, 8, 0)
    ) is None
    assert longitudinal_overlap(
        (0, 0, 0), (0, 0, 0), (0, 1, 0), (2, 1, 0)
    ) is None


def _geometry_records(tmp_path: Path) -> dict:
    drawing = create_synthetic_pdf(tmp_path / "drawing.pdf")
    records = synthetic_records(drawing)
    template = next(row for row in records["elements"] if row["id"] == "wall-north")
    records["geometries"] = []
    for index, entity_id in enumerate(
        ("a", "ambiguous", "b", "box", "c", "d", "overlap", "touch")
    ):
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
        # A second indexed row for B must not duplicate the occurrence result.
        geometry("bbox-b", "b", "bbox3d", {"min": [3, 0, 0], "max": [3, 4, 0]}, (3, 3, 0, 4, 0, 0)),
        # Its broad-phase box intersects a radius-5 query, but its exact line does not.
        geometry("line-c", "c", "line", {"start": [4, 4, 4], "end": [4, 4, 8]}, (4, 4, 4, 4, 4, 8)),
        geometry("box", "box", "bbox3d", {"min": [-1, -1, 1], "max": [1, 1, 2]}, (-1, 1, -1, 1, 1, 2)),
        geometry("point-d", "d", "point", {"point": [0, 3, 0]}, (0, 0, 3, 3, 0, 0)),
        geometry("ambiguous-a", "ambiguous", "point", {"point": [1, 0, 0]}, (1, 1, 0, 0, 0, 0)),
        geometry("ambiguous-b", "ambiguous", "line", {"start": [1, 0, 0], "end": [2, 0, 0]}, (1, 2, 0, 0, 0, 0)),
        geometry("bbox-touch", "touch", "bbox3d", {"min": [0, 0, 2], "max": [1, 1, 3]}, (0, 1, 0, 1, 2, 3)),
        geometry("bbox-overlap", "overlap", "bbox3d", {"min": [0, 0, 1.5], "max": [1, 1, 3]}, (0, 1, 0, 1, 1.5, 3)),
        geometry("linked-a", "element-linked-collision", "point", {"point": [10, 0, 0]}, (10, 10, 0, 0, 0, 0), "link-instance-a"),
        geometry("linked-bbox-a", "element-linked-collision", "bbox3d", {"min": [10, 0, 10], "max": [11, 1, 20]}, (10, 11, 0, 1, 10, 20), "link-instance-a"),
        geometry("linked-b", "element-linked-collision", "point", {"point": [10010, 0, 0]}, (10010, 10010, 0, 0, 0, 0), "link-instance-b"),
        geometry("linked-bbox-b", "element-linked-collision", "bbox3d", {"min": [10010, 0, -20], "max": [10011, 1, -10]}, (10010, 10011, 0, 1, -20, -10), "link-instance-b"),
    ])
    return records


def _database(tmp_path: Path) -> Path:
    return build_database(_geometry_records(tmp_path), tmp_path / "geometry.sqlite")


@pytest.mark.parametrize(
    ("geometry_id", "bound", "value"),
    (("point-a", "max_x", -1), ("line-b", "min_y", 1)),
)
def test_build_rejects_location_bounds_that_exclude_exact_primitive(
    tmp_path: Path, geometry_id: str, bound: str, value: float
) -> None:
    records = _geometry_records(tmp_path)
    row = next(item for item in records["geometries"] if item["id"] == geometry_id)
    row[bound] = value

    with pytest.raises(QueryCoreError, match=geometry_id):
        build_database(records, tmp_path / "invalid-bounds.sqlite")


def test_build_accepts_exact_and_conservative_location_bounds(tmp_path: Path) -> None:
    records = _geometry_records(tmp_path)
    point = next(item for item in records["geometries"] if item["id"] == "point-a")
    line = next(item for item in records["geometries"] if item["id"] == "line-b")
    # point-a keeps exact scalar bounds; line-b uses a deliberately wider index box.
    line.update(min_x=-100, max_x=100, min_y=-100, max_y=100, min_z=-100, max_z=100)

    database = build_database(records, tmp_path / "valid-bounds.sqlite")

    assert database.exists()
    assert point["min_x"] == point["max_x"] == 0


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


def test_public_point_to_point_distance_contract(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as query:
        result = query.get_location_distance("element", "a", "element", "d")

    assert result["status"] == "ok"
    assert result["distance"] == pytest.approx(3)
    assert result["unit"] == "mm"
    assert result["geometry_ids"] == ["point-a", "point-d"]
    assert result["occurrence_a"] == {
        "entity_kind": "element", "entity_id": "a", "link_instance_id": None
    }
    assert result["occurrence_b"] == {
        "entity_kind": "element", "entity_id": "d", "link_instance_id": None
    }


def test_ambiguous_geometry_is_never_selected(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as query:
        distance = query.get_location_distance("element", "a", "element", "ambiguous")
        nearby = query.find_nearby_by_location("element", "a", 5)
        nearest = query.get_nearest_by_location(
            "element", "a", 5, target_kind="element"
        )

    assert distance["status"] == "ambiguous_geometry"
    # Geometry rows follow the public (link instance, geometry type, id) order.
    assert distance["geometry_ids"] == ["ambiguous-b", "ambiguous-a"]
    assert all(item["entity"]["id"] != "ambiguous" for item in nearby["results"])
    skipped = {item["occurrence"]["entity_id"]: item["reason"] for item in nearby["coverage"]["skipped"]}
    assert skipped["ambiguous"] == "ambiguous_geometry"
    assert nearest["nearest"]["entity"]["id"] != "ambiguous"


def test_get_entity_geometries_contract_and_link_filter(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as query:
        rows = query.get_entity_geometries("element", "a")
        linked = query.get_entity_geometries(
            "element", "element-linked-collision", "link-instance-a"
        )

    assert [row["id"] for row in rows] == ["bbox-a", "point-a"]
    assert rows[0]["geometry"] == {"max": [0, 0, 0], "min": [0, 0, 0]}
    assert "geometry_json" not in rows[0]
    assert all(row["provenance"] == "synthetic:test" for row in rows)
    assert all(row["confidence"] is None and row["evidence"] is None for row in rows)
    assert [row["id"] for row in linked] == ["linked-bbox-a", "linked-a"]
    assert {row["link_instance_id"] for row in linked} == {"link-instance-a"}


def test_incompatible_geometry_is_not_converted_defensively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with QueryCore(_database(tmp_path)) as query:
        stored_get = query.get_entity_geometries

        def incompatible(kind: str, entity_id: str, link_instance_id: str | None = None):
            rows = stored_get(kind, entity_id, link_instance_id)
            # Valid v2 payloads enforce host_revit_internal_origin/mm. This narrow
            # test double exercises the read-time guard without weakening schema v2.
            if entity_id == "d":
                for row in rows:
                    row["unit"] = "ft"
            return rows

        monkeypatch.setattr(query, "get_entity_geometries", incompatible)
        result = query.get_location_distance("element", "a", "element", "d")

    assert result["status"] == "insufficient_data"
    assert result["reason"] == "incompatible_coordinate_system_or_unit"
    assert "distance" not in result


def test_indexed_nearby_and_bounded_nearest(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as query:
        nearby = query.find_nearby_by_location("element", "a", 5)
        assert [(item["entity"]["id"], item["distance"]) for item in nearby["results"]] == [("b", 3), ("d", 3)]
        assert [item["entity"]["id"] for item in nearby["results"]].count("b") == 1
        assert any(item["occurrence"]["entity_id"] == "box" for item in nearby["coverage"]["skipped"])
        nearest = query.get_nearest_by_location("element", "a", 5)
        assert nearest["status"] == "ok" and nearest["nearest"]["entity"]["id"] == "b"
        assert query.get_nearest_by_location("element", "a", 2)["status"] == "insufficient_data"
        with pytest.raises(QueryCoreError):
            query.find_nearby_by_location("element", "a", float("inf"))


def test_nearby_order_is_deterministic_and_nearest_can_be_pure_no_match(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as query:
        first = query.find_nearby_by_location("element", "a", 5)
        second = query.find_nearby_by_location("element", "a", 5)
        no_match = query.get_nearest_by_location(
            "element", "a", 5, target_kind="space"
        )

    assert first == second
    assert [item["entity"]["id"] for item in first["results"]] == ["b", "d"]
    assert no_match["status"] == "no_match"
    assert no_match["coverage"]["skipped"] == []


def test_conservative_vertical_relation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with QueryCore(database) as query:
        # Point A has Z range 0; bbox is strictly above it.
        above = query.get_vertical_relation("element", "box", "element", "a")
        assert above["status"] == "ok" and above["relation"] == "above" and above["z_separation"] == 1
        below = query.get_vertical_relation("element", "a", "element", "box")
        assert below["relation"] == "below"
        assert query.get_vertical_relation("element", "element-linked-collision", "element", "box")["status"] == "ambiguous_occurrence"
        assert query.get_vertical_relation("element", "touch", "element", "box")["relation"] == "indeterminate"
        assert query.get_vertical_relation("element", "overlap", "element", "box")["relation"] == "indeterminate"


def test_selected_link_occurrence_uses_its_stored_host_coordinates(tmp_path: Path) -> None:
    with QueryCore(_database(tmp_path)) as query:
        distance_a = query.get_location_distance(
            "element", "a", "element", "element-linked-collision",
            link_instance_id_b="link-instance-a",
        )
        distance_b = query.get_location_distance(
            "element", "a", "element", "element-linked-collision",
            link_instance_id_b="link-instance-b",
        )
        vertical_a = query.get_vertical_relation(
            "element", "element-linked-collision", "element", "box",
            link_instance_id_a="link-instance-a",
        )
        vertical_b = query.get_vertical_relation(
            "element", "element-linked-collision", "element", "box",
            link_instance_id_a="link-instance-b",
        )

    assert (distance_a["distance"], distance_a["occurrence_b"]["link_instance_id"]) == (10, "link-instance-a")
    assert (distance_b["distance"], distance_b["occurrence_b"]["link_instance_id"]) == (10010, "link-instance-b")
    assert (vertical_a["relation"], vertical_a["occurrence_a"]["link_instance_id"]) == ("above", "link-instance-a")
    assert (vertical_b["relation"], vertical_b["occurrence_a"]["link_instance_id"]) == ("below", "link-instance-b")
