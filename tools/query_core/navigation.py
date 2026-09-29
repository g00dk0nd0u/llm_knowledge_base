from __future__ import annotations

import json
from typing import Any, Iterable


_BBOX_KEYS = ("x_min", "y_min", "x_max", "y_max")


def navigation_target(
    row: dict[str, Any], *, source_kind: str
) -> dict[str, Any]:
    """Convert a stored evidence or appearance row into a viewer-neutral target."""
    bbox = (
        [row[key] for key in _BBOX_KEYS]
        if all(row.get(key) is not None for key in _BBOX_KEYS)
        else None
    )
    source_id_key = (
        "evidence_id" if source_kind == "evidence" else
        "appearance_id" if source_kind == "entity_appearance" else "source_id"
    )
    target = {
        "document": {
            "id": row["document_id"],
            "identity": row["document_identity"],
            "source_filename": row["source_filename"],
        },
        "sheet_id": row.get("sheet_id"),
        "sheet_number": row.get("sheet_number"),
        "sheet_name": row.get("sheet_name"),
        "pdf_page": row["pdf_page"],
        "view_id": row.get("view_id"),
        "view_name": row.get("view_name"),
        "bbox": bbox,
        "coordinate_space": row.get("coordinate_space"),
        "bbox_quality": row.get("bbox_quality"),
        "link_instance_id": row.get("link_instance_id"),
        "provenance": row.get("provenance"),
        "source_kind": source_kind,
        "source_id": row[source_id_key],
        "can_zoom": bbox is not None,
        source_id_key: row[source_id_key],
    }
    if source_kind == "entity_appearance":
        target["entity_kind"] = row["entity_kind"]
        target["entity_id"] = row["entity_id"]
    return target


def deduplicate_navigation(
    targets: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate equivalent stored locations and return a stable ordering."""
    unique: dict[str, dict[str, Any]] = {}
    for target in targets:
        # IDs establish traceability, but duplicate exported appearances may have
        # different IDs. Keep the lexically first ID for an identical location.
        location = {
            key: value
            for key, value in target.items()
            if key not in {"source_id", "appearance_id", "evidence_id"}
        }
        key = json.dumps(location, sort_keys=True, separators=(",", ":"))
        current = unique.get(key)
        if current is None or target["source_id"] < current["source_id"]:
            unique[key] = target
    return sorted(
        unique.values(),
        key=lambda item: (
            item["document"]["identity"],
            item["pdf_page"],
            item.get("sheet_number") or "",
            item.get("view_name") or "",
            item["source_kind"],
            item["source_id"],
        ),
    )
