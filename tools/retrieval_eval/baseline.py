"""Observe explicit existing Query Core calls without expanding or reranking."""

from __future__ import annotations

import json
from typing import Any

from tools.query_core import QueryCore

from .contract import LOCATION_FIELDS, validate_case


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _unique(values: list[Any]) -> list[Any]:
    seen = set()
    result = []
    for value in values:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def observe(payload: Any) -> dict[str, list[Any]]:
    """Index only returned facts/navigation; never retrieve missing navigation."""
    entities, documents, references, evidence_ids, locations = [], [], [], [], []
    for row in _walk(payload):
        if "entity_class" in row and row.get("id"):
            entities.append(row["id"])
        if row.get("identity") and "source_filename" in row:
            documents.append(row["identity"])
        if row.get("source_kind") and row.get("source_id"):
            references.append({"kind": row["source_kind"], "id": row["source_id"]})
        if row.get("record_kind") and row.get("record_id"):
            references.append({"kind": row["record_kind"], "id": row["record_id"]})
        for column, kind in (
            ("block_id", "pdf_text_block"), ("span_id", "pdf_text_span"),
            ("cell_id", "pdf_table_cell"), ("appearance_id", "entity_appearance"),
            ("property_id", "semantic_property"), ("parameter_id", "parameter"),
        ):
            if row.get(column):
                references.append({"kind": kind, "id": row[column]})
        for key in ("source", "source_reference"):
            ref = row.get(key)
            if isinstance(ref, dict) and ref.get("kind") and ref.get("id"):
                references.append({"kind": ref["kind"], "id": ref["id"]})
        if row.get("evidence_id"):
            evidence_ids.append(row["evidence_id"])
        evidence_ids.extend(row.get("evidence_refs", []))
        # Navigation may be a single object or a list inside spatial contexts.
        # Index the actual returned navigation object, not its containing record.
        if not {"document", "source_kind", "source_id", "can_zoom"} <= row.keys():
            continue
        nav = row
        location = {key: nav[key] for key in LOCATION_FIELDS if key in nav}
        document = nav.get("document") or {}
        if document.get("identity"):
            location["document_identity"] = document["identity"]
        if document.get("id"):
            location["document_id"] = document["id"]
        if nav.get("source_kind") and nav.get("source_id"):
            location["source_reference"] = {"kind": nav["source_kind"], "id": nav["source_id"]}
        if location:
            locations.append(location)
    return {
        "semantic_entity_ids": _unique(entities),
        "document_identities": _unique(documents),
        "source_references": _unique(references),
        "evidence_ids": _unique(evidence_ids),
        "evidence": _unique(locations),
    }


def execute_baseline(core: QueryCore, case: dict[str, Any]) -> dict[str, Any]:
    validate_case(case)
    operation = case["baseline"]["operation"]
    args = case["baseline"]["arguments"]
    ranking = None
    if operation in {"search_semantic_entities", "semantic_discovery_context"}:
        discovery = core.search_semantic_entities(**args)
        ranking = [{"position": i, "semantic_entity_id": row["semantic_entity"]["id"]}
                   for i, row in enumerate(discovery["candidates"], 1)]
        payload = discovery
        if operation == "semantic_discovery_context":
            context = None
            if discovery["status"] == "exact_unique":
                # Query Core ranks exact matches first. exact_unique may still
                # include contains candidates; selection is gated by full status.
                selected = discovery["candidates"][0]
                context = core.get_architectural_evidence_context(selected["semantic_entity"]["id"])
            payload = {"discovery": discovery, "context": context}
            status = context["status"] if context is not None else discovery["status"]
        else:
            status = discovery["status"]
        query_core_status = status
    else:
        # Operation whitelist was validated above. No arbitrary method execution.
        payload = getattr(core, operation)(**args)
        query_core_status = payload.get("status", payload.get("resolution_state")) if isinstance(payload, dict) else None
        status = query_core_status or ("not_found" if payload is None else "returned")
        if operation in {"search_text", "search_pdf_text"}:
            status = "returned" if payload else "no_match"
            ranking = [{"position": i, "source_reference": (
                {"kind": row["record_kind"], "id": row["record_id"]}
                if operation == "search_text" else {"kind": "pdf_text_block", "id": row["block_id"]}
            )} for i, row in enumerate(payload, 1)]
    observed = observe(payload)
    if ranking:
        observed["source_references"] = _unique(observed["source_references"] + [
            row["source_reference"] for row in ranking if "source_reference" in row
        ])
    return {
        "status": status, "query_core_status": query_core_status,
        "ranking": ranking, "ranking_limit": args.get("limit", 20) if ranking is not None else None,
        "payload": payload, "observed": observed,
    }
