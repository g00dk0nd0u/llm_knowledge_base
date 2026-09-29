from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .errors import QueryCoreError


def _has_valid_pdf_page(row: Mapping[str, Any]) -> bool:
    page = row.get("pdf_page")
    return isinstance(page, int) and not isinstance(page, bool) and page >= 1


def validate_drawing_references(
    references: Iterable[Mapping[str, Any]],
    evidence: Iterable[Mapping[str, Any]],
    views: Iterable[Mapping[str, Any]],
    sheets: Iterable[Mapping[str, Any]],
    viewports: Iterable[Mapping[str, Any]],
) -> None:
    """Validate the source-neutral drawing-reference fact contract."""
    evidence_rows = {row.get("id"): row for row in evidence}
    view_rows = {row.get("id"): row for row in views}
    sheet_rows = {row.get("id"): row for row in sheets}
    placements = {
        (row.get("sheet_id"), row.get("view_id")) for row in viewports
    }
    placed_views = {view_id for _, view_id in placements}

    for reference in references:
        relation_type = reference.get("relation_type")
        if not isinstance(relation_type, str) or not relation_type.strip():
            raise QueryCoreError("drawing reference relation_type must be non-empty")
        source = evidence_rows.get(reference.get("source_evidence_id"))
        if source is None:
            raise QueryCoreError(
                "drawing reference references nonexistent source evidence"
            )
        if not _has_valid_pdf_page(source):
            raise QueryCoreError("drawing reference source evidence has invalid pdf_page")
        state = reference.get("resolution_state")
        if state not in {
            "exact", "resolved_deterministically", "ambiguous", "unresolved",
        }:
            raise QueryCoreError("drawing reference has invalid resolution_state")
        pointer_keys = (
            "target_view_id", "target_sheet_id", "target_evidence_id",
        )
        pointers = [reference.get(key) for key in pointer_keys]
        if state in {"exact", "resolved_deterministically"} and not any(pointers):
            raise QueryCoreError("resolved drawing reference requires a target")
        if state in {"ambiguous", "unresolved"} and any(pointers):
            raise QueryCoreError("unresolved drawing reference must not select a target")

        view = view_rows.get(pointers[0]) if pointers[0] is not None else None
        sheet = sheet_rows.get(pointers[1]) if pointers[1] is not None else None
        target_evidence = (
            evidence_rows.get(pointers[2]) if pointers[2] is not None else None
        )
        if pointers[0] is not None and view is None:
            raise QueryCoreError("drawing reference references nonexistent target view")
        if pointers[1] is not None and sheet is None:
            raise QueryCoreError("drawing reference references nonexistent target sheet")
        if pointers[2] is not None and target_evidence is None:
            raise QueryCoreError(
                "drawing reference references nonexistent target evidence"
            )
        if sheet is not None and not _has_valid_pdf_page(sheet):
            raise QueryCoreError("drawing reference target sheet has invalid pdf_page")
        if target_evidence is not None and not _has_valid_pdf_page(target_evidence):
            raise QueryCoreError(
                "drawing reference target evidence has invalid pdf_page"
            )
        targets = [row for row in (view, sheet, target_evidence) if row is not None]
        if len({row["document_id"] for row in targets}) > 1:
            raise QueryCoreError(
                "drawing reference targets belong to different documents"
            )
        if target_evidence is not None:
            if view is not None and target_evidence.get("view_id") not in {
                None, view["id"],
            }:
                raise QueryCoreError("drawing reference target evidence/view mismatch")
            if sheet is not None:
                if target_evidence.get("sheet_id") not in {None, sheet["id"]}:
                    raise QueryCoreError(
                        "drawing reference target evidence/sheet mismatch"
                    )
                if target_evidence.get("pdf_page") != sheet.get("pdf_page"):
                    raise QueryCoreError(
                        "drawing reference target evidence/sheet page mismatch"
                    )
        if (
            view is not None
            and sheet is not None
            and view["id"] in placed_views
            and (sheet["id"], view["id"]) not in placements
        ):
            raise QueryCoreError(
                "drawing reference target view is not placed on target sheet"
            )
