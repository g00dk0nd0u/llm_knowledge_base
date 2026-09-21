"""Strict single-document adapter from PDF Pipeline v1 to Query Core v2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .build import build_database
from .errors import QueryCoreError


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


def _bbox(value: Any, label: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value
        )
        or value[0] > value[2]
        or value[1] > value[3]
    ):
        raise QueryCoreError(f"invalid {label} bbox")
    return [float(item) for item in value]


def records_from_pdf_pipeline(
    repo_root: Path, knowledge_directory: Path
) -> dict[str, Any]:
    """Validate and translate exactly one generated knowledge directory."""
    root, directory = Path(repo_root).resolve(), Path(knowledge_directory).resolve()
    if not (directory / ".pdf-pipeline-v1").is_file():
        raise QueryCoreError(
            "knowledge directory lacks .pdf-pipeline-v1 ownership marker"
        )
    document = _load(directory / "document.json")
    required = (
        "document_id",
        "source_file",
        "source_sha256",
        "project_id",
        "page_count",
        "pages",
    )
    if document.get("pipeline_version") != "1" or any(
        key not in document for key in required
    ):
        raise QueryCoreError("invalid or unsupported PDF Pipeline document contract")
    identity, revision = document["source_file"], document["source_sha256"]
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
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != revision:
        raise QueryCoreError("source PDF byte SHA-256 mismatch")
    pages = document["pages"]
    if not isinstance(pages, list) or document["page_count"] != len(pages):
        raise QueryCoreError("document page count mismatch")
    title = document.get("pdf_metadata", {}).get("title") or Path(identity).name
    records: dict[str, Any] = {
        "project_id": document["project_id"],
        "created_from": "pdf-pipeline/1",
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
                "provenance": "pdf_pipeline_v1",
            }
        )
        blocks = sidecar.get("text_blocks")
        if not isinstance(blocks, list):
            raise QueryCoreError("sidecar text_blocks must be a list")
        for bi, block in enumerate(blocks):
            if block.get("order_index") != bi:
                raise QueryCoreError("non-deterministic block order")
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
                    "provenance": block.get("provenance", "embedded_pdf_text"),
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
                        "provenance": line.get("provenance", "embedded_pdf_text"),
                    }
                )
                for si, span in enumerate(spans):
                    if span.get("order_index") != si:
                        raise QueryCoreError("non-deterministic span order")
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
                        "provenance": span.get("provenance", "embedded_pdf_text"),
                    }
                    for source_key, target_key in (
                        ("font_name", "font_name"),
                        ("font_size", "font_size"),
                        ("font_flags", "font_flags"),
                    ):
                        if source_key in span:
                            row[target_key] = span[source_key]
                    records["pdf_text_spans"].append(row)
    return records


def build_pdf_database(
    repo_root: Path, knowledge_directory: Path, output: Path
) -> Path:
    return build_database(
        records_from_pdf_pipeline(repo_root, knowledge_directory), output
    )
