from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tools.query_core.fixtures import build_synthetic_fixture
from tools.query_core.query import QueryCore


@pytest.fixture
def adjacency_database(tmp_path: Path) -> Path:
    _, source = build_synthetic_fixture(tmp_path)
    database = tmp_path / "adjacency.sqlite"
    shutil.copyfile(source, database)
    return database


def _space(
    connection: sqlite3.Connection,
    space_id: str,
    *,
    source_model_id: str | None = "model-host",
    level_id: str | None = "level-2",
    phase: str | None = "phase-new-construction",
) -> None:
    connection.execute(
        "INSERT INTO spaces VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            space_id,
            "Room",
            space_id,
            space_id,
            level_id,
            source_model_id,
            f"{space_id}-source" if source_model_id else None,
            phase,
            "synthetic:test",
            None,
        ),
    )


def _boundary(
    connection: sqlite3.Connection,
    space_id: str,
    *,
    suffix: str = "outer",
    occurrence: str | None = None,
    loop_kind: str = "outer",
) -> str:
    boundary_id = f"boundary-{space_id}-{suffix}"
    connection.execute(
        "INSERT INTO spatial_boundaries VALUES (?,?,?,?,?,?,?,?)",
        (
            boundary_id,
            space_id,
            occurrence,
            0 if loop_kind == "outer" else 1,
            loop_kind,
            "host_revit_internal_origin",
            "mm",
            "synthetic:test",
        ),
    )
    return boundary_id


def _segment(
    connection: sqlite3.Connection,
    boundary_id: str,
    segment_id: str,
    *,
    start: tuple[float, float, float] = (0, 0, 0),
    end: tuple[float, float, float] = (10, 0, 0),
    source_model_id: str | None = "model-link",
    source_unique_id: str | None = "linked-wall",
    source_link_instance_id: str | None = "link-instance-a",
    curve_kind: str | None = "line",
    occurrence: str | None = None,
    index: int = 0,
) -> None:
    connection.execute(
        "INSERT INTO spatial_boundary_segments VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            segment_id,
            boundary_id,
            occurrence,
            index,
            *start,
            *end,
            source_model_id,
            source_unique_id,
            source_link_instance_id,
            curve_kind,
        ),
    )


def _pair(
    database: Path,
    *,
    subject: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
    subject_segment: dict[str, Any] | None = None,
    candidate_segment: dict[str, Any] | None = None,
    subject_loop: str = "outer",
    candidate_loop: str = "outer",
) -> None:
    with sqlite3.connect(database) as connection:
        _space(connection, "subject", **(subject or {}))
        _space(connection, "candidate", **(candidate or {}))
        subject_boundary = _boundary(
            connection, "subject", loop_kind=subject_loop
        )
        candidate_boundary = _boundary(
            connection, "candidate", loop_kind=candidate_loop
        )
        _segment(
            connection,
            subject_boundary,
            "segment-subject",
            **(subject_segment or {}),
        )
        candidate_values = {
            "start": (8, 2, 0),
            "end": (2, 2, 0),
            **(candidate_segment or {}),
        }
        _segment(
            connection,
            candidate_boundary,
            "segment-candidate",
            **candidate_values,
        )


def test_host_rooms_can_share_one_linked_wall_occurrence(
    adjacency_database: Path,
) -> None:
    _pair(adjacency_database)
    with QueryCore(adjacency_database) as core:
        result = core.get_adjacent_spaces("subject")
    assert result["status"] == "ok"
    assert result["coverage"] == {
        "total_subject_outer_boundary_segments": 1,
        "usable_line_segments": 1,
        "nonlinear_segments_skipped": 0,
        "degenerate_line_segments_skipped": 0,
        "unresolved_source_segments_skipped": 0,
        "candidate_segment_pairs_evaluated": 1,
    }
    assert result["adjacent_spaces"][0]["occurrence"] == {
        "space_id": "candidate",
        "link_instance_id": None,
    }
    assert result["adjacent_spaces"][0]["shared_boundaries"][0][
        "overlap_length_mm"
    ] == pytest.approx(6)


@pytest.mark.parametrize(
    ("candidate", "candidate_segment"),
    [
        ({}, {"source_unique_id": "different-wall"}),
        ({}, {"source_link_instance_id": "link-instance-b"}),
        ({"level_id": "level-other"}, {}),
        ({"phase": "phase-other"}, {}),
        ({}, {"start": (11, 2, 0), "end": (20, 2, 0)}),
        ({}, {"start": (10, 2, 0), "end": (20, 2, 0)}),
    ],
    ids=(
        "different-source-element",
        "different-source-link-occurrence",
        "different-level",
        "different-phase",
        "disjoint-interval",
        "endpoint-only",
    ),
)
def test_matching_geometry_without_complete_semantics_is_not_adjacency(
    adjacency_database: Path,
    candidate: dict[str, Any],
    candidate_segment: dict[str, Any],
) -> None:
    if candidate.get("level_id") == "level-other":
        with sqlite3.connect(adjacency_database) as connection:
            connection.execute(
                "INSERT INTO levels VALUES "
                "('level-other','Other',9000,'mm','model-host','level-other-source',"
                "'synthetic:test',NULL)"
            )
    _pair(
        adjacency_database,
        candidate=candidate,
        candidate_segment=candidate_segment,
    )
    with QueryCore(adjacency_database) as core:
        result = core.get_adjacent_spaces("subject")
    assert result["status"] == "ok"
    assert result["adjacent_spaces"] == []


@pytest.mark.parametrize(
    ("subject", "warning"),
    [
        ({"level_id": None}, "subject_level_unavailable"),
        ({"phase": None}, "subject_phase_unavailable"),
        ({"source_model_id": None}, "subject_source_model_unavailable"),
    ],
)
def test_missing_subject_semantic_context_is_insufficient(
    adjacency_database: Path, subject: dict[str, Any], warning: str
) -> None:
    _pair(adjacency_database, subject=subject)
    with QueryCore(adjacency_database) as core:
        result = core.get_adjacent_spaces("subject")
    assert result["status"] == "insufficient_data"
    assert result["warnings"] == [warning]
    assert result["adjacent_spaces"] == []


@pytest.mark.parametrize("curve_kind", ["arc", "ellipse", "spline", "other"])
def test_nonlinear_subject_boundary_is_not_proof(
    adjacency_database: Path, curve_kind: str
) -> None:
    _pair(adjacency_database, subject_segment={"curve_kind": curve_kind})
    with QueryCore(adjacency_database) as core:
        result = core.get_adjacent_spaces("subject")
    assert result["status"] == "insufficient_data"
    assert result["coverage"]["nonlinear_segments_skipped"] == 1
    assert result["coverage"]["usable_line_segments"] == 0
    assert result["adjacent_spaces"] == []


def test_inner_loop_and_unresolved_source_cannot_prove_adjacency(
    adjacency_database: Path,
) -> None:
    _pair(
        adjacency_database,
        subject_loop="inner",
        subject_segment={"source_model_id": None, "source_unique_id": None},
    )
    with QueryCore(adjacency_database) as core:
        inner = core.get_adjacent_spaces("subject")
    assert inner["status"] == "insufficient_data"
    assert inner["coverage"]["total_subject_outer_boundary_segments"] == 0

    second = adjacency_database.with_name("unresolved.sqlite")
    shutil.copyfile(adjacency_database, second)
    with sqlite3.connect(second) as connection:
        connection.execute(
            "UPDATE spatial_boundaries SET loop_kind='outer',loop_index=0 "
            "WHERE space_id='subject'"
        )
    with QueryCore(second) as core:
        unresolved = core.get_adjacent_spaces("subject")
    assert unresolved["status"] == "insufficient_data"
    assert unresolved["coverage"]["unresolved_source_segments_skipped"] == 1
    assert unresolved["coverage"]["usable_line_segments"] == 0


def test_positive_proof_with_skipped_evidence_is_partial(
    adjacency_database: Path,
) -> None:
    _pair(adjacency_database)
    with sqlite3.connect(adjacency_database) as connection:
        _segment(
            connection,
            "boundary-subject-outer",
            "segment-subject-arc",
            curve_kind="arc",
            index=1,
        )
    with QueryCore(adjacency_database) as core:
        result = core.get_adjacent_spaces("subject")
    assert result["status"] == "partial"
    assert result["warnings"] == ["incomplete_boundary_coverage"]
    assert len(result["adjacent_spaces"]) == 1
    assert result["coverage"]["total_subject_outer_boundary_segments"] == 2
    assert result["coverage"]["usable_line_segments"] == 1
    assert result["coverage"]["nonlinear_segments_skipped"] == 1


def test_repeated_linked_space_occurrences_are_selected_independently(
    adjacency_database: Path,
) -> None:
    with sqlite3.connect(adjacency_database) as connection:
        _space(connection, "subject", source_model_id="model-link")
        _space(connection, "candidate", source_model_id="model-link")
        for occurrence, offset in (("link-instance-a", 0), ("link-instance-b", 100)):
            subject_boundary = _boundary(
                connection,
                "subject",
                suffix=occurrence,
                occurrence=occurrence,
            )
            candidate_boundary = _boundary(
                connection,
                "candidate",
                suffix=occurrence,
                occurrence=occurrence,
            )
            _segment(
                connection,
                subject_boundary,
                f"subject-{occurrence}",
                start=(offset, 0, 0),
                end=(offset + 10, 0, 0),
                occurrence=occurrence,
                source_link_instance_id=occurrence,
            )
            _segment(
                connection,
                candidate_boundary,
                f"candidate-{occurrence}",
                start=(offset + 2, 2, 0),
                end=(offset + 8, 2, 0),
                occurrence=occurrence,
                source_link_instance_id=occurrence,
            )
    with QueryCore(adjacency_database) as core:
        ambiguous = core.get_adjacent_spaces("subject")
        first = core.get_adjacent_spaces("subject", "link-instance-a")
        second = core.get_adjacent_spaces("subject", "link-instance-b")
        context = core.get_spatial_context("space", "subject")
        assert first == core.get_adjacent_spaces("subject", "link-instance-a")
    assert ambiguous["status"] == "ambiguous_occurrence"
    assert context["adjacency"] == ambiguous
    assert first["adjacent_spaces"][0]["occurrence"]["link_instance_id"] == "link-instance-a"
    assert second["adjacent_spaces"][0]["occurrence"]["link_instance_id"] == "link-instance-b"
    assert first != second
