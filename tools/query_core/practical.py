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
_DATUM_PREFIX = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_. -]*?)\s*[+-]\s*\d")
_DATUM_TEXT = re.compile(r"^[A-Za-z][A-Za-z0-9_. -]{0,31}$")


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


def _explicit_evidence(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    bbox = None
    if all(row.get(key) is not None for key in ("x_min", "y_min", "x_max", "y_max")):
        bbox = [row[key] for key in ("x_min", "y_min", "x_max", "y_max")]
    return {
        "source_kind": "evidence",
        "evidence_id": row["id"],
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
        "coordinate_space": row["coordinate_space"],
    }


def _occurrence_evidence(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in (
            ("source_kind", "entity_appearance"),
            ("appearance_id", row["appearance_id"]),
            ("document", row["document"]),
            ("sheet_id", row["sheet_id"]),
            ("sheet_number", row["sheet_number"]),
            ("sheet_name", row["sheet_name"]),
            ("pdf_page", row["pdf_page"]),
            ("view_id", row["view_id"]),
            ("view_name", row["view_name"]),
            ("bbox", row["bbox"]),
            ("coordinate_space", row["coordinate_space"]),
            ("bbox_quality", row["bbox_quality"]),
            ("appearance_kind", row["appearance_kind"]),
            ("link_instance_id", row["link_instance_id"]),
            ("provenance", row["provenance"]),
        )
        if value is not None or key in {"bbox", "link_instance_id"}
    }


def _evidence(
    records: Iterable[dict[str, Any]], occurrences: Iterable[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    items = [
        compact
        for record in records
        if (compact := _explicit_evidence(record.get("evidence"))) is not None
    ]
    items.extend(_occurrence_evidence(row) for row in occurrences)
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        identity = (
            ("evidence", item["evidence_id"])
            if "evidence_id" in item
            else ("appearance", item["appearance_id"])
        )
        unique[identity] = item
    return sorted(
        unique.values(),
        key=lambda item: (
            item["document"]["identity"],
            item["pdf_page"],
            item.get("sheet_number") or "",
            item.get("view_name") or "",
            item.get("evidence_id") or item.get("appearance_id") or "",
        ),
    )


def _occurrences(
    core: QueryCore, entities: Iterable[tuple[str, str]]
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for kind, entity_id in entities:
        for row in core.get_occurrence_evidence(kind, entity_id):
            unique[row["appearance_id"]] = row
    return list(unique.values())


def _fact_key(record: dict[str, Any]) -> tuple[float, str]:
    return (record["numeric_value"], record["unit"])


def _opening_facts(
    core: QueryCore, entity_id: str
) -> tuple[int, list[dict[str, Any]]]:
    parameters = core.get_numeric_facts("element", entity_id)
    builtin = [
        row
        for row in parameters
        if (row.get("definition_key") or "").casefold() == "builtin:door_width"
    ]
    if builtin:
        return 1, builtin
    named = [
        row
        for row in parameters
        if row["definition_name"].strip().casefold() in OPENING_WIDTH_NAMES
    ]
    if named:
        return 2, named
    annotations = [
        row
        for row in core.get_dimensions(entity_id)
        if (row.get("semantic_type") or "").strip().casefold() == "opening_width"
        and row.get("numeric_value") is not None
    ]
    return (3, annotations) if annotations else (0, [])


def _relationship_path(relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "relationship_id": row["id"],
            "relation_type": row["relation_type"],
            "source_id": row["source_id"],
            "target_id": row["target_id"],
            "provenance": row["provenance"],
            "confidence": row["confidence"],
        }
        for row in sorted(relationships, key=lambda item: item["id"])
    ]


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
    # A dimension may reference equipment beside an opening. Prefer an explicit
    # related opening target over treating that referenced equipment as the opening.
    facts = direct_facts if rank in {1, 2} else []
    relationships: list[dict[str, Any]] = []
    target_kind, target_row = subject_kind, subject_row

    if not facts:
        targets: dict[str, dict[str, Any]] = {}
        for relation in core.get_related_entities(subject_kind, entity_id):
            if not (
                relation["source_kind"] == subject_kind
                and relation["source_id"] == entity_id
                and relation["relation_type"] in OPENING_RELATION_TYPES
                and relation["target_kind"] == "element"
            ):
                continue
            candidate = core.get_entity("element", relation["target_id"])
            candidate_rank, candidate_facts = _opening_facts(core, relation["target_id"])
            if candidate is not None and candidate_facts:
                target = targets.setdefault(
                    relation["target_id"],
                    {"entity": candidate, "rank": candidate_rank, "facts": candidate_facts, "relations": []},
                )
                target["relations"].append(relation)
        if len(targets) > 1:
            candidate_ids = sorted(targets)
            records = [relation for value in targets.values() for relation in value["relations"]]
            occurrences = _occurrences(
                core, [(subject_kind, entity_id), *(("element", value) for value in candidate_ids)]
            )
            return {
                "query_kind": "opening_width", "status": "ambiguous", "subject": subject,
                "candidate_ids": candidate_ids, "evidence": _evidence(records, occurrences),
            }
        if not targets and direct_facts:
            facts = direct_facts
        elif not targets:
            return {
                "query_kind": "opening_width", "status": "not_found", "subject": subject,
                "reason": "no explicit opening-width fact or related opening target", "evidence": [],
            }
        else:
            _target_id, target = next(iter(targets.items()))
            target_kind, target_row = "element", target["entity"]
            rank, facts, relationships = target["rank"], target["facts"], target["relations"]

    records = relationships + facts
    annotation_facts = facts if rank == 3 else [
        row
        for row in core.get_dimensions(target_row["id"])
        if (row.get("semantic_type") or "").strip().casefold() == "opening_width"
        and row.get("numeric_value") is not None
        and _fact_key(row) in {_fact_key(fact) for fact in facts}
    ]
    records += annotation_facts
    occurrences = _occurrences(
        core,
        [(subject_kind, entity_id), (target_kind, target_row["id"])]
        + [("annotation", row["id"]) for row in annotation_facts],
    )
    values = sorted({_fact_key(fact) for fact in facts})
    path = _relationship_path(relationships)
    if len(values) > 1:
        return {
            "query_kind": "opening_width", "status": "conflict", "subject": subject,
            "resolved_target": _compact_entity(target_kind, target_row), "relationship_path": path,
            "candidates": [
                {"fact_id": fact["id"], "numeric_value": fact["numeric_value"], "unit": fact["unit"]}
                for fact in sorted(facts, key=lambda row: row["id"])
            ],
            "evidence": _evidence(records, occurrences),
        }
    chosen = min(facts, key=lambda row: row["id"])
    return {
        "query_kind": "opening_width", "status": "ok", "subject": subject,
        "resolved_target": _compact_entity(target_kind, target_row),
        "numeric_value": chosen["numeric_value"], "unit": chosen["unit"],
        "display_text": chosen.get("value_text") or chosen.get("display_text"),
        "fact_source_kind": "annotation" if rank == 3 else "parameter",
        "fact_id": chosen["id"], "supporting_fact_ids": sorted(row["id"] for row in facts),
        "provenance": chosen["provenance"], "confidence": chosen["confidence"],
        "relationship_path": path, "evidence": _evidence(records, occurrences),
    }


def _datum_text(value: str | None) -> str | None:
    if not value:
        return None
    match = _DATUM_PREFIX.match(value)
    if match:
        return match.group(1).strip()
    text = value.strip()
    return text if _DATUM_TEXT.fullmatch(text) and not any(char.isdigit() for char in text) else None


def _annotation_datums(core: QueryCore, annotation: dict[str, Any]) -> list[str]:
    values = [_datum_text(annotation.get("display_text"))]
    for segment in core.get_annotation_segments(annotation["id"]):
        values.extend(
            _datum_text(segment.get(key))
            for key in ("display_text", "prefix", "suffix", "above", "below")
        )
    return sorted({value for value in values if value}, key=str.casefold)


def _parameter_datums(parameter: dict[str, Any]) -> list[str]:
    return sorted(
        {value for value in (_datum_text(parameter.get("value_text")), _datum_text(parameter.get("raw_value_text"))) if value},
        key=str.casefold,
    )


def get_relative_elevation(
    core: QueryCore, entity_id: str, reference: str | None = None
) -> dict[str, Any]:
    """Return a relative elevation only when its stored datum is explicit."""
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise ValueError("entity_id must be a non-empty string")
    resolved = _entity(core, entity_id)
    if resolved is None:
        return {"query_kind": "relative_elevation", "status": "not_found", "entity_id": entity_id}
    kind, row = resolved
    subject = _compact_entity(kind, row)
    spots = [item for item in core.get_spot_elevations(entity_id) if item.get("numeric_value") is not None]
    candidates = [(item, _annotation_datums(core, item), 1) for item in spots]
    if not candidates:
        parameters = [
            item for item in core.get_numeric_facts(kind, entity_id)
            if item["definition_name"].strip().casefold() in RELATIVE_ELEVATION_NAMES
        ]
        candidates = [(item, _parameter_datums(item), 2) for item in parameters]
    if not candidates:
        return {
            "query_kind": "relative_elevation", "status": "not_found", "subject": subject,
            "reference": reference, "evidence": [],
        }

    requested = reference.strip().casefold() if reference is not None else None
    if requested is not None:
        selected = [item for item in candidates if any(datum.casefold() == requested for datum in item[1])]
        if not selected:
            return {
                "query_kind": "relative_elevation", "status": "insufficient_data",
                "subject": subject, "reference": reference,
                "reason": "stored facts do not establish the requested reference datum",
                "evidence": _evidence(
                    (item[0] for item in candidates),
                    _occurrences(core, [(kind, entity_id)] + [("annotation", item[0]["id"]) for item in candidates if item[2] == 1]),
                ),
            }
        candidates = selected
    else:
        datums = sorted({datum.casefold(): datum for _, values, _ in candidates for datum in values}.values(), key=str.casefold)
        if len(datums) > 1:
            return {
                "query_kind": "relative_elevation", "status": "ambiguous", "subject": subject,
                "candidate_references": datums,
                "evidence": _evidence((item[0] for item in candidates)),
            }
        if not datums:
            return {
                "query_kind": "relative_elevation", "status": "insufficient_data",
                "subject": subject, "reference": None,
                "reason": "stored facts do not establish a reference datum",
                "evidence": _evidence((item[0] for item in candidates)),
            }
        reference = datums[0]
        candidates = [item for item in candidates if any(datum.casefold() == reference.casefold() for datum in item[1])]

    facts = [item[0] for item in candidates]
    annotation_ids = [("annotation", item[0]["id"]) for item in candidates if item[2] == 1]
    occurrences = _occurrences(core, [(kind, entity_id)] + annotation_ids)
    values = sorted({_fact_key(fact) for fact in facts})
    established = next(datum for _, datums, _ in candidates for datum in datums if reference is None or datum.casefold() == reference.casefold())
    if len(values) > 1:
        return {
            "query_kind": "relative_elevation", "status": "conflict", "subject": subject,
            "reference": established,
            "candidates": [
                {"fact_id": fact["id"], "numeric_value": fact["numeric_value"], "unit": fact["unit"]}
                for fact in sorted(facts, key=lambda item: item["id"])
            ],
            "evidence": _evidence(facts, occurrences),
        }
    chosen, _, rank = min(candidates, key=lambda item: item[0]["id"])
    return {
        "query_kind": "relative_elevation", "status": "ok", "subject": subject,
        "reference": established, "numeric_value": chosen["numeric_value"], "unit": chosen["unit"],
        "display_text": chosen.get("display_text") or chosen.get("value_text"),
        "fact_source_kind": "annotation" if rank == 1 else "parameter",
        "annotation_id": chosen["id"] if rank == 1 else None,
        "fact_id": chosen["id"], "supporting_fact_ids": sorted(fact["id"] for fact in facts),
        "provenance": chosen["provenance"], "confidence": chosen["confidence"],
        "evidence": _evidence(facts, occurrences),
    }
