from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.pdf_pipeline.characterization import (
    GAPS,
    PROFILES,
    canonical_json,
    characterize,
    fixture_names,
    generate_fixture,
    run_corpus,
)


def _one(tmp_path: Path, fixture: str, profile_name: str = "P0") -> dict:
    path = tmp_path / f"{fixture}.pdf"
    generate_fixture(path, fixture)
    profile = next(profile for profile in PROFILES if profile.name == profile_name)
    return characterize(path, fixture, profile)


def test_corpus_is_complete_and_byte_deterministic(tmp_path: Path) -> None:
    first = canonical_json(run_corpus(tmp_path / "first"))
    second = canonical_json(run_corpus(tmp_path / "second"))
    assert first == second
    decoded = json.loads(first)
    assert len(decoded) == len(fixture_names()) * len(PROFILES)
    assert all(set(item) == {"fixture", "profile", "parameters", "drawing_path_count",
                             "candidate_count", "table_count", "tables", "coordinate_space"}
               for item in decoded)


def test_fully_ruled_p0_is_phase_3a_control(tmp_path: Path) -> None:
    result = _one(tmp_path, "fully_ruled")
    assert result["parameters"] == {"strategy": "lines_strict", "use_layout": False}
    assert result["candidate_count"] == 1
    assert result["tables"][0]["row_count"] == 3
    assert result["tables"][0]["column_count"] == 3
    assert result["tables"][0]["phase_3a_accepted"] is True


@pytest.mark.parametrize("axis", ["horizontal", "vertical"])
def test_gap_sweeps_have_stable_recovery_boundary(tmp_path: Path, axis: str) -> None:
    p0 = [_one(tmp_path, f"{axis}_gap_{gap}")["tables"][0]["phase_3a_accepted"] for gap in GAPS]
    p1 = [_one(tmp_path, f"{axis}_gap_{gap}", "P1-join-6")["tables"][0]["phase_3a_accepted"] for gap in GAPS]
    assert p0 == [True, True, True, False, False, False, False]
    assert p1 == [True, True, True, True, True, False, False]


def test_missing_separators_are_distinct_from_gaps(tmp_path: Path) -> None:
    horizontal = _one(tmp_path, "missing_internal_horizontal")["tables"][0]
    vertical = _one(tmp_path, "missing_internal_vertical")["tables"][0]
    assert (horizontal["row_count"], horizontal["column_count"]) == (2, 3)
    assert (vertical["row_count"], vertical["column_count"]) == (3, 2)


def test_merged_geometry_is_recorded_without_span_guessing(tmp_path: Path) -> None:
    for fixture in ("merged_horizontal", "merged_vertical"):
        table = _one(tmp_path, fixture)["tables"][0]
        assert table["missing_cell_count"] == 1
        assert table["phase_3a_accepted"] is False
        assert table["rejection_reason"] == "merged_or_missing_cell"
        assert table["cell_bboxes"].count(None) == 1


@pytest.mark.parametrize("fixture", ["aligned_prose", "background_rectangles",
                                      "unrelated_rectangles", "floor_plan", "sparse_diagram"])
def test_false_positive_controls_remain_negative_through_join_12(tmp_path: Path, fixture: str) -> None:
    for profile in ("P0", "P1-join-6", "P1-join-12"):
        assert _one(tmp_path, fixture, profile)["candidate_count"] == 0


def test_empty_multiline_japanese_and_rotated_are_deterministic(tmp_path: Path) -> None:
    japanese = _one(tmp_path, "empty_multiline_japanese")
    assert japanese["tables"][0]["missing_cell_count"] == 0
    assert "一行目\n二行目" in sum(japanese["tables"][0]["extracted_text"], [])
    rotated = _one(tmp_path, "rotated_partial", "P1-join-6")
    assert rotated["coordinate_space"] == "pdf_points_top_left"
    assert rotated["tables"][0]["bbox"] == [50.0, 50.0, 320.0, 158.0]


def test_large_schedule_has_bounded_structural_work(tmp_path: Path) -> None:
    result = _one(tmp_path, "large_schedule")
    table = result["tables"][0]
    assert result["drawing_path_count"] == 1
    assert result["candidate_count"] == 1
    assert (table["row_count"], table["column_count"], table["total_cell_slots"]) == (20, 12, 240)
    assert table["linked_source_span_count"] == 240
    assert table["phase_3a_accepted"] is True


def test_profiles_are_explicit_and_never_fully_text_based() -> None:
    for profile in PROFILES:
        parameters = profile.parameters
        assert parameters.get("strategy") != "text"
        assert not (parameters.get("vertical_strategy") == parameters.get("horizontal_strategy") == "text")
        assert parameters["use_layout"] is False
