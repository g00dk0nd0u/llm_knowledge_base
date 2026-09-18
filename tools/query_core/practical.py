from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .query import QueryCore


OPENING_RELATION_TYPES = frozenset(
    {"served_by", "related_opening", "has_opening", "opens_with"}
)
OPENING_WIDTH_NAMES = frozenset({"opening_width", "opening width"})
RELATIVE_ELEVATION_NAMES = frozenset(
    {"relative_elevation", "relative elevation", "elevation_relative_to_datum"}
)
RELATIVE_ELEVATION_SEMANTICS = frozenset(
    {"relative_elevation", "relative_finish_level", "elevation_relative_to_datum"}
)


def _entity(core: QueryCore, entity_id: str) -> tuple[str, dict[str, Any]] | None:
    matches = []
    for kind in ("element", "space", "level", "element_type"):
        value = core.get_entity(kind, entity_id)
        if value is not None:
            matches.append((kind, value))
    return matches[0] if len(matches) == 1 else None


def _compact_entity(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in (
            ("kind", kind),
            ("id", row["id"]),
            ("name", row.get("name") or row.get("type_name")),
            ("category", row.get("category")),
        )
        if value is not None
    }


def _compact_evidence(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    bbox = None
    if all(row.get(key) is not None for key in ("x_min", "y_min", "x_max", "y_max")):
        bbox = [row[key] for key in ("x_min", "y_min", "x_max", "y_max")]
    return {
        "evidence_id": row["id"],
        "document": {
            "id": row["document_id"],
            "identity": row["document_identity"],
            "source_filename": row["source_filename"],
        },
        "sheet_number": row.get("sheet_number"),
        "sheet_name": row.get("sheet_name"),
        "pdf_page": row["pdf_page"],
        "view": (
            {"id": row["view_id"], "name": row.get("view_name")}
            if row.get("view_id")
            else None
        ),
        "bbox": bbox,
        "coordinate_space": row["coordinate_space"],
    }


def _evidence(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        compact = _compact_evidence(record.get("evidence"))
        if compact is not None:
            unique[compact["evidence_id"]] = compact
    return sorted(
        unique.values(),
        key=lambda item: (
            item["document"]["identity"],
            item["pdf_page"],
            item["sheet_number"] or "",
            (item["view"] or {}).get("name") or "",
            item["evidence_id"],
        ),
    )


def _fact_key(record: dict[str, Any]) -> tuple[float, str]:
    return (record["numeric_value"], record["unit"])


def _opening_facts(
    core: QueryCore, entity_id: str
) -> tuple[int, list[dict[str, Any]]]:
    parameters = [
        row
        for row in core.get_numeric_facts("element", entity_id)
        if row["definition_name"].strip().casefold() in OPENING_WIDTH_NAMES
    ]
    if parameters:
        return 1, parameters
    annotations = [
        row
        for row in core.get_dimensions(entity_id)
        if (row.get("semantic_type") or "").strip().casefold() == "opening_width"
        and row.get("numeric_value") is not None
    ]
    return (2, annotations) if annotations else (0, [])


def get_opening_width(core: QueryCore, entity_id: str) -> dict[str, Any]:
    """Resolve an explicit opening-width fact through at most one relationship."""
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise ValueError("entity_id must be a non-empty string")
    resolved = _entity(core, entity_id)
    if resolved is None:
        return {"query_kind": "opening_width", "status": "not_found", "entity_id": entity_id}
    subject_kind, subject_row = resolved
    subject = _compact_entity(subject_kind, subject_row)

    rank, direct_facts = _opening_facts(core, entity_id) if subject_kind == "element" else (0, [])
    relationship: dict[str, Any] | None = None
    target_kind, target_row = subject_kind, subject_row
    facts = direct_facts
    if not facts:
        plausible = []
        for relation in core.get_related_entities(subject_kind, entity_id):
            if (
                relation["source_kind"] != subject_kind
                or relation["source_id"] != entity_id
                or relation["relation_type"] not in OPENING_RELATION_TYPES
                or relation["target_kind"] != "element"
            ):
                continue
            candidate = core.get_entity("element", relation["target_id"])
            candidate_rank, candidate_facts = _opening_facts(core, relation["target_id"])
            if candidate is not None and candidate_facts:
                plausible.append((relation, candidate, candidate_rank, candidate_facts))
        plausible.sort(key=lambda item: (item[0]["target_id"], item[0]["id"]))
        if len(plausible) > 1:
            return {
                "query_kind": "opening_width",
                "status": "ambiguous",
                "subject": subject,
                "candidate_ids": [item[0]["target_id"] for item in plausible],
                "evidence": _evidence(item[0] for item in plausible),
            }
        if not plausible:
            return {
                "query_kind": "opening_width",
                "status": "not_found",
                "subject": subject,
                "reason": "no explicit opening-width fact or related opening target",
                "evidence": [],
            }
        relationship, target_row, rank, facts = plausible[0]
        target_kind = relationship["target_kind"]

    values = sorted({_fact_key(fact) for fact in facts})
    path = [] if relationship is None else [{
        "relationship_id": relationship["id"],
        "relation_type": relationship["relation_type"],
        "source_id": relationship["source_id"],
        "target_id": relationship["target_id"],
        "provenance": relationship["provenance"],
        "confidence": relationship["confidence"],
    }]
    if len(values) > 1:
        return {
            "query_kind": "opening_width",
            "status": "conflict",
            "subject": subject,
            "resolved_target": _compact_entity(target_kind, target_row),
            "relationship_path": path,
            "candidates": [
                {"fact_id": fact["id"], "numeric_value": fact["numeric_value"], "unit": fact["unit"]}
                for fact in sorted(facts, key=lambda row: row["id"])
            ],
            "evidence": _evidence(([relationship] if relationship else []) + facts),
        }
    chosen = sorted(facts, key=lambda row: row["id"])[0]
    supporting = facts
    if rank == 1:
        supporting += [
            row for row in core.get_dimensions(target_row["id"])
            if (row.get("semantic_type") or "").casefold() == "opening_width"
            and row.get("numeric_value") is not None
            and _fact_key(row) == _fact_key(chosen)
        ]
    return {
        "query_kind": "opening_width",
        "status": "ok",
        "subject": subject,
        "resolved_target": _compact_entity(target_kind, target_row),
        "numeric_value": chosen["numeric_value"],
        "unit": chosen["unit"],
        "display_text": chosen.get("value_text") or chosen.get("display_text"),
        "fact_source_kind": "parameter" if rank == 1 else "annotation",
        "fact_id": chosen["id"],
        "provenance": chosen["provenance"],
        "confidence": chosen["confidence"],
        "relationship_path": path,
        "evidence": _evidence(([relationship] if relationship else []) + supporting),
    }


def _reference(display_text: str) -> str | None:
    match = re.match(r"^\s*(.*?)\s*[+-]\s*\d", display_text)
    return match.group(1).strip() or None if match else None


def get_relative_elevation(
    core: QueryCore, entity_id: str, reference: str | None = None
) -> dict[str, Any]:
    """Return an explicit relative-elevation annotation or parameter fact."""
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise ValueError("entity_id must be a non-empty string")
    resolved = _entity(core, entity_id)
    if resolved is None:
        return {"query_kind": "relative_elevation", "status": "not_found", "entity_id": entity_id}
    kind, row = resolved
    subject = _compact_entity(kind, row)
    annotations = [
        item
        for item in core.get_spot_elevations(entity_id)
        if item.get("numeric_value") is not None
        and (item.get("semantic_type") or "").strip().casefold()
        in RELATIVE_ELEVATION_SEMANTICS
    ]
    if reference is not None:
        annotations = [item for item in annotations if (_reference(item["display_text"]) or "").casefold() == reference.strip().casefold()]
    rank = 1
    facts = annotations
    if not facts:
        rank = 2
        facts = [
            item for item in core.get_numeric_facts(kind, entity_id)
            if item["definition_name"].strip().casefold() in RELATIVE_ELEVATION_NAMES
        ]
    if not facts:
        return {
            "query_kind": "relative_elevation", "status": "not_found",
            "subject": subject, "reference": reference, "evidence": [],
        }
    values = sorted({_fact_key(fact) for fact in facts})
    if len(values) > 1:
        return {
            "query_kind": "relative_elevation", "status": "conflict", "subject": subject,
            "candidates": [
                {"fact_id": fact["id"], "numeric_value": fact["numeric_value"], "unit": fact["unit"]}
                for fact in sorted(facts, key=lambda item: item["id"])
            ],
            "evidence": _evidence(facts),
        }
    chosen = sorted(facts, key=lambda item: item["id"])[0]
    return {
        "query_kind": "relative_elevation", "status": "ok", "subject": subject,
        "reference": _reference(chosen.get("display_text") or chosen.get("value_text") or ""),
        "numeric_value": chosen["numeric_value"], "unit": chosen["unit"],
        "display_text": chosen.get("display_text") or chosen.get("value_text"),
        "fact_source_kind": "annotation" if rank == 1 else "parameter",
        "annotation_id": chosen["id"] if rank == 1 else None,
        "fact_id": chosen["id"], "provenance": chosen["provenance"],
        "confidence": chosen["confidence"], "evidence": _evidence(facts),
    }
