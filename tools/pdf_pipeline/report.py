"""Deterministic, standard-library-only reports over PDF Pipeline v2 knowledge."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from .constants import MANY_DRAWINGS_THRESHOLD

REJECTION_REASONS = (
    "insufficient_dimensions",
    "invalid_shape",
    "merged_or_missing_cell",
    "invalid_cell_geometry",
    "ambiguous_span_mapping",
    "overlapping_candidate",
    "extraction_error",
)
STATUSES = ("extracted", "minimal_text", "no_text")
NOTICE = {
    "generated_knowledge": "Generated knowledge is an index, not authoritative evidence.",
    "source_pdf": "The original source PDF remains authoritative.",
    "vision_recommended": "vision_recommended is a processing hint only.",
    "review_queue": "A report entry does not imply that OCR or Vision is required.",
}


class ReportError(RuntimeError):
    """An actionable error in generated report input."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReportError(f"cannot read valid JSON object: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReportError(f"expected JSON object: {path}")
    return value


def _integer(value: Any, name: str, path: Path) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReportError(f"invalid {name}: {path}")
    return value


def _summary() -> dict[str, Any]:
    return {
        "page_count": 0,
        "extracted": 0,
        "minimal_text": 0,
        "no_text": 0,
        "pages_with_images": 0,
        "pages_with_many_drawings": 0,
        "vision_recommended_pages": 0,
        "pages_with_accepted_tables": 0,
        "accepted_table_count": 0,
        "table_candidate_count": 0,
        "rejected_table_candidate_count": 0,
        "table_extraction_error_pages": 0,
        "table_rejection_counts": {reason: 0 for reason in REJECTION_REASONS},
    }


def _add(target: dict[str, Any], page: dict[str, Any], table: dict[str, Any]) -> None:
    status = page["extraction_status"]
    target["page_count"] += 1
    target[status] += 1
    target["pages_with_images"] += page["image_count"] > 0
    target["pages_with_many_drawings"] += page["drawing_count"] >= MANY_DRAWINGS_THRESHOLD
    target["vision_recommended_pages"] += page["vision_recommended"]
    target["pages_with_accepted_tables"] += table["accepted_count"] > 0
    target["accepted_table_count"] += table["accepted_count"]
    target["table_candidate_count"] += table["candidate_count"]
    target["rejected_table_candidate_count"] += table["rejected_count"]
    target["table_extraction_error_pages"] += table["status"] == "extraction_error"
    for reason, count in table["rejection_counts"].items():
        target["table_rejection_counts"][reason] += count


def _validate_page(
    page: Any, sidecar: dict[str, Any], identity: dict[str, str], path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(page, dict):
        raise ReportError(f"invalid document page entry: {path}")
    status = page.get("extraction_status")
    if status not in STATUSES:
        raise ReportError(f"invalid extraction_status: {path}")
    values = {name: _integer(page.get(name), name, path) for name in ("page", "text_char_count", "image_count", "drawing_count")}
    if not isinstance(page.get("vision_recommended"), bool):
        raise ReportError(f"invalid vision_recommended: {path}")
    for name, expected in (*identity.items(), ("page", values["page"])):
        if sidecar.get(name) != expected:
            raise ReportError(f"{name} identity conflict: {path}")
    table = sidecar.get("table_extraction")
    if not isinstance(table, dict) or table.get("status") not in {"completed", "no_candidates", "extraction_error"}:
        raise ReportError(f"invalid table_extraction: {path}")
    counts = {name: _integer(table.get(name), name, path) for name in ("candidate_count", "accepted_count", "rejected_count")}
    reasons = table.get("rejection_counts")
    if not isinstance(reasons, dict) or not set(reasons).issubset(REJECTION_REASONS):
        raise ReportError(f"invalid table rejection_counts: {path}")
    checked_reasons = {key: _integer(value, f"rejection_counts.{key}", path) for key, value in reasons.items()}
    if any(count < 1 for count in checked_reasons.values()):
        raise ReportError(f"invalid table rejection_counts: {path}")
    tables = sidecar.get("tables")
    if not isinstance(tables, list):
        raise ReportError(f"inconsistent table extraction counts: {path}")
    table_status = table["status"]
    if table_status == "no_candidates":
        valid = counts == {"candidate_count": 0, "accepted_count": 0, "rejected_count": 0} and checked_reasons == {} and tables == []
    elif table_status == "extraction_error":
        valid = counts == {"candidate_count": 0, "accepted_count": 0, "rejected_count": 0} and checked_reasons == {"extraction_error": 1} and tables == []
    else:
        valid = (
            counts["candidate_count"] >= 1
            and counts["candidate_count"] == counts["accepted_count"] + counts["rejected_count"]
            and len(tables) == counts["accepted_count"]
            and "extraction_error" not in checked_reasons
            and sum(checked_reasons.values()) == counts["rejected_count"]
        )
    if not valid:
        raise ReportError(f"inconsistent table extraction counts: {path}")
    normalized_page = {**values, "extraction_status": status, "vision_recommended": page["vision_recommended"]}
    return normalized_page, {"status": table_status, **counts, "rejection_counts": checked_reasons}


def build_report(project_directory: Path) -> dict[str, Any]:
    """Build a report without opening any source PDF."""
    project = project_directory.resolve()
    manifest_path = project / "manifest.json"
    manifest = _read_object(manifest_path)
    project_id = manifest.get("project_id")
    documents = manifest.get("documents")
    if not isinstance(project_id, str) or not project_id or manifest.get("pipeline_version") != "2" or not isinstance(documents, list):
        raise ReportError(f"invalid Pipeline v2 project manifest: {manifest_path}")
    if project.name != project_id or project.parent.name != "projects":
        raise ReportError(f"project identity conflicts with directory: {manifest_path}")
    root = project.parent.parent
    project_summary = _summary()
    document_reports: list[dict[str, Any]] = []
    review_pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in sorted(documents, key=lambda item: item.get("source_file", "") if isinstance(item, dict) else ""):
        if not isinstance(entry, dict):
            raise ReportError(f"invalid document entry: {manifest_path}")
        required = ("document_id", "source_file", "source_sha256", "knowledge_path", "page_count")
        if any(name not in entry for name in required) or entry.get("pipeline_version") != "2":
            raise ReportError(f"invalid Pipeline v2 document entry: {manifest_path}")
        knowledge_rel = entry["knowledge_path"]
        expected_prefix = PurePosixPath("projects", project_id, "knowledge")
        knowledge_parts = PurePosixPath(knowledge_rel).parts if isinstance(knowledge_rel, str) else ()
        if len(knowledge_parts) < 4 or knowledge_parts[:3] != expected_prefix.parts or any(part in ("", ".", "..") for part in knowledge_parts):
            raise ReportError(f"invalid knowledge_path in {manifest_path}: {knowledge_rel!r}")
        knowledge = root.joinpath(*PurePosixPath(knowledge_rel).parts)
        if not knowledge.is_dir():
            raise ReportError(f"referenced knowledge directory does not exist: {knowledge}")
        document_path = knowledge / "document.json"
        document = _read_object(document_path)
        identity = {name: entry[name] for name in ("document_id", "source_file", "source_sha256")}
        if entry["document_id"] in seen or any(document.get(name) != value for name, value in identity.items()) or document.get("project_id") != project_id or document.get("pipeline_version") != "2":
            raise ReportError(f"document/project identity conflict: {document_path}")
        seen.add(entry["document_id"])
        pages = document.get("pages")
        page_count = _integer(entry["page_count"], "page_count", manifest_path)
        if not isinstance(pages, list) or document.get("page_count") != page_count or len(pages) != page_count:
            raise ReportError(f"page_count conflict: {document_path}")
        doc_summary = _summary()
        for expected_number, page in enumerate(pages, 1):
            expected_sidecar = f"pages/p{expected_number:04d}.json"
            if not isinstance(page, dict) or page.get("page") != expected_number or page.get("structured_page") != expected_sidecar:
                raise ReportError(f"invalid page ordering or sidecar reference: {document_path}")
            sidecar_path = knowledge.joinpath(*PurePosixPath(page["structured_page"]).parts)
            normalized, table = _validate_page(page, _read_object(sidecar_path), identity, sidecar_path)
            _add(doc_summary, normalized, table)
            _add(project_summary, normalized, table)
            reasons = []
            for reason, condition in (
                ("minimal_text", normalized["extraction_status"] == "minimal_text"),
                ("no_text", normalized["extraction_status"] == "no_text"),
                ("contains_raster_image", normalized["image_count"] > 0),
                ("many_vector_drawings", normalized["drawing_count"] >= MANY_DRAWINGS_THRESHOLD),
                ("vision_recommended", normalized["vision_recommended"]),
                ("table_candidates_rejected", table["rejected_count"] > 0),
                ("table_extraction_error", table["status"] == "extraction_error"),
            ):
                if condition:
                    reasons.append(reason)
            if reasons:
                review_pages.append({**identity, **normalized, "table_candidate_count": table["candidate_count"], "table_accepted_count": table["accepted_count"], "table_rejected_count": table["rejected_count"], "table_extraction_status": table["status"], "reasons": reasons})
        document_reports.append({**identity, **doc_summary})
    return {"project_id": project_id, "document_count": len(document_reports), **project_summary, "documents": document_reports, "review_pages": review_pages, "notice": NOTICE}


def format_text(report: dict[str, Any]) -> str:
    lines = [f"PDF intake / readiness report: {report['project_id']}", f"Documents: {report['document_count']}  Pages: {report['page_count']}", f"Text pages: extracted={report['extracted']} minimal={report['minimal_text']} none={report['no_text']}", f"Review pages: {len(report['review_pages'])}"]
    for page in report["review_pages"]:
        lines.append(f"- {page['source_file']} page {page['page']}: {', '.join(page['reasons'])}")
    lines.extend(("", *NOTICE.values()))
    return "\n".join(lines)
