"""Render an explicitly selected Phase 5A candidate; never execute Vision."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

import pymupdf as fitz

from tools.query_core.errors import QueryCoreError
from tools.query_core.query import QueryCore

from .pipeline import (
    PipelineError,
    _atomic_png,
    _page_boxes_in_unrotated_page_space,
    _render_output_directory,
    _render_pixmap,
)


def _candidate_bbox(candidate: dict[str, Any]) -> list[float] | None:
    scope, bbox = candidate.get("input_scope"), candidate.get("bbox")
    if scope == "page":
        if bbox is not None:
            raise PipelineError("page candidate must have null bbox")
        return None
    if scope != "region":
        raise PipelineError("unsupported candidate input scope")
    if candidate.get("coordinate_space") != "pdf_points_top_left":
        raise PipelineError("unsupported coordinate space for region candidate")
    try:
        valid = (
            isinstance(bbox, list) and len(bbox) == 4
            and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in bbox)
            and bbox[0] < bbox[2] and bbox[1] < bbox[3]
        )
    except OverflowError:
        valid = False
    if not valid:
        raise PipelineError("invalid bbox for region candidate")
    return list(bbox)


def render_vision_candidate(
    repo_root: Path,
    database: Path,
    semantic_entity_id: str,
    candidate_id: str,
    source_pdf: Path,
    *,
    dpi: int = 300,
    output: Path,
) -> dict[str, Any]:
    """Regenerate/select a candidate, verify PDF bytes, and render exact evidence.

    Relative paths resolve against repo_root. The supplied source may be copied
    or renamed: only its SHA is authoritative. Inputs are never saved or updated.
    Region bboxes use the pipeline's unrotated page-local crop extent. PyMuPDF's
    get_pixmap clip uses displayed page coordinates, so rotation_matrix maps the
    exact stored rectangle into that space without adding context or clipping.
    """
    root = repo_root.resolve()
    database = (root / database).resolve()
    source_pdf = (root / source_pdf).resolve()
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 36 <= dpi <= 1200:
        raise PipelineError("DPI must be an integer between 36 and 1200")
    try:
        with QueryCore(database) as core:
            routed = core.get_vision_evidence_candidates(semantic_entity_id)
    except QueryCoreError as exc:
        raise PipelineError(f"cannot load Query Core: {exc}") from exc
    if routed is None:
        raise PipelineError(f"semantic entity not found or unavailable: {semantic_entity_id}")
    matches = [row for row in routed["candidates"] if row["candidate_id"] == candidate_id]
    if not matches:
        raise PipelineError(f"candidate not found: {candidate_id}")
    if len(matches) != 1:
        raise PipelineError(f"duplicate candidate ID: {candidate_id}")
    candidate = matches[0]
    bbox = _candidate_bbox(candidate)
    document_metadata = dict(candidate["document"])
    expected_sha = document_metadata.get("source_sha256")
    if expected_sha is None or expected_sha == "":
        raise PipelineError("candidate source SHA missing")
    if not isinstance(expected_sha, str) or re.fullmatch(r"[a-f0-9]{64}", expected_sha) is None:
        raise PipelineError("candidate source SHA malformed")
    if not source_pdf.is_file():
        raise PipelineError(f"source PDF not found: {source_pdf}")
    try:
        # Open the very bytes that were hashed, avoiding a hash/open race if the
        # caller replaces the file. This retains one PDF byte buffer in memory.
        source_bytes = source_pdf.read_bytes()
    except OSError as exc:
        raise PipelineError(f"cannot read source PDF: {source_pdf}: {exc}") from exc
    if hashlib.sha256(source_bytes).hexdigest() != expected_sha:
        raise PipelineError("source SHA mismatch")
    try:
        document = fitz.open(stream=source_bytes, filetype="pdf")
    except Exception as exc:
        raise PipelineError(f"cannot open PDF: {exc}") from exc
    try:
        if document.needs_pass:
            raise PipelineError("password-protected PDF")
        number = candidate.get("pdf_page")
        if (isinstance(number, bool) or not isinstance(number, int)
                or not 1 <= number <= document.page_count):
            raise PipelineError("page outside PDF")
        page = document[number - 1]
        clip = None
        if bbox is not None:
            _, extent = _page_boxes_in_unrotated_page_space(page)
            if not (extent[0] <= bbox[0] < bbox[2] <= extent[2]
                    and extent[1] <= bbox[1] < bbox[3] <= extent[3]):
                raise PipelineError("out-of-bounds bbox")
            clip = fitz.Rect(bbox) * page.rotation_matrix
            # Do not let PyMuPDF float conversion collapse tiny valid numbers or
            # silently alter coordinates beyond the actual displayed page.
            if clip.is_empty or clip.is_infinite or not page.rect.contains(clip):
                raise PipelineError("invalid/out-of-bounds bbox in renderer coordinates")
        if re.fullmatch(r"vision-candidate-[a-f0-9]{64}", candidate_id) is None:
            raise PipelineError("malformed candidate ID")
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(
            document_metadata.get("source_filename") or "document"
        ).stem).strip("-.")[:80] or "document"
        directory = _render_output_directory(root, output)
        target = directory / f"{stem}-{candidate_id}-p{number:04d}-{dpi}dpi.png"
        if target.resolve() in {source_pdf, database}:
            raise PipelineError("unsafe output path: would replace a read-only input")
        pixmap = _render_pixmap(page, dpi, clip=clip)
        if pixmap.width <= 0 or pixmap.height <= 0:
            raise PipelineError("render failure: empty PNG")
        output_sha = _atomic_png(pixmap, target)
        try:
            output_path = target.relative_to(root).as_posix()
        except ValueError:
            output_path = target.as_posix()
        return {
            "status": "ok",
            "candidate_id": candidate_id,
            "semantic_entity_id": semantic_entity_id,
            "input_scope": candidate["input_scope"],
            "document": document_metadata,
            "pdf_page": number,
            "bbox": bbox,
            "coordinate_space": candidate.get("coordinate_space"),
            "dpi": dpi,
            "pixel_width": pixmap.width,
            "pixel_height": pixmap.height,
            "renderer": {
                "name": "tools.pdf_pipeline.vision_render",
                "library": "PyMuPDF",
                "library_version": fitz.__version__,
                "page_rotation": page.rotation,
                "alpha": False,
            },
            "output_path": output_path,
            "output_sha256": output_sha,
        }
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"render failure: {exc}") from exc
    finally:
        document.close()
