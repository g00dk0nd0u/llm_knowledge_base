"""Source-faithful, deterministic PDF knowledge generation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import fitz

from . import PIPELINE_VERSION

LOW_TEXT_THRESHOLD = 40
MANY_DRAWINGS_THRESHOLD = 200
MANAGED_MARKER = ".pdf-pipeline-v2"
LEGACY_MANAGED_MARKER = ".pdf-pipeline-v1"
MANAGED_MARKERS = {"1": LEGACY_MANAGED_MARKER, "2": MANAGED_MARKER}
TABLE_EPSILON = 0.25
RENDER_ROOTS = frozenset({".tmp", ".cache", "artifacts", "vision", "renders", "tiles"})


class PipelineError(RuntimeError):
    """An actionable pipeline failure."""


@dataclass(frozen=True)
class ProcessResult:
    processed: int = 0
    unchanged: int = 0
    removed: int = 0


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _relative_source(root: Path, pdf: Path) -> str:
    return pdf.resolve().relative_to(root.resolve()).as_posix()


def document_id(source_file: str) -> str:
    """Return a stable identity based only on normalized repository-relative path."""
    normalized = PurePosixPath(source_file).as_posix()
    return "doc-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(pdf: Path, doc_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", pdf.stem).strip("-.") or "document"
    return f"{safe}--{doc_id[4:16]}"


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip()


def _status(char_count: int) -> str:
    if char_count == 0:
        return "no_text"
    if char_count < LOW_TEXT_THRESHOLD:
        return "minimal_text"
    return "extracted"


def _frontmatter(values: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in values.items():
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines) + "\n---\n"


def _metadata(document: fitz.Document) -> dict[str, str]:
    # PyMuPDF exposes standard embedded fields. Empty values are deliberately omitted.
    return {
        key: value
        for key, value in sorted((document.metadata or {}).items())
        if isinstance(value, str) and value.strip()
    }


def _bbox(rect: Any) -> list[float]:
    """Return a PyMuPDF rectangle using the pipeline's JSON coordinate shape."""
    return [float(value) for value in rect]


def _page_boxes_in_unrotated_page_space(
    page: fitz.Page,
) -> tuple[list[float], list[float]]:
    """Return MediaBox and crop extent in unrotated text-bbox coordinates."""
    rotation = page.rotation
    try:
        # In PyMuPDF the page transformation matrix reflects the current page
        # rotation. Temporarily removing it yields the same unrotated space used
        # by text bboxes; restoring it leaves the source page unchanged.
        if rotation:
            page.set_rotation(0)
        media_box = _bbox(page.mediabox * page.transformation_matrix)
        # Page.cropbox already uses MuPDF's top-left convention, while page.rect
        # expresses that crop as the page-local extent used by text bboxes.
        crop_box = _bbox(page.rect)
        return media_box, crop_box
    finally:
        if rotation:
            page.set_rotation(rotation)


def _structured_page(
    page: fitz.Page,
    *,
    number: int,
    document_id: str,
    source_file: str,
    source_sha256: str,
    extraction_status: str,
    image_count: int,
    drawings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Preserve embedded text primitives without adding semantic interpretation."""
    extracted = page.get_text("dict", sort=True)
    media_box, crop_box = _page_boxes_in_unrotated_page_space(page)
    blocks: list[dict[str, Any]] = []
    for source_block in extracted.get("blocks", []):
        if source_block.get("type") != 0:
            continue
        lines: list[dict[str, Any]] = []
        for line_index, source_line in enumerate(source_block.get("lines", [])):
            spans: list[dict[str, Any]] = []
            for span_index, source_span in enumerate(source_line.get("spans", [])):
                span = {
                    "order_index": span_index,
                    "text": source_span.get("text", ""),
                    "bbox": _bbox(source_span["bbox"]),
                    "provenance": "embedded_pdf_text",
                }
                # These properties are copied only when PyMuPDF supplies them.
                if "font" in source_span:
                    span["font_name"] = source_span["font"]
                if "size" in source_span:
                    span["font_size"] = float(source_span["size"])
                if "flags" in source_span:
                    span["font_flags"] = source_span["flags"]
                spans.append(span)
            lines.append(
                {
                    "order_index": line_index,
                    "text": "".join(span["text"] for span in spans),
                    "bbox": _bbox(source_line["bbox"]),
                    "provenance": "embedded_pdf_text",
                    "spans": spans,
                }
            )
        blocks.append(
            {
                "order_index": len(blocks),
                "text": "\n".join(line["text"] for line in lines),
                "bbox": _bbox(source_block["bbox"]),
                "provenance": "embedded_pdf_text",
                "lines": lines,
            }
        )

    tables, table_extraction = _extract_tables(page, drawings, blocks)

    # get_text("dict") dimensions and text bboxes share PyMuPDF's unrotated,
    # top-left page coordinate system. Rotation is retained separately.
    return {
        "document_id": document_id,
        "source_file": source_file,
        "source_sha256": source_sha256,
        "page": number,
        "width_points": float(extracted["width"]),
        "height_points": float(extracted["height"]),
        "rotation": page.rotation,
        "media_box": media_box,
        "crop_box": crop_box,
        "coordinate_space": "pdf_points_top_left",
        "extraction_status": extraction_status,
        "image_count": image_count,
        "drawing_count": len(drawings),
        "text_blocks": blocks,
        "table_extraction": table_extraction,
        "tables": tables,
    }


def _table_metadata(status: str, candidates: int, accepted: int, reasons: dict[str, int]) -> dict[str, Any]:
    return {
        "status": status,
        "algorithm": "pymupdf_lines_strict",
        "algorithm_version": "1",
        "library": "PyMuPDF",
        "library_version": fitz.__version__,
        "candidate_count": candidates,
        "accepted_count": accepted,
        "rejected_count": candidates - accepted,
        "rejection_counts": dict(sorted(reasons.items())),
    }


def _valid_rect(value: Any) -> bool:
    return (
        isinstance(value, (tuple, list))
        and len(value) == 4
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)
        and value[2] - value[0] > TABLE_EPSILON
        and value[3] - value[1] > TABLE_EPSILON
    )


def _contains(outer: Any, inner: Any) -> bool:
    return all((inner[i] >= outer[i] - TABLE_EPSILON if i < 2 else inner[i] <= outer[i] + TABLE_EPSILON) for i in range(4))


def _intersects(a: Any, b: Any) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > TABLE_EPSILON and min(a[3], b[3]) - max(a[1], b[1]) > TABLE_EPSILON


def _geometry_key(bbox: Any) -> tuple[float, float, float, float]:
    """Sort page geometry in reading order with complete bbox tie-breaks."""
    return (bbox[1], bbox[0], bbox[3], bbox[2])


def _evaluate_table_candidates(
    candidates: list[Any], blocks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Apply the Phase 3A contract to already deterministically ordered candidates."""
    accepted: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    evaluations: list[dict[str, Any]] = []
    spans = [
        (block["order_index"], line["order_index"], span["order_index"], span)
        for block in blocks for line in block["lines"] for span in line["spans"]
        if span["text"]
    ]
    for candidate in candidates:
        reason = None
        bbox = candidate.bbox
        rows = candidate.rows
        raw_cells: list[Any] = []
        if candidate.row_count < 2 or candidate.col_count < 2:
            reason = "insufficient_dimensions"
        elif not _valid_rect(bbox) or len(rows) != candidate.row_count or any(len(row.cells) != candidate.col_count for row in rows):
            reason = "invalid_shape"
        else:
            raw_cells = [cell for row in rows for cell in row.cells]
            if any(cell is None for cell in raw_cells):
                reason = "merged_or_missing_cell"
            elif any(not _valid_rect(cell) or not _contains(bbox, cell) for cell in raw_cells):
                reason = "invalid_cell_geometry"
        if reason is None and any(_intersects(bbox, table["bbox"]) for table in accepted):
            reason = "overlapping_candidate"
        cells: list[dict[str, Any]] = []
        linked_span_count = 0
        if reason is None:
            row_major_cells = [
                (row_index, column_index, cell_bbox)
                for row_index, row in enumerate(rows)
                for column_index, cell_bbox in enumerate(row.cells)
            ]
            assignments: list[list[tuple[int, int, int, str]]] = [
                [] for _cell in row_major_cells
            ]
            for block_index, line_index, span_index, span in spans:
                span_bbox = span["bbox"]
                if not _intersects(bbox, span_bbox):
                    continue
                linked_span_count += 1
                containing = [
                    index
                    for index, (_row, _column, cell_bbox) in enumerate(row_major_cells)
                    if _contains(cell_bbox, span_bbox)
                ]
                if len(containing) != 1:
                    reason = "ambiguous_span_mapping"
                    break
                assignments[containing[0]].append(
                    (block_index, line_index, span_index, span["text"])
                )
            if reason is None:
                for (row_index, column_index, cell_bbox), assigned in zip(
                    row_major_cells, assignments, strict=True
                ):
                    line_groups: list[list[str]] = []
                    previous_line = None
                    refs = []
                    for bi, li, si, text in assigned:
                        key = (bi, li)
                        if key != previous_line:
                            line_groups.append([])
                            previous_line = key
                        line_groups[-1].append(text)
                        refs.append({"block_index": bi, "line_index": li, "span_index": si})
                    cells.append({"row_index": row_index, "column_index": column_index, "row_span": 1, "column_span": 1, "bbox": _bbox(cell_bbox), "text": "\n".join("".join(group) for group in line_groups), "span_refs": refs})
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            accepted.append({"order_index": 0, "bbox": _bbox(bbox), "row_count": candidate.row_count, "column_count": candidate.col_count, "provenance": "derived_pdf_table", "cells": cells})
        evaluations.append({
            "candidate": candidate,
            "raw_cells": raw_cells,
            "accepted": reason is None,
            "rejection_reason": reason,
            "linked_source_span_count": linked_span_count,
        })
    accepted.sort(key=lambda table: _geometry_key(table["bbox"]))
    for index, table in enumerate(accepted):
        table["order_index"] = index
    return accepted, reasons, evaluations


def _extract_tables(page: fitz.Page, drawings: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Persist only complete ruled grids; text always comes from existing spans."""
    usable = any(any(item and item[0] in {"l", "re"} for item in path.get("items", [])) for path in drawings)
    if not usable:
        return [], _table_metadata("no_candidates", 0, 0, {})
    try:
        rotation = page.rotation
        try:
            # Text primitives, drawings, and persisted geometry use unrotated page
            # coordinates. find_tables() must run in that same space on rotated pages.
            if rotation:
                page.set_rotation(0)
            candidates = sorted(
                page.find_tables(strategy="lines_strict", use_layout=False, paths=drawings).tables,
                key=lambda candidate: _geometry_key(candidate.bbox),
            )
        finally:
            if rotation:
                page.set_rotation(rotation)
        accepted, reasons, _evaluations = _evaluate_table_candidates(candidates, blocks)
        status = "completed" if candidates else "no_candidates"
        return accepted, _table_metadata(status, len(candidates), len(accepted), reasons)
    except Exception:
        return [], _table_metadata("extraction_error", 0, 0, {"extraction_error": 1})


def _build_document(root: Path, project: Path, pdf: Path, destination: Path) -> dict[str, Any]:
    source_file = _relative_source(root, pdf)
    source_hash = _sha256(pdf)
    doc_id = document_id(source_file)
    try:
        document = fitz.open(pdf)
    except Exception as exc:
        raise PipelineError(f"cannot open corrupt or invalid PDF {source_file}: {exc}") from exc
    try:
        if document.needs_pass:
            raise PipelineError(f"password-protected PDF is unsupported: {source_file}")
        page_count = document.page_count
        if page_count < 1:
            raise PipelineError(f"PDF has no pages: {source_file}")
        pages: list[dict[str, Any]] = []
        page_dir = destination / "pages"
        page_dir.mkdir(parents=True)
        project_id = project.name
        for number, page in enumerate(document, start=1):
            text = _normalize_text(page.get_text("text", sort=True))
            char_count = len(text)
            image_count = len(page.get_images(full=True))
            try:
                drawings = page.get_drawings()
            except Exception:
                drawings = []
            drawing_count = len(drawings)
            status = _status(char_count)
            vision = (
                char_count < LOW_TEXT_THRESHOLD
                or image_count > 0
                or drawing_count >= MANY_DRAWINGS_THRESHOLD
            )
            structured = _structured_page(
                page,
                number=number,
                document_id=doc_id,
                source_file=source_file,
                source_sha256=source_hash,
                extraction_status=status,
                image_count=image_count,
                drawings=drawings,
            )
            page_data = {
                "page": number,
                "width_points": structured["width_points"],
                "height_points": structured["height_points"],
                "rotation": structured["rotation"],
                "media_box": structured["media_box"],
                "crop_box": structured["crop_box"],
                "coordinate_space": structured["coordinate_space"],
                "structured_page": f"pages/p{number:04d}.json",
                "extraction_status": status,
                "text_char_count": char_count,
                "image_count": image_count,
                "drawing_count": drawing_count,
                "vision_recommended": vision,
            }
            pages.append(page_data)
            (page_dir / f"p{number:04d}.json").write_text(
                json.dumps(structured, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            frontmatter = {
                "document_id": doc_id,
                "source_file": source_file,
                "source_sha256": source_hash,
                "project_id": project_id,
                "page": number,
                "page_count": page_count,
                **page_data,
            }
            body = text if text else "<!-- No embedded text was extracted from this page. -->"
            (page_dir / f"p{number:04d}.md").write_text(
                _frontmatter(frontmatter) + "\n" + body + "\n", encoding="utf-8"
            )
        data = {
            "document_id": doc_id,
            "source_file": source_file,
            "source_sha256": source_hash,
            "project_id": project_id,
            "page_count": page_count,
            "pipeline_version": PIPELINE_VERSION,
            "pdf_metadata": _metadata(document),
            "pages": pages,
        }
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"failed to extract {source_file}: {exc}") from exc
    finally:
        document.close()

    (destination / "document.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    index_lines = [
        f"# {pdf.name}",
        "",
        "> Generated retrieval layer only. The source PDF and cited page are authoritative.",
        "> `vision_recommended` is a processing hint, not a legal or semantic judgment.",
        "",
        f"- Document ID: `{doc_id}`",
        f"- Source: `{source_file}`",
        f"- SHA-256: `{source_hash}`",
        f"- Pages: {page_count}",
        "",
        "## Pages",
        "",
    ]
    index_lines.extend(
        f"- [Page {p['page']}](pages/p{p['page']:04d}.md) — {p['extraction_status']}, "
        f"{p['text_char_count']} chars, vision: {str(p['vision_recommended']).lower()}"
        for p in pages
    )
    (destination / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    (destination / MANAGED_MARKER).write_text("managed by pdf pipeline v2\n", encoding="utf-8")
    return data


def _load_manifest(path: Path, project_id: str) -> dict[str, Any]:
    if not path.exists():
        return {"project_id": project_id, "documents": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"invalid manifest {path}: {exc}") from exc
    if data.get("project_id") != project_id or not isinstance(data.get("documents"), list):
        raise PipelineError(f"manifest has invalid project identity or documents list: {path}")
    return data


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_v2_table_contract(structured: dict[str, Any]) -> bool:
    """Validate cached v2 table data and every reference to source text evidence."""
    extraction = structured.get("table_extraction")
    tables = structured.get("tables")
    required_extraction = {
        "status",
        "algorithm",
        "algorithm_version",
        "library",
        "library_version",
        "candidate_count",
        "accepted_count",
        "rejected_count",
        "rejection_counts",
    }
    if (
        not isinstance(extraction, dict)
        or set(extraction) != required_extraction
        or extraction.get("status") not in {"completed", "no_candidates", "extraction_error"}
        or extraction.get("algorithm") != "pymupdf_lines_strict"
        or extraction.get("algorithm_version") != "1"
        or extraction.get("library") != "PyMuPDF"
        or extraction.get("library_version") != fitz.__version__
        or not all(
            _nonnegative_integer(extraction.get(key))
            for key in ("candidate_count", "accepted_count", "rejected_count")
        )
        or not isinstance(extraction.get("rejection_counts"), dict)
        or any(
            not isinstance(reason, str)
            or not reason
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 1
            for reason, count in extraction.get("rejection_counts", {}).items()
        )
        or not isinstance(tables, list)
        or extraction["candidate_count"]
        != extraction["accepted_count"] + extraction["rejected_count"]
        or extraction["accepted_count"] != len(tables)
    ):
        return False
    status = extraction["status"]
    rejection_counts = extraction["rejection_counts"]
    allowed_rejections = {
        "insufficient_dimensions",
        "invalid_shape",
        "merged_or_missing_cell",
        "invalid_cell_geometry",
        "ambiguous_span_mapping",
        "overlapping_candidate",
        "extraction_error",
    }
    if (
        not set(rejection_counts).issubset(allowed_rejections)
        or (
            status == "no_candidates"
            and (extraction["candidate_count"] != 0 or rejection_counts)
        )
        or (
            status == "completed"
            and (
                extraction["candidate_count"] < 1
                or sum(rejection_counts.values()) != extraction["rejected_count"]
            )
        )
        or (
            status == "extraction_error"
            and (
                extraction["candidate_count"] != 0
                or tables
                or rejection_counts != {"extraction_error": 1}
            )
        )
    ):
        return False

    blocks = structured.get("text_blocks")
    if not isinstance(blocks, list):
        return False
    previous_bbox: tuple[float, ...] | None = None
    for table_index, table in enumerate(tables):
        if not isinstance(table, dict):
            return False
        bbox = table.get("bbox")
        row_count = table.get("row_count")
        column_count = table.get("column_count")
        cells = table.get("cells")
        bbox_key = _geometry_key(bbox) if _valid_rect(bbox) else None
        if (
            set(table) != {
                "order_index", "bbox", "row_count", "column_count", "provenance", "cells"
            }
            or table.get("order_index") != table_index
            or bbox_key is None
            or (previous_bbox is not None and bbox_key < previous_bbox)
            or not isinstance(row_count, int)
            or isinstance(row_count, bool)
            or row_count < 2
            or not isinstance(column_count, int)
            or isinstance(column_count, bool)
            or column_count < 2
            or table.get("provenance") != "derived_pdf_table"
            or not isinstance(cells, list)
            or len(cells) != row_count * column_count
        ):
            return False
        previous_bbox = bbox_key
        for cell_index, cell in enumerate(cells):
            expected_row, expected_column = divmod(cell_index, column_count)
            if (
                not isinstance(cell, dict)
                or set(cell) != {
                    "row_index", "column_index", "row_span", "column_span",
                    "bbox", "text", "span_refs",
                }
                or cell.get("row_index") != expected_row
                or cell.get("column_index") != expected_column
                or cell.get("row_span") != 1
                or cell.get("column_span") != 1
                or not _valid_rect(cell.get("bbox"))
                or not _contains(bbox, cell["bbox"])
                or not isinstance(cell.get("text"), str)
                or not isinstance(cell.get("span_refs"), list)
            ):
                return False
            referenced: list[tuple[int, int, int, str]] = []
            for reference in cell["span_refs"]:
                if (
                    not isinstance(reference, dict)
                    or set(reference) != {"block_index", "line_index", "span_index"}
                ):
                    return False
                indices = tuple(
                    reference.get(key)
                    for key in ("block_index", "line_index", "span_index")
                )
                if not all(_nonnegative_integer(index) for index in indices):
                    return False
                block_index, line_index, span_index = indices
                try:
                    block = blocks[block_index]
                    line = block["lines"][line_index]
                    span = line["spans"][span_index]
                except (IndexError, KeyError, TypeError):
                    return False
                if (
                    block.get("order_index") != block_index
                    or line.get("order_index") != line_index
                    or span.get("order_index") != span_index
                    or not isinstance(span.get("text"), str)
                    or not span["text"]
                    or not _valid_rect(span.get("bbox"))
                    or not _contains(cell["bbox"], span["bbox"])
                ):
                    return False
                referenced.append((*indices, span["text"]))
            if referenced != sorted(referenced, key=lambda item: item[:3]):
                return False
            line_groups: list[list[str]] = []
            previous_line = None
            for block_index, line_index, _span_index, text in referenced:
                key = (block_index, line_index)
                if key != previous_line:
                    line_groups.append([])
                    previous_line = key
                line_groups[-1].append(text)
            if cell["text"] != "\n".join("".join(group) for group in line_groups):
                return False
    return True


def _generated_tree_is_valid(
    output: Path,
    *,
    document_id: str,
    source_file: str,
    source_sha256: str,
    version: str = PIPELINE_VERSION,
) -> bool:
    """Check that a pipeline-owned retrieval tree is complete and internally consistent."""
    marker = MANAGED_MARKERS.get(version)
    if marker is None or not (output / marker).is_file():
        return False
    document_path = output / "document.json"
    try:
        data = json.loads(document_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    expected = {
        "document_id": document_id,
        "source_file": source_file,
        "source_sha256": source_sha256,
        "pipeline_version": version,
    }
    if any(data.get(key) != value for key, value in expected.items()):
        return False
    page_count = data.get("page_count")
    pages = data.get("pages")
    if (
        not isinstance(page_count, int)
        or isinstance(page_count, bool)
        or page_count < 1
        or not isinstance(pages, list)
        or len(pages) != page_count
        or [page.get("page") if isinstance(page, dict) else None for page in pages]
        != list(range(1, page_count + 1))
    ):
        return False
    required = [output / "index.md"]
    for number, page in enumerate(pages, start=1):
        expected_sidecar = f"pages/p{number:04d}.json"
        if page.get("structured_page") != expected_sidecar:
            return False
        try:
            structured = json.loads((output / expected_sidecar).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if (
            not isinstance(structured, dict)
            or structured.get("document_id") != document_id
            or structured.get("source_file") != source_file
            or structured.get("source_sha256") != source_sha256
            or structured.get("page") != number
            or structured.get("coordinate_space") != "pdf_points_top_left"
            or not isinstance(structured.get("text_blocks"), list)
            or (version == "2" and not _valid_v2_table_contract(structured))
        ):
            return False
        required.extend(
            [
                output / "pages" / f"p{number:04d}.md",
                output / expected_sidecar,
            ]
        )
    try:
        return all(path.is_file() and path.stat().st_size > 0 for path in required)
    except OSError:
        return False


def _ownership_version(output: Path) -> str | None:
    """Return the sole declared owner, rejecting ambiguous marker state."""
    marker_names = sorted(
        path.name for path in output.glob(".pdf-pipeline-v*") if path.is_file()
    )
    if len(marker_names) > 1:
        raise PipelineError(f"ambiguous PDF pipeline ownership markers: {output}")
    if not marker_names:
        return None
    marker_name = marker_names[0]
    versions = [version for version, known in MANAGED_MARKERS.items() if known == marker_name]
    if not versions:
        raise PipelineError(f"unsupported PDF pipeline ownership marker {marker_name}: {output}")
    return versions[0]


def _rollback_project(
    applied: list[tuple[Path, Path | None, Path]],
    removed: list[tuple[Path, Path]],
) -> None:
    """Restore moved directories after a failed project transaction."""
    failures: list[str] = []
    for stale, backup in reversed(removed):
        try:
            if backup.exists():
                os.replace(backup, stale)
        except OSError as exc:
            failures.append(f"restore {stale}: {exc}")
    for output, backup, staged_output in reversed(applied):
        try:
            if output.exists():
                os.replace(output, staged_output)
            if backup is not None and backup.exists():
                os.replace(backup, output)
        except OSError as exc:
            failures.append(f"restore {output}: {exc}")
    if failures:
        raise PipelineError("transaction rollback failed: " + "; ".join(failures))


def process_all(root: Path) -> ProcessResult:
    projects = root / "projects"
    if not projects.is_dir():
        raise PipelineError(f"projects directory not found under repository root: {root}")
    processed = unchanged = removed = 0
    for project in sorted(p for p in projects.iterdir() if p.is_dir() and p.name != "_template"):
        source = project / "source"
        manifest_path = project / "manifest.json"
        if not source.is_dir() and not manifest_path.exists():
            continue
        manifest = _load_manifest(manifest_path, project.name)
        old_by_id = {item.get("document_id"): item for item in manifest["documents"]}
        pdfs = sorted(
            (p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
            key=lambda p: p.relative_to(source).as_posix(),
        ) if source.is_dir() else []
        staged: list[tuple[Path, Path, dict[str, Any]]] = []
        entries: list[dict[str, Any]] = []
        temp_root = Path(tempfile.mkdtemp(prefix="pdf-pipeline-", dir=project))
        try:
            for pdf in pdfs:
                source_file = _relative_source(root, pdf)
                doc_id = document_id(source_file)
                output_rel = f"projects/{project.name}/knowledge/{_slug(pdf, doc_id)}"
                output = root / output_rel
                if output.exists():
                    owner = _ownership_version(output)
                    if owner is None:
                        raise PipelineError(f"refusing to replace unmanaged knowledge directory: {output_rel}")
                    try:
                        owned_document = json.loads((output / "document.json").read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                        if owner == "1":
                            raise PipelineError(f"invalid legacy pipeline-owned knowledge directory: {output_rel}") from exc
                        owned_document = None
                    if owned_document is not None and (owned_document.get("pipeline_version") != owner or owned_document.get("document_id") != doc_id or owned_document.get("source_file") != source_file):
                        raise PipelineError(f"invalid pipeline-owned knowledge directory: {output_rel}")
                source_hash = _sha256(pdf)
                previous = old_by_id.get(doc_id)
                entry = {
                    "document_id": doc_id,
                    "source_file": source_file,
                    "source_sha256": source_hash,
                    "knowledge_path": output_rel,
                    "page_count": previous.get("page_count") if previous else None,
                    "pipeline_version": PIPELINE_VERSION,
                }
                if (
                    previous
                    and previous.get("source_sha256") == source_hash
                    and previous.get("pipeline_version") == PIPELINE_VERSION
                    and previous.get("knowledge_path") == output_rel
                    and _generated_tree_is_valid(
                        output,
                        document_id=doc_id,
                        source_file=source_file,
                        source_sha256=source_hash,
                    )
                ):
                    unchanged += 1
                else:
                    staged_output = temp_root / doc_id
                    data = _build_document(root, project, pdf, staged_output)
                    entry["page_count"] = data["page_count"]
                    staged.append((staged_output, output, data))
                    processed += 1
                entries.append(entry)

            current_paths = {entry["knowledge_path"] for entry in entries}
            stale_directories: list[Path] = []
            for previous in manifest["documents"]:
                stale_rel = previous.get("knowledge_path")
                if not isinstance(stale_rel, str) or stale_rel in current_paths:
                    continue
                stale = root / stale_rel
                expected = project / "knowledge"
                if _inside(stale, expected) and stale.exists():
                    owner = _ownership_version(stale)
                    if owner is None:
                        continue
                    try:
                        stale_document = json.loads((stale / "document.json").read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                        raise PipelineError(f"invalid pipeline-owned stale directory: {stale}") from exc
                    if stale_document.get("pipeline_version") != owner or stale_document.get("document_id") != previous.get("document_id"):
                        raise PipelineError(f"invalid pipeline-owned stale directory: {stale}")
                    stale_directories.append(stale)

            updated = {key: value for key, value in manifest.items() if key != "documents"}
            updated["project_id"] = project.name
            updated["pipeline_version"] = PIPELINE_VERSION
            updated["documents"] = sorted(entries, key=lambda item: item["source_file"])
            applied: list[tuple[Path, Path | None, Path]] = []
            removed_for_rollback: list[tuple[Path, Path]] = []
            try:
                # Every extraction and ownership check succeeded. Apply as one project
                # transaction, retaining backups until the manifest is safely replaced.
                for staged_output, output, _data in staged:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    backup = temp_root / (staged_output.name + "-old")
                    previous_output = backup if output.exists() else None
                    if previous_output is not None:
                        os.replace(output, previous_output)
                    try:
                        os.replace(staged_output, output)
                    except BaseException:
                        if previous_output is not None and previous_output.exists():
                            os.replace(previous_output, output)
                        raise
                    applied.append((output, previous_output, staged_output))

                for index, stale in enumerate(stale_directories):
                    backup = temp_root / f"stale-{index}"
                    os.replace(stale, backup)
                    removed_for_rollback.append((stale, backup))

                if updated != manifest:
                    _atomic_json(manifest_path, updated)
            except BaseException as exc:
                try:
                    _rollback_project(applied, removed_for_rollback)
                except PipelineError as rollback_exc:
                    raise rollback_exc from exc
                if isinstance(exc, PipelineError):
                    raise
                raise PipelineError(f"failed to apply project transaction {project.name}: {exc}") from exc
            removed += len(stale_directories)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
    return ProcessResult(processed, unchanged, removed)


def _parse_pages(specification: str, page_count: int) -> list[int]:
    selected: set[int] = set()
    for item in specification.split(","):
        item = item.strip()
        if not item:
            raise PipelineError("empty page selector")
        try:
            if "-" in item:
                start_text, end_text = item.split("-", 1)
                start, end = int(start_text), int(end_text)
                if start > end:
                    raise ValueError
                selected.update(range(start, end + 1))
            else:
                selected.add(int(item))
        except ValueError as exc:
            raise PipelineError(f"invalid page selector: {item}") from exc
    if not selected or min(selected) < 1 or max(selected) > page_count:
        raise PipelineError(f"page selection must be within 1-{page_count}")
    return sorted(selected)


def render_pages(
    root: Path,
    pdf: Path,
    *,
    pages: str | None,
    all_pages: bool,
    dpi: int,
    output: Path,
) -> list[Path]:
    root = root.resolve()
    pdf = (root / pdf).resolve() if not pdf.is_absolute() else pdf.resolve()
    try:
        project_relative = pdf.relative_to(root / "projects")
    except ValueError:
        project_relative = None
    if (
        project_relative is None
        or len(project_relative.parts) < 3
        or project_relative.parts[1] != "source"
        or pdf.suffix.lower() != ".pdf"
    ):
        raise PipelineError("render input must be a PDF inside projects/<project-id>/source/")
    if not pdf.is_file():
        raise PipelineError(f"source PDF not found: {pdf}")
    if dpi < 36 or dpi > 1200:
        raise PipelineError("DPI must be between 36 and 1200")
    try:
        document = fitz.open(pdf)
    except Exception as exc:
        raise PipelineError(f"cannot open corrupt or invalid PDF {pdf}: {exc}") from exc
    try:
        chosen = list(range(1, document.page_count + 1)) if all_pages else _parse_pages(pages or "", document.page_count)
        requested_absolute = output.is_absolute()
        logical_output = Path(os.path.abspath(output if requested_absolute else root / output))
        output = logical_output.resolve()
        try:
            logical_relative = logical_output.relative_to(root)
        except ValueError:
            logical_relative = None
        try:
            resolved_relative = output.relative_to(root)
        except ValueError:
            resolved_relative = None
        logical_allowed = (
            logical_relative is not None
            and bool(logical_relative.parts)
            and logical_relative.parts[0] in RENDER_ROOTS
        )
        resolved_allowed = (
            resolved_relative is not None
            and bool(resolved_relative.parts)
            and resolved_relative.parts[0] in RENDER_ROOTS
        )
        if (not requested_absolute and not logical_allowed) or (
            resolved_relative is not None and not resolved_allowed
        ):
            raise PipelineError(
                "repository-local render output must be under an ignored artifact root: "
                + ", ".join(sorted(RENDER_ROOTS))
            )
        output.mkdir(parents=True, exist_ok=True)
        results: list[Path] = []
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", pdf.stem).strip("-.") or "document"
        for number in chosen:
            target = output / f"{stem}-p{number:04d}-{dpi}dpi.png"
            temporary = target.with_name(f".{target.stem}.tmp.png")
            pixmap = document[number - 1].get_pixmap(matrix=matrix, alpha=False)
            pixmap.save(temporary)
            os.replace(temporary, target)
            results.append(target)
        return results
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"failed to render {pdf}: {exc}") from exc
    finally:
        document.close()
