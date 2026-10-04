"""Strict single-document adapter from supported PDF Pipeline versions."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any

from tools.table_geometry import TABLE_EPSILON

from .build import build_database
from .errors import QueryCoreError
from .pdf_pipeline_contract import created_from, require_matching_marker, require_pipeline_version


def _validate_output_location(
    output: Path,
    *,
    protected_files: tuple[Path, ...] = (),
    protected_directories: tuple[Path, ...] = (),
) -> None:
    """Reject output aliases that resolve onto pipeline-owned inputs."""
    resolved = Path(output).resolve()
    if resolved in {path.resolve() for path in protected_files}:
        raise QueryCoreError(
            "Query Core output may not overwrite protected PDF Pipeline project inputs"
        )
    for directory in protected_directories:
        try:
            resolved.relative_to(directory.resolve())
        except ValueError:
            continue
        raise QueryCoreError(
            "Query Core output may not be placed inside protected PDF Pipeline "
            "project inputs"
        )


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueryCoreError(f"invalid PDF Pipeline artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise QueryCoreError(f"PDF Pipeline artifact is not an object: {path}")
    return value


def _id(kind: str, identity: str, revision: str, *indexes: int) -> str:
    seed = "\0".join((identity, revision, *(str(value) for value in indexes)))
    return f"{kind}-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_id(source_file: Any) -> str:
    if not isinstance(source_file, str):
        raise QueryCoreError("source_file must be a canonical repository-relative path")
    logical = PurePosixPath(source_file)
    parts = logical.parts
    if (
        logical.is_absolute()
        or logical.as_posix() != source_file
        or len(parts) < 4
        or parts[0] != "projects"
        or not parts[1]
        or parts[2] != "source"
        or any(part in {".", ".."} for part in parts)
        or logical.suffix.lower() != ".pdf"
    ):
        raise QueryCoreError(
            "source_file must match projects/<project-id>/source/<path>.pdf"
        )
    return parts[1]


def _bbox(value: Any, label: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            for item in value
        )
        or value[0] > value[2]
        or value[1] > value[3]
    ):
        raise QueryCoreError(f"invalid {label} bbox")
    return [float(item) for item in value]


def _provenance(primitive: dict[str, Any], label: str) -> str:
    provenance = primitive.get("provenance")
    if provenance != "embedded_pdf_text":
        raise QueryCoreError(
            f"invalid {label} provenance: expected embedded_pdf_text"
        )
    return provenance


def records_from_pdf_pipeline(
    repo_root: Path, knowledge_directory: Path
) -> dict[str, Any]:
    """Validate and translate exactly one generated knowledge directory."""
    root, directory = Path(repo_root).resolve(), Path(knowledge_directory).resolve()
    document = _load(directory / "document.json")
    pipeline_version = require_pipeline_version(document.get("pipeline_version"))
    require_matching_marker(directory, pipeline_version)
    required = (
        "document_id",
        "source_file",
        "source_sha256",
        "project_id",
        "page_count",
        "pages",
    )
    if any(key not in document for key in required):
        raise QueryCoreError("invalid or unsupported PDF Pipeline document contract")
    identity, revision = document["source_file"], document["source_sha256"]
    source_project_id = _project_id(identity)
    if document["project_id"] != source_project_id:
        raise QueryCoreError("project_id does not match source_file project identity")
    expected_document_id = (
        "doc-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    )
    if document["document_id"] != expected_document_id:
        raise QueryCoreError("document_id does not match the logical source identity")
    source = (root / identity).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise QueryCoreError("source_file escapes repo root") from exc
    if not source.is_file():
        raise QueryCoreError(f"source PDF does not exist: {identity}")
    actual = _sha256(source)
    if actual != revision:
        raise QueryCoreError("source PDF byte SHA-256 mismatch")
    pages = document["pages"]
    if not isinstance(pages, list) or document["page_count"] != len(pages):
        raise QueryCoreError("document page count mismatch")
    title = document.get("pdf_metadata", {}).get("title") or Path(identity).name
    records: dict[str, Any] = {
        "project_id": document["project_id"],
        "created_from": created_from(pipeline_version),
        "binding_mode": "single_document",
        "source_document_identity": identity,
        "source_document_sha256": revision,
        "documents": [
            {
                "id": document["document_id"],
                "identity": identity,
                "title": title,
                "source_filename": Path(identity).name,
                "source_sha256": revision,
            }
        ],
        "pdf_pages": [],
        "pdf_text_blocks": [],
        "pdf_text_lines": [],
        "pdf_text_spans": [],
        "pdf_tables": [],
        "pdf_table_cells": [],
        "pdf_table_cell_spans": [],
        "evidence": [],
        "search_content": [],
    }
    for expected_page, summary in enumerate(pages, 1):
        if (
            not isinstance(summary, dict)
            or summary.get("page") != expected_page
            or summary.get("structured_page") != f"pages/p{expected_page:04d}.json"
        ):
            raise QueryCoreError(f"invalid document page identity: {expected_page}")
        sidecar = _load(directory / summary["structured_page"])
        expected_identity = {
            "document_id": document["document_id"],
            "source_file": identity,
            "source_sha256": revision,
            "page": expected_page,
        }
        if any(sidecar.get(key) != value for key, value in expected_identity.items()):
            raise QueryCoreError(
                f"sidecar/document identity mismatch on page {expected_page}"
            )
        crosscheck = (
            "width_points",
            "height_points",
            "rotation",
            "media_box",
            "crop_box",
            "coordinate_space",
        )
        if any(sidecar.get(key) != summary.get(key) for key in crosscheck):
            raise QueryCoreError(
                f"sidecar/document page metadata mismatch on page {expected_page}"
            )
        if sidecar.get("coordinate_space") != "pdf_points_top_left":
            raise QueryCoreError("unsupported PDF coordinate space")
        page_id = _id("pdf-page", identity, revision, expected_page)
        media, crop = _bbox(sidecar["media_box"], "media_box"), _bbox(
            sidecar["crop_box"], "crop_box"
        )
        records["pdf_pages"].append(
            {
                "id": page_id,
                "document_id": document["document_id"],
                "page_number": expected_page,
                "width_points": sidecar["width_points"],
                "height_points": sidecar["height_points"],
                "rotation": sidecar["rotation"],
                "media_x_min": media[0],
                "media_y_min": media[1],
                "media_x_max": media[2],
                "media_y_max": media[3],
                "crop_x_min": crop[0],
                "crop_y_min": crop[1],
                "crop_x_max": crop[2],
                "crop_y_max": crop[3],
                "coordinate_space": "pdf_points_top_left",
                "provenance": f"pdf_pipeline_v{pipeline_version}",
            }
        )
        blocks = sidecar.get("text_blocks")
        if not isinstance(blocks, list):
            raise QueryCoreError("sidecar text_blocks must be a list")
        for bi, block in enumerate(blocks):
            if block.get("order_index") != bi:
                raise QueryCoreError("non-deterministic block order")
            block_provenance = _provenance(block, "block")
            lines = block.get("lines")
            if not isinstance(lines, list) or block.get("text") != "\n".join(
                line.get("text", "") for line in lines
            ):
                raise QueryCoreError("inconsistent block text hierarchy")
            block_id, evidence_id = _id(
                "pdf-block", identity, revision, expected_page, bi
            ), _id("evidence", identity, revision, expected_page, bi)
            bbox = _bbox(block.get("bbox"), "block")
            common = dict(zip(("x_min", "y_min", "x_max", "y_max"), bbox))
            records["evidence"].append(
                {
                    "id": evidence_id,
                    "document_id": document["document_id"],
                    "sheet_id": None,
                    "view_id": None,
                    "pdf_page": expected_page,
                    **common,
                    "coordinate_space": "pdf_points_top_left",
                }
            )
            records["pdf_text_blocks"].append(
                {
                    "id": block_id,
                    "page_id": page_id,
                    "order_index": bi,
                    "text": block.get("text", ""),
                    **common,
                    "coordinate_space": "pdf_points_top_left",
                    "provenance": block_provenance,
                    "evidence_id": evidence_id,
                }
            )
            if block.get("text"):
                records["search_content"].append(
                    {
                        "record_kind": "pdf_text_block",
                        "record_id": block_id,
                        "content": block["text"],
                    }
                )
            for li, line in enumerate(lines):
                if line.get("order_index") != li:
                    raise QueryCoreError("non-deterministic line order")
                line_provenance = _provenance(line, "line")
                spans = line.get("spans")
                if not isinstance(spans, list) or line.get("text") != "".join(
                    span.get("text", "") for span in spans
                ):
                    raise QueryCoreError("inconsistent line text hierarchy")
                line_id = _id("pdf-line", identity, revision, expected_page, bi, li)
                lb = _bbox(line.get("bbox"), "line")
                records["pdf_text_lines"].append(
                    {
                        "id": line_id,
                        "block_id": block_id,
                        "order_index": li,
                        "text": line.get("text", ""),
                        **dict(zip(("x_min", "y_min", "x_max", "y_max"), lb)),
                        "coordinate_space": "pdf_points_top_left",
                        "provenance": line_provenance,
                    }
                )
                for si, span in enumerate(spans):
                    if span.get("order_index") != si:
                        raise QueryCoreError("non-deterministic span order")
                    span_provenance = _provenance(span, "span")
                    sb = _bbox(span.get("bbox"), "span")
                    row = {
                        "id": _id(
                            "pdf-span", identity, revision, expected_page, bi, li, si
                        ),
                        "line_id": line_id,
                        "order_index": si,
                        "text": span.get("text", ""),
                        **dict(zip(("x_min", "y_min", "x_max", "y_max"), sb)),
                        "coordinate_space": "pdf_points_top_left",
                        "provenance": span_provenance,
                    }
                    for source_key, target_key in (
                        ("font_name", "font_name"),
                        ("font_size", "font_size"),
                        ("font_flags", "font_flags"),
                    ):
                        if source_key in span:
                            row[target_key] = span[source_key]
                    records["pdf_text_spans"].append(row)
        if pipeline_version == "2":
            _append_tables(records, sidecar, identity, revision, expected_page, page_id)
    return records


def _append_tables(
    records: dict[str, Any], sidecar: dict[str, Any], identity: str,
    revision: str, page: int, page_id: str,
) -> None:
    """Strictly translate accepted v2 tables after resolving authoritative spans."""
    extraction, tables = sidecar.get("table_extraction"), sidecar.get("tables")
    required = {"status", "algorithm", "algorithm_version", "library", "library_version",
                "candidate_count", "accepted_count", "rejected_count", "rejection_counts"}
    count_keys = ("candidate_count", "accepted_count", "rejected_count")
    if (not isinstance(extraction, dict) or set(extraction) != required
            or extraction.get("status") not in {"completed", "no_candidates", "extraction_error"}
            or extraction.get("algorithm") != "pymupdf_lines_strict"
            or extraction.get("algorithm_version") != "1"
            or extraction.get("library") != "PyMuPDF"
            or not isinstance(extraction.get("library_version"), str)
            or not extraction.get("library_version")
            or any(not isinstance(extraction.get(k), int) or isinstance(extraction[k], bool)
                   or extraction[k] < 0 for k in count_keys)
            or not isinstance(extraction.get("rejection_counts"), dict)
            or any(not isinstance(k, str) or not k or not isinstance(v, int)
                   or isinstance(v, bool) or v < 1
                   for k, v in extraction.get("rejection_counts", {}).items())
            or not isinstance(tables, list) or extraction.get("accepted_count") != len(tables)
            or extraction.get("candidate_count") != extraction.get("accepted_count") + extraction.get("rejected_count")
            or (extraction.get("status") != "extraction_error"
                and sum(extraction.get("rejection_counts", {}).values()) != extraction.get("rejected_count"))
            or (extraction.get("status") == "extraction_error" and
                (extraction.get("candidate_count") != 0 or tables
                 or extraction.get("rejection_counts") != {"extraction_error": 1}))
            or (extraction.get("status") == "no_candidates" and
                (extraction.get("candidate_count") != 0 or tables
                 or extraction.get("rejection_counts")))
            or (extraction.get("status") == "completed" and
                extraction.get("candidate_count") < 1)):
        raise QueryCoreError("invalid Pipeline v2 table extraction contract")
    blocks = sidecar.get("text_blocks", [])
    previous_bbox = None
    for ti, table in enumerate(tables):
        if (not isinstance(table, dict) or set(table) != {"order_index", "bbox", "row_count", "column_count", "provenance", "cells"}
                or table.get("order_index") != ti or table.get("provenance") != "derived_pdf_table"):
            raise QueryCoreError("invalid Pipeline v2 table structure")
        tb = _bbox(table.get("bbox"), "table")
        geometry_key = (tb[1], tb[0], tb[3], tb[2])
        if tb[0] >= tb[2] or tb[1] >= tb[3] or (previous_bbox is not None and geometry_key < previous_bbox):
            raise QueryCoreError("invalid or non-deterministic table bbox")
        previous_bbox = geometry_key
        rows, columns, cells = table.get("row_count"), table.get("column_count"), table.get("cells")
        if (not isinstance(rows, int) or isinstance(rows, bool) or rows < 2
                or not isinstance(columns, int) or isinstance(columns, bool) or columns < 2
                or not isinstance(cells, list) or len(cells) != rows * columns):
            raise QueryCoreError("invalid Pipeline v2 table dimensions")
        table_id = _id("pdf-table", identity, revision, page, ti)
        records["pdf_tables"].append({"id": table_id, "page_id": page_id, "order_index": ti,
            "row_count": rows, "column_count": columns, **dict(zip(("x_min", "y_min", "x_max", "y_max"), tb)),
            "coordinate_space": "pdf_points_top_left", "provenance": "derived_pdf_table",
            "detection_method": extraction["algorithm"], "algorithm_version": extraction["algorithm_version"],
            "library": extraction["library"], "library_version": extraction["library_version"]})
        for ci, cell in enumerate(cells):
            row_index, column_index = divmod(ci, columns)
            if (not isinstance(cell, dict) or set(cell) != {"row_index", "column_index", "row_span", "column_span", "bbox", "text", "span_refs"}
                    or cell.get("row_index") != row_index or cell.get("column_index") != column_index
                    or cell.get("row_span") != 1 or cell.get("column_span") != 1
                    or not isinstance(cell.get("text"), str) or not isinstance(cell.get("span_refs"), list)):
                raise QueryCoreError("invalid Pipeline v2 table cell structure")
            cb = _bbox(cell.get("bbox"), "table cell")
            if cb[0] >= cb[2] or cb[1] >= cb[3] or cb[0] < tb[0] or cb[1] < tb[1] or cb[2] > tb[2] or cb[3] > tb[3]:
                raise QueryCoreError("table cell bbox lies outside table")
            resolved = []
            for ref in cell["span_refs"]:
                if not isinstance(ref, dict) or set(ref) != {"block_index", "line_index", "span_index"}:
                    raise QueryCoreError("malformed table cell span_ref")
                indexes = tuple(ref.get(k) for k in ("block_index", "line_index", "span_index"))
                if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in indexes):
                    raise QueryCoreError("malformed table cell span_ref")
                bi, li, si = indexes
                try:
                    span = blocks[bi]["lines"][li]["spans"][si]
                except (IndexError, KeyError, TypeError):
                    raise QueryCoreError("table cell span_ref does not resolve") from None
                if span.get("order_index") != si or not isinstance(span.get("text"), str) or not span["text"]:
                    raise QueryCoreError("table cell span_ref does not resolve")
                sb = _bbox(span.get("bbox"), "referenced span")
                if (sb[0] < cb[0] - TABLE_EPSILON or sb[1] < cb[1] - TABLE_EPSILON
                        or sb[2] > cb[2] + TABLE_EPSILON or sb[3] > cb[3] + TABLE_EPSILON):
                    raise QueryCoreError("table cell span_ref lies outside cell bbox")
                resolved.append((bi, li, si, span["text"], _id("pdf-span", identity, revision, page, bi, li, si)))
            if resolved != sorted(resolved, key=lambda item: item[:3]) or len({item[4] for item in resolved}) != len(resolved):
                raise QueryCoreError("table cell span_refs are not deterministic and unique")
            groups: list[list[str]] = []
            previous_line = None
            for bi, li, _si, text, _span_id in resolved:
                if (bi, li) != previous_line:
                    groups.append([]); previous_line = (bi, li)
                groups[-1].append(text)
            reconstructed = "\n".join("".join(group) for group in groups)
            if reconstructed != cell["text"]:
                raise QueryCoreError("table cell text does not match authoritative source spans")
            cell_id = _id("pdf-cell", identity, revision, page, ti, row_index, column_index)
            records["pdf_table_cells"].append({"id": cell_id, "table_id": table_id, "row_index": row_index,
                "column_index": column_index, "row_span": 1, "column_span": 1, "text": reconstructed,
                **dict(zip(("x_min", "y_min", "x_max", "y_max"), cb)), "coordinate_space": "pdf_points_top_left",
                "provenance": "derived_pdf_table"})
            records["pdf_table_cell_spans"].extend({"cell_id": cell_id, "span_id": item[4], "order_index": oi}
                for oi, item in enumerate(resolved))


def build_pdf_database(
    repo_root: Path, knowledge_directory: Path, output: Path
) -> Path:
    records = records_from_pdf_pipeline(repo_root, knowledge_directory)
    source = Path(repo_root).resolve() / records["source_document_identity"]
    _validate_output_location(
        output,
        protected_files=(source,),
        protected_directories=(Path(knowledge_directory),),
    )
    return build_database(records, output)
