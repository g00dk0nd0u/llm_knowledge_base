"""Deterministic Phase 3B table characterization (never used by production)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import fitz

from .pipeline import _contains, _intersects, _valid_rect

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
          values: dict[tuple[int, int], str] | None = None) -> None:
    left, top = 50.0, 50.0
    width, height = 90.0, 36.0
    omit_h, omit_v, h_gaps, v_gaps = omit_h or set(), omit_v or set(), h_gaps or {}, v_gaps or {}
    shape = page.new_shape()
    for row in range(rows + 1):
        if row in omit_h:
            continue
        y = top + row * height
        for x0, x1 in _segments(left, left + columns * width, h_gaps.get(row, [])):
            shape.draw_line((x0, y), (x1, y))
    for column in range(columns + 1):
        if column in omit_v:
            continue
        x = left + column * width
        for y0, y1 in _segments(top, top + rows * height, v_gaps.get(column, [])):
            shape.draw_line((x, y0), (x, y1))
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
    large = name == "large_schedule"
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
        _grid(page, rows=20, columns=12)
    else:
        kwargs: dict[str, Any] = {}
        if name.startswith("horizontal_gap_"):
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
        _grid(page, **kwargs)
        if name == "rotated_partial":
            page.set_rotation(90)
    doc.save(path); doc.close()


def fixture_names() -> tuple[str, ...]:
    return ("fully_ruled", *(f"horizontal_gap_{g}" for g in GAPS),
            *(f"vertical_gap_{g}" for g in GAPS), "multiple_gaps",
            "missing_internal_horizontal", "missing_internal_vertical", "missing_top", "missing_left",
            "vertical_only", "horizontal_only", "merged_horizontal", "merged_vertical",
            "empty_multiline_japanese", "rotated_partial", "large_schedule", "aligned_prose",
            "background_rectangles", "unrelated_rectangles", "floor_plan", "sparse_diagram")


def characterize(path: Path, fixture: str, profile: Profile) -> dict[str, Any]:
    with fitz.open(path) as doc:
        page = doc[0]; rotation = page.rotation
        if rotation: page.set_rotation(0)
        drawings = page.get_drawings()
        kwargs = dict(profile.parameters); kwargs["paths"] = drawings
        candidates = page.find_tables(**kwargs).tables
        spans = [s for b in page.get_text("dict").get("blocks", []) if b.get("type") == 0
                 for line in b.get("lines", []) for s in line.get("spans", []) if s.get("text")]
        tables = []
        for candidate in candidates:
            raw = [cell for row in candidate.rows for cell in row.cells]
            linked_spans = [span for span in spans if _intersects(candidate.bbox, span["bbox"])]
            reason = None
            if candidate.row_count < 2 or candidate.col_count < 2: reason = "insufficient_dimensions"
            elif any(cell is None for cell in raw): reason = "merged_or_missing_cell"
            elif not _valid_rect(candidate.bbox) or any(not _valid_rect(cell) for cell in raw): reason = "invalid_shape"
            unique = reason is None
            if unique:
                for span in linked_spans:
                    if sum(_contains(cell, span["bbox"]) for cell in raw) != 1:
                        unique = False; reason = "ambiguous_span_mapping"; break
            tables.append({"bbox": list(candidate.bbox), "row_count": candidate.row_count,
                           "column_count": candidate.col_count, "total_cell_slots": len(raw),
                           "missing_cell_count": sum(cell is None for cell in raw),
                           "cell_bboxes": [None if cell is None else list(cell) for cell in raw],
                           "phase_3a_accepted": reason is None, "rejection_reason": reason,
                           "source_spans_map_uniquely": unique,
                           "linked_source_span_count": len(linked_spans),
                           "extracted_text": candidate.extract()})
        return {"fixture": fixture, "profile": profile.name, "parameters": profile.parameters,
                "drawing_path_count": len(drawings), "candidate_count": len(candidates),
                "table_count": len(tables), "tables": tables,
                "coordinate_space": "pdf_points_top_left"}


def run_corpus(directory: Path) -> list[dict[str, Any]]:
    observations = []
    for name in fixture_names():
        path = directory / f"{name}.pdf"; generate_fixture(path, name)
        observations.extend(characterize(path, name, profile) for profile in PROFILES)
    return observations


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
