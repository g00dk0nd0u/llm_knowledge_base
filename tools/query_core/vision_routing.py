"""Deterministic routing of existing architectural context; no Vision execution."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def route_vision_evidence(context: dict[str, Any]) -> dict[str, Any]:
    """Route only direct context surfaces, preserving geometry and provenance.

    Canonical source_documents are authoritative, including their SHA. Navigation
    variants and quality metadata survive deduplication in source_refs; the first
    surface in explicit lexical order supplies the candidate's primary navigation.
    Missing quality stays null rather than claiming an exact source measurement.
    """
    documents = {row["id"]: row for row in context["source_documents"]}
    facts = [("semantic_entity", context["entity"])]
    facts.extend(("semantic_binding", row) for row in context["bindings"])
    facts.extend((row["fact_kind"], row) for row in context["properties"])
    facts.extend((row["fact_kind"], row) for row in context["relationships"])
    evidence_facts: dict[str, list[tuple[dict[str, Any], str]]] = {}
    binding_facts: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for kind, fact in facts:
        ref = {"kind": kind, "id": (
            fact.get("property_id") or fact.get("parameter_id") or fact["id"]
        )}
        evidence_ids = set(fact.get("evidence_refs", []))
        if fact.get("evidence_id"):
            evidence_ids.add(fact["evidence_id"])
        for evidence_id in evidence_ids:
            evidence_facts.setdefault(evidence_id, []).append(
                (ref, f"{kind}:evidence_ref")
            )
        binding_id = fact.get("source_binding_ref") or fact.get("source_binding_id")
        if binding_id:
            binding_facts.setdefault(binding_id, []).append(
                (ref, f"{kind}:source_binding_ref")
            )

    surfaces = [("drawing_occurrence", row) for row in context["drawing_occurrences"]]
    surfaces.extend(("evidence", row) for row in context["evidence"])
    grouped: dict[str, dict[str, Any]] = {}
    for kind, row in sorted(surfaces, key=lambda item: (
        item[0], item[1].get("occurrence_id") or item[1].get("evidence_id") or "",
        (item[1].get("binding") or {}).get("id") or "",
    )):
        document = documents.get((row.get("document") or {}).get("id"))
        page = row.get("pdf_page")
        if document is None or isinstance(page, bool) or not isinstance(page, int) or page < 1:
            continue
        bbox = row.get("bbox")
        if bbox is not None and not (
            isinstance(bbox, list) and len(bbox) == 4
            and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in bbox)
            and bbox[0] < bbox[2] and bbox[1] < bbox[3]
        ):
            # Invalid geometry must not silently become a whole-page request.
            continue
        scope = "page" if bbox is None else "region"
        navigation = row.get("navigation") or {}
        coordinate_space = row.get("coordinate_space", navigation.get("coordinate_space"))
        quality = row.get("bbox_quality", navigation.get("bbox_quality"))
        surface_key = _json_key([document["id"], page, scope, bbox])
        candidate = grouped.setdefault(surface_key, {
            "candidate_id": "vision-candidate-" + hashlib.sha256(
                surface_key.encode("utf-8")
            ).hexdigest(),
            "input_scope": scope,
            "document": dict(document),
            "pdf_page": page,
            "bbox": bbox,
            "bbox_quality": quality,
            "coordinate_space": coordinate_space,
            "navigation": navigation,
            "source_refs": {},
            "routing_reasons": set(),
        })

        def contribute(ref: dict[str, Any], reason: str) -> None:
            candidate["source_refs"][_json_key(ref)] = ref
            candidate["routing_reasons"].add(reason)

        source_ref = {
            "kind": kind,
            "id": row["occurrence_id"] if kind == "drawing_occurrence" else row["evidence_id"],
            "navigation": navigation,
            "bbox_quality": quality,
            "coordinate_space": coordinate_space,
        }
        if kind == "drawing_occurrence":
            binding = row["binding"]
            source_ref.update(
                source_kind=row["source_kind"], source_id=row["source_id"],
                binding_ref=binding["id"],
            )
            contribute(source_ref, "drawing_occurrence:" + (
                row.get("appearance_kind") or row["source_kind"]
            ))
            contribute({"kind": "semantic_binding", "id": binding["id"]},
                       "semantic_binding:drawing_occurrence")
            for ref, reason in binding_facts.get(binding["id"], []):
                contribute(ref, reason)
        else:
            contribute(source_ref, "direct_evidence")
        for ref, reason in evidence_facts.get(row.get("evidence_id"), []):
            contribute(ref, reason)

    candidates = sorted(grouped.values(), key=lambda row: (
        row["document"]["identity"] or "", row["document"]["id"], row["pdf_page"],
        row["input_scope"], tuple(row["bbox"]) if row["bbox"] is not None else (),
        row["coordinate_space"] or "", row["candidate_id"],
    ))
    for candidate in candidates:
        candidate["source_refs"] = [
            candidate["source_refs"][key] for key in sorted(candidate["source_refs"])
        ]
        candidate["routing_reasons"] = sorted(candidate["routing_reasons"])
    return {
        "capability": context["capability"],
        "status": "ok",
        "semantic_entity_id": context["entity"]["id"],
        "candidates": candidates,
        "coverage": {"candidate_count": len(candidates)},
    }
