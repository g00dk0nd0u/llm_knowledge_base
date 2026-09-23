"""Deterministic Phase 3B table characterization (never used by production)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import fitz

from .pipeline import (
    TABLE_EPSILON,
    _bbox,
    _contains,
    _evaluate_table_candidates,
    _geometry_key,
    _intersects,
)

GAPS = (1, 2, 3, 4, 6, 9, 12)
JOIN_TOLERANCES = (1, 2, 3, 4, 6, 9, 12)


@dataclass(frozen=True)
class Profile:
    name: str
    parameters: dict[str, Any]


PROFILES = (
    Profile("P0", {"strategy": "lines_strict", "use_layout": False}),
    *(Profile(f"P1-join-{value}", {"strategy": "lines_strict", "use_layout": False,
                                    "join_tolerance": value}) for value in JOIN_TOLERANCES),
    Profile("P2", {"vertical_strategy": "lines_strict", "horizontal_strategy": "text",
                   "min_words_horizontal": 2, "use_layout": False}),
    Profile("P3", {"vertical_strategy": "text", "horizontal_strategy": "lines_strict",
                   "min_words_vertical": 2, "use_layout": False}),
)


def _segments(start: float, end: float, gaps: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    result, cursor = [], start
    for gap_start, gap_end in sorted(gaps):
        if cursor < gap_start:
            result.append((cursor, gap_start))
        cursor = gap_end
    if cursor < end:
        result.append((cursor, end))
    return result


def _grid(page: fitz.Page, *, rows: int = 3, columns: int = 3,
          omit_h: set[int] | None = None, omit_v: set[int] | None = None,
          h_gaps: dict[int, list[tuple[float, float]]] | None = None,
          v_gaps: dict[int, list[tuple[float, float]]] | None = None,
          text: bool = True, empty_cells: set[tuple[int, int]] | None = None,
          values: dict[tuple[int, int], str] | None = None,
          left: float = 50.0, top: float = 50.0,
          cell_width: float = 90.0, cell_height: float = 36.0,
          separate_paths: bool = False) -> None:
    width, height = cell_width, cell_height
    omit_h, omit_v, h_gaps, v_gaps = omit_h or set(), omit_v or set(), h_gaps or {}, v_gaps or {}
    lines: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for row in range(rows + 1):
        if row in omit_h:
            continue
        y = top + row * height
        for x0, x1 in _segments(left, left + columns * width, h_gaps.get(row, [])):
            lines.append(((x0, y), (x1, y)))
    for column in range(columns + 1):
        if column in omit_v:
            continue
        x = left + column * width
        for y0, y1 in _segments(top, top + rows * height, v_gaps.get(column, [])):
            lines.append(((x, y0), (x, y1)))
    if separate_paths:
        for start, end in lines:
            page.draw_line(start, end)
    else:
        shape = page.new_shape()
        for start, end in lines:
            shape.draw_line(start, end)
        shape.finish(); shape.commit()
    empty_cells, values = empty_cells or set(), values or {}
    if text:
        for row in range(rows):
            for column in range(columns):
                if (row, column) in empty_cells:
                    continue
                value = values.get((row, column), f"R{row + 1}C{column + 1}")
                font = "japan" if any(ord(char) > 127 for char in value) else "helv"
                for line_number, line in enumerate(value.splitlines()):
                    page.insert_text((left + column * width + 5,
                                      top + row * height + 14 + line_number * 10), line,
                                     fontname=font, fontsize=8)


def generate_fixture(path: Path, name: str) -> None:
    """Generate one born-digital fixture. The name completely defines its geometry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    large = name in {"large_schedule", "fragmented_large_schedule"}
    page = doc.new_page(width=1191 if large else 595, height=842)
    if name == "aligned_prose":
        for row in range(3):
            page.insert_text((50, 70 + row * 36), f"alpha          beta          gamma {row}")
    elif name == "background_rectangles":
        for row in range(3):
            page.draw_rect((45, 50 + row * 40, 330, 82 + row * 40), fill=(.9, .9, .9), color=None)
            page.insert_text((55, 70 + row * 40), f"background paragraph {row}")
    elif name == "unrelated_rectangles":
        for rect in ((40, 40, 100, 90), (180, 130, 250, 210), (350, 50, 430, 100)):
            page.draw_rect(rect)
    elif name == "floor_plan":
        shape = page.new_shape()
        for points in (((30, 30), (400, 30)), ((30, 30), (30, 350)), ((30, 180), (170, 180)),
                       ((210, 180), (400, 180)), ((200, 30), (200, 130)), ((200, 230), (200, 350))):
            shape.draw_line(*points)
        shape.finish(); shape.commit()
    elif name == "sparse_diagram":
        page.draw_rect((50, 50, 130, 100)); page.draw_rect((260, 160, 340, 210))
        page.draw_line((130, 75), (260, 185))
    elif large:
        _grid(page, rows=20, columns=12, separate_paths=name == "fragmented_large_schedule")
    else:
        kwargs: dict[str, Any] = {}
        prebuilt = False
        if name.startswith("side_by_side_"):
            gap = float(name.rsplit("_", 1)[1])
            _grid(page, rows=2, columns=2, left=30, top=50, cell_width=55)
            _grid(page, rows=2, columns=2, left=140 + gap, top=50, cell_width=55)
            prebuilt = True
        elif name.startswith("stacked_"):
            gap = float(name.rsplit("_", 1)[1])
            _grid(page, rows=2, columns=2, left=50, top=40, cell_width=70, cell_height=36)
            _grid(page, rows=2, columns=2, left=50, top=112 + gap, cell_width=70, cell_height=36)
            prebuilt = True
        elif name.startswith("adjacent_linework_"):
            gap = float(name.rsplit("_", 1)[1])
            _grid(page, rows=3, columns=3, left=50, top=50)
            # Collinear segments outside the right border challenge geometry joining.
            for row in range(4):
                y = 50 + row * 36
                page.draw_line((320 + gap, y), (380 + gap, y))
            prebuilt = True
        elif name == "broken_beside_intact":
            _grid(page, rows=3, columns=3, left=30, top=50, cell_width=45,
                  h_gaps={1: [(95.5, 99.5)]})
            _grid(page, rows=3, columns=3, left=190, top=50, cell_width=45)
            prebuilt = True
        elif name == "two_tables_broken_separator":
            _grid(page, rows=3, columns=3, left=30, top=50, cell_width=45,
                  h_gaps={1: [(95.5, 99.5)]})
            _grid(page, rows=3, columns=3, left=169, top=50, cell_width=45)
            prebuilt = True
        elif name == "nested_linework":
            _grid(page, rows=3, columns=3, left=50, top=50)
            page.draw_rect((70, 64, 105, 76))
            page.draw_rect((45, 45, 325, 163))
            prebuilt = True
        elif name == "double_line_border":
            _grid(page, rows=3, columns=3, left=50, top=50)
            page.draw_line((51.5, 50), (51.5, 158))
            page.draw_line((318.5, 50), (318.5, 158))
            prebuilt = True
        elif name == "multiple_gaps_one_separator":
            kwargs["h_gaps"] = {1: [(95, 99), (181, 185)]}
        elif name == "gaps_two_separators":
            kwargs["h_gaps"] = {1: [(181, 185)]}
            kwargs["v_gaps"] = {1: [(102, 106)]}
        elif name == "mixed_page":
            _grid(page, rows=3, columns=3, left=30, top=40, cell_width=45)
            _grid(page, rows=3, columns=3, left=230, top=40, cell_width=45,
                  h_gaps={1: [(295.5, 299.5)]})
            page.insert_text((30, 220), "Prose outside table geometry.")
            page.draw_rect((400, 190, 480, 250))
            page.draw_line((400, 220), (480, 220))
            prebuilt = True
        elif name.startswith("horizontal_gap_"):
            gap = float(name.rsplit("_", 1)[1]); mid = 185.0
            kwargs["h_gaps"] = {1: [(mid - gap / 2, mid + gap / 2)]}
        elif name.startswith("vertical_gap_"):
            gap = float(name.rsplit("_", 1)[1]); mid = 104.0
            kwargs["v_gaps"] = {1: [(mid - gap / 2, mid + gap / 2)]}
        elif name == "multiple_gaps":
            kwargs["h_gaps"] = {1: [(100, 102), (180, 182), (260, 262)]}
        elif name == "missing_internal_horizontal": kwargs["omit_h"] = {1}
        elif name == "missing_internal_vertical": kwargs["omit_v"] = {1}
        elif name == "missing_top": kwargs["omit_h"] = {0}
        elif name == "missing_left": kwargs["omit_v"] = {0}
        elif name == "vertical_only": kwargs["omit_h"] = set(range(4))
        elif name == "horizontal_only": kwargs["omit_v"] = set(range(4))
        elif name == "merged_horizontal": kwargs["v_gaps"] = {1: [(50, 86)]}
        elif name == "merged_vertical": kwargs["h_gaps"] = {1: [(50, 140)]}
        elif name == "empty_multiline_japanese":
            kwargs["empty_cells"] = {(0, 2)}
            kwargs["values"] = {(0, 1): "一行目\n二行目"}
        elif name == "rotated_partial":
            kwargs["h_gaps"] = {1: [(183, 187)]}
        if not prebuilt:
            _grid(page, **kwargs)
        if name == "rotated_partial":
            page.set_rotation(90)
    doc.save(path); doc.close()


def fixture_names() -> tuple[str, ...]:
    return ("fully_ruled", *(f"horizontal_gap_{g}" for g in GAPS),
            *(f"vertical_gap_{g}" for g in GAPS), "multiple_gaps",
            "missing_internal_horizontal", "missing_internal_vertical", "missing_top", "missing_left",
            "vertical_only", "horizontal_only", "merged_horizontal", "merged_vertical",
            *(f"side_by_side_{g}" for g in GAPS), *(f"stacked_{g}" for g in GAPS),
            *(f"adjacent_linework_{g}" for g in GAPS),
            "broken_beside_intact", "two_tables_broken_separator", "nested_linework",
            "double_line_border", "multiple_gaps_one_separator", "gaps_two_separators",
            "mixed_page",
            "empty_multiline_japanese", "rotated_partial", "large_schedule", "fragmented_large_schedule", "aligned_prose",
            "background_rectangles", "unrelated_rectangles", "floor_plan", "sparse_diagram")


def _production_blocks(page: fitz.Page) -> list[dict[str, Any]]:
    blocks = []
    for source_block in page.get_text("dict", sort=True).get("blocks", []):
        if source_block.get("type") != 0:
            continue
        lines = []
        for line_index, source_line in enumerate(source_block.get("lines", [])):
            spans = [{"order_index": span_index, "text": source_span.get("text", ""),
                      "bbox": _bbox(source_span["bbox"])}
                     for span_index, source_span in enumerate(source_line.get("spans", []))]
            lines.append({"order_index": line_index, "spans": spans})
        blocks.append({"order_index": len(blocks), "lines": lines})
    return blocks


def _characterize_page(page: fitz.Page, fixture: str, profile: Profile,
                       drawings: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    kwargs = dict(profile.parameters); kwargs["paths"] = drawings
    candidates = sorted(page.find_tables(**kwargs).tables, key=lambda item: _geometry_key(item.bbox))
    accepted, reasons, evaluations = _evaluate_table_candidates(candidates, blocks)
    spans = [span for block in blocks for line in block["lines"] for span in line["spans"] if span["text"]]
    tables = []
    for evaluation in evaluations:
        candidate = evaluation["candidate"]
        raw = evaluation["raw_cells"]
        linked = [span for span in spans if _intersects(candidate.bbox, span["bbox"])]
        tables.append({"bbox": list(candidate.bbox), "row_count": candidate.row_count,
                       "column_count": candidate.col_count, "total_cell_slots": len(raw),
                       "missing_cell_count": sum(cell is None for cell in raw),
                       "cell_bboxes": [None if cell is None else list(cell) for cell in raw],
                       "phase_3a_accepted": evaluation["accepted"],
                       "rejection_reason": evaluation["rejection_reason"],
                       "source_spans_map_uniquely": evaluation["accepted"],
                       "linked_source_span_count": len(linked),
                       "extracted_text": candidate.extract()})
    return {"fixture": fixture, "profile": profile.name, "parameters": profile.parameters,
            "drawing_path_count": len(drawings), "candidate_count": len(candidates),
            "table_count": len(tables), "accepted_count": len(accepted),
            "rejection_counts": dict(sorted(reasons.items())), "tables": tables,
            "accepted_tables": [{key: table[key] for key in ("bbox", "row_count", "column_count")}
                                for table in accepted],
            "coordinate_space": "pdf_points_top_left"}


def characterize(path: Path, fixture: str, profile: Profile) -> dict[str, Any]:
    with fitz.open(path) as doc:
        page = doc[0]; rotation = page.rotation
        if rotation: page.set_rotation(0)
        return _characterize_page(page, fixture, profile, page.get_drawings(), _production_blocks(page))


def _same_bbox(first: Any, second: Any) -> bool:
    return all(abs(float(a) - float(b)) <= TABLE_EPSILON for a, b in zip(first, second, strict=True))


def _missing_count(evaluation: dict[str, Any]) -> int:
    return sum(cell is None for cell in evaluation["raw_cells"])


def _missing_coordinates(evaluation: dict[str, Any]) -> list[list[int]]:
    columns = evaluation["candidate"].col_count
    return [[index // columns, index % columns]
            for index, cell in enumerate(evaluation["raw_cells"]) if cell is None]


def _source_evidence(candidate: Any, spans: list[tuple[int, int, int, dict[str, Any]]]) -> tuple[
    list[list[int]], dict[tuple[int, int], list[list[int]]]
]:
    """Return ordered source identities for the table and each observable cell."""
    table_refs = [[bi, li, si] for bi, li, si, span in spans
                  if _intersects(candidate.bbox, span["bbox"])]
    assignments: dict[tuple[int, int], list[list[int]]] = {}
    for row_index, row in enumerate(candidate.rows):
        for column_index, cell in enumerate(row.cells):
            if cell is not None:
                assignments[(row_index, column_index)] = [
                    [bi, li, si] for bi, li, si, span in spans
                    if _intersects(candidate.bbox, span["bbox"])
                    and _contains(cell, span["bbox"])
                ]
    return table_refs, assignments


def _guard_decision(
    widened: dict[str, Any], baseline: list[dict[str, Any]], profile: str,
    baseline_candidate_count: int, widened_candidate_count: int,
    spans: list[tuple[int, int, int, dict[str, Any]]],
) -> dict[str, Any]:
    """Match by bbox intersection and characterize one widened candidate conservatively.

    Intersection is deterministic pre-persistence geometry. Zero or multiple intersecting
    P0 candidates is deliberately ambiguous and can never be repaired.
    """
    candidate = widened["candidate"]
    anchors = [item for item in baseline if _intersects(candidate.bbox, item["candidate"].bbox)]
    anchor = anchors[0] if len(anchors) == 1 else None
    widened_missing = _missing_coordinates(widened)
    result = {
        "profile": profile,
        "baseline_candidate_count": baseline_candidate_count,
        "widened_candidate_count": widened_candidate_count,
        "baseline_anchor_count": len(anchors),
        "baseline_bbox": None if anchor is None else list(anchor["candidate"].bbox),
        "widened_bbox": list(candidate.bbox),
        "baseline_row_count": None if anchor is None else anchor["candidate"].row_count,
        "baseline_column_count": None if anchor is None else anchor["candidate"].col_count,
        "widened_row_count": candidate.row_count,
        "widened_column_count": candidate.col_count,
        "baseline_missing_cell_count": None if anchor is None else _missing_count(anchor),
        "widened_missing_cell_count": _missing_count(widened),
        "baseline_missing_cell_coordinates": None if anchor is None else _missing_coordinates(anchor),
        "widened_missing_cell_coordinates": widened_missing,
        "repaired_cell_coordinates": [],
        "baseline_table_source_refs": [],
        "widened_table_source_refs": [],
        "preserved_source_refs": [],
        "newly_assigned_source_refs": [],
        "table_source_span_set_unchanged": False,
        "existing_cell_geometry_unchanged": False,
        "preserved_source_assignments_unchanged": False,
        "source_span_evidence_unchanged": False,
        "guard_eligible": False,
        "guard_reason": "multiple_baseline_anchors" if len(anchors) > 1 else "no_baseline_anchor",
    }
    if anchor is None:
        return result
    baseline_candidate = anchor["candidate"]
    baseline_missing = _missing_coordinates(anchor)
    result["repaired_cell_coordinates"] = [coordinate for coordinate in baseline_missing
                                            if coordinate not in widened_missing]
    baseline_table_refs, baseline_assignments = _source_evidence(baseline_candidate, spans)
    widened_table_refs, widened_assignments = _source_evidence(candidate, spans)
    result["baseline_table_source_refs"] = baseline_table_refs
    result["widened_table_source_refs"] = widened_table_refs
    result["table_source_span_set_unchanged"] = baseline_table_refs == widened_table_refs
    preserved_coordinates = sorted(baseline_assignments)
    geometry_unchanged = all(
        coordinate in widened_assignments
        and _same_bbox(baseline_candidate.rows[coordinate[0]].cells[coordinate[1]],
                       candidate.rows[coordinate[0]].cells[coordinate[1]])
        for coordinate in preserved_coordinates
    )
    result["existing_cell_geometry_unchanged"] = geometry_unchanged
    assignments_unchanged = all(
        baseline_assignments[coordinate] == widened_assignments.get(coordinate)
        for coordinate in preserved_coordinates
    )
    result["preserved_source_assignments_unchanged"] = assignments_unchanged
    result["preserved_source_refs"] = [
        ref for coordinate in preserved_coordinates for ref in baseline_assignments[coordinate]
    ]
    repaired_coordinates = {tuple(coordinate) for coordinate in result["repaired_cell_coordinates"]}
    result["newly_assigned_source_refs"] = [
        ref for coordinate in sorted(repaired_coordinates)
        for ref in widened_assignments.get(coordinate, [])
        if ref not in result["preserved_source_refs"]
    ]
    evidence_unchanged = (
        result["table_source_span_set_unchanged"]
        and geometry_unchanged
        and assignments_unchanged
        and all(ref not in result["preserved_source_refs"]
                for coordinate in repaired_coordinates
                for ref in widened_assignments.get(coordinate, []))
    )
    result["source_span_evidence_unchanged"] = evidence_unchanged
    if not _same_bbox(baseline_candidate.bbox, candidate.bbox):
        result["guard_reason"] = "outer_bbox_changed"
    elif (baseline_candidate.row_count, baseline_candidate.col_count) != (candidate.row_count, candidate.col_count):
        result["guard_reason"] = "dimensions_changed"
    elif anchor["accepted"]:
        result["guard_reason"] = "baseline_already_accepted"
    elif anchor["rejection_reason"] != "merged_or_missing_cell":
        result["guard_reason"] = f"baseline_not_repairable_{anchor['rejection_reason']}"
    elif _missing_count(anchor) == 0:
        result["guard_reason"] = "baseline_has_no_missing_cells"
    elif not widened["accepted"]:
        result["guard_reason"] = f"widened_not_accepted_{widened['rejection_reason']}"
    elif _missing_count(widened) != 0:
        result["guard_reason"] = "widened_still_has_missing_cells"
    elif not geometry_unchanged:
        result["guard_reason"] = "existing_cell_geometry_changed"
    elif result["repaired_cell_coordinates"] != baseline_missing:
        result["guard_reason"] = "repaired_cell_coordinates_changed"
    elif not result["table_source_span_set_unchanged"]:
        result["guard_reason"] = "table_source_span_set_changed"
    elif not assignments_unchanged or not evidence_unchanged:
        result["guard_reason"] = "preserved_source_assignment_changed"
    else:
        result["guard_eligible"] = True
        result["guard_reason"] = "eligible_monotonic_completion"
    return result


def evaluate_guarded_repair(path: Path, fixture: str, join_tolerance: int) -> dict[str, Any]:
    """Evaluate, but never production-enable, a P0-anchored widened profile."""
    with fitz.open(path) as doc:
        page = doc[0]
        if page.rotation:
            page.set_rotation(0)
        drawings, blocks = page.get_drawings(), _production_blocks(page)
        baseline_candidates = sorted(page.find_tables(strategy="lines_strict", use_layout=False,
                                                       paths=drawings).tables,
                                     key=lambda item: _geometry_key(item.bbox))
        widened_candidates = sorted(page.find_tables(strategy="lines_strict", use_layout=False,
                                                      join_tolerance=join_tolerance,
                                                      paths=drawings).tables,
                                    key=lambda item: _geometry_key(item.bbox))
        baseline_evaluations = _evaluate_table_candidates(baseline_candidates, blocks)[2]
        widened_evaluations = _evaluate_table_candidates(widened_candidates, blocks)[2]
        spans = [
            (block["order_index"], line["order_index"], span["order_index"], span)
            for block in blocks for line in block["lines"] for span in line["spans"]
            if span["text"]
        ]
        profile = f"P1-join-{join_tolerance}"
        return {
            "fixture": fixture,
            "profile": profile,
            "baseline_candidate_count": len(baseline_candidates),
            "widened_candidate_count": len(widened_candidates),
            "decisions": [_guard_decision(item, baseline_evaluations, profile,
                                           len(baseline_candidates), len(widened_candidates), spans)
                          for item in widened_evaluations],
        }


def run_corpus(directory: Path) -> list[dict[str, Any]]:
    observations = []
    for name in fixture_names():
        path = directory / f"{name}.pdf"; generate_fixture(path, name)
        with fitz.open(path) as doc:
            page = doc[0]
            if page.rotation: page.set_rotation(0)
            drawings = page.get_drawings()
            blocks = _production_blocks(page)
            observations.extend(_characterize_page(page, name, profile, drawings, blocks)
                                for profile in PROFILES)
    return observations


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
