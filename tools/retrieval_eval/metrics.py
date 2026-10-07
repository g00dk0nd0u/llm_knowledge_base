"""Deterministic scoring of observed retrieval, independent of execution."""

from __future__ import annotations

from typing import Any

from .contract import FIELD_EXPECTATIONS, validate_case

NOT_APPLICABLE = "not_applicable"


def _matches(actual: Any, expected: Any) -> bool:
    # Object selectors are conjunctive partial records. Scalars/lists are exact.
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _matches(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return _equal(actual, expected)
    if type(actual) is bool or type(expected) is bool:
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def _equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(
            _equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _equal(a, e) for a, e in zip(actual, expected, strict=True)
        )
    return _matches(actual, expected)


def _check(payload: Any, check: dict[str, Any]) -> dict[str, Any]:
    value = payload
    try:
        for key in check["path"]:
            if isinstance(key, str) and isinstance(value, dict):
                value = value[key]
            elif type(key) is int and isinstance(value, list):
                value = value[key]
            else:
                return {"status": "fail", "reason": "path_missing"}
    except (KeyError, IndexError):
        return {"status": "fail", "reason": "path_missing"}
    if "equals" in check:
        passed = _equal(value, check["equals"])
    else:
        passed = isinstance(value, list) and any(_matches(row, check["contains"]) for row in value)
    return {"status": "pass" if passed else "fail", "actual": value}


def score_result(case: dict[str, Any], retrieval: dict[str, Any]) -> dict[str, Any]:
    """Future adapters can supply this same observed-result shape for scoring."""
    validate_case(case)
    expected = case["expected"]
    observed = retrieval["observed"]
    regions = expected.get("evidence", [])
    actual_regions = observed["evidence"]
    fields = {}
    for field, location_field in FIELD_EXPECTATIONS.items():
        targets = expected.get(field, []) + [r[location_field] for r in regions if location_field in r]
        actual = observed.get(field, []) + [r[location_field] for r in actual_regions if location_field in r]
        fields[field] = ("hit" if any(value in actual for value in targets) else "miss") if targets else NOT_APPLICABLE
    bbox_targets = [r for r in regions if isinstance(r.get("bbox"), list)]
    fields["bbox"] = ("hit" if any(any(_matches(a, r) for a in actual_regions)
                                  for r in bbox_targets) else "miss") if bbox_targets else NOT_APPLICABLE
    evidence = {"status": NOT_APPLICABLE}
    if "evidence" in expected:
        found = [r for r in regions if any(_matches(a, r) for a in actual_regions)]
        missing = [r for r in regions if not any(_matches(a, r) for a in actual_regions)]
        unexpected = [a for a in actual_regions if not any(_matches(a, r) for r in regions)]
        evidence = {
            "status": "scored", "expected_retrieved": found, "expected_missing": missing,
            "unexpected_returned": unexpected,
            "expected_count": len(regions), "returned_count": len(actual_regions),
            "precision": (len(actual_regions) - len(unexpected)) / len(actual_regions) if actual_regions else None,
            "recall": len(found) / len(regions) if regions else None,
            "coverage": len(found) / len(regions) if regions else None,
        }
    ranked = retrieval["ranking"]
    targets = expected.get("semantic_entity_ids", []) if ranked and "semantic_entity_id" in ranked[0] else expected.get("source_references", [])
    # Empty semantic searches still have a genuine ordered candidate contract.
    if ranked == [] and case["baseline"]["operation"] in {"search_semantic_entities", "semantic_discovery_context"}:
        targets = expected.get("semantic_entity_ids", [])
    ranking = {"status": NOT_APPLICABLE, "hit_at_1": None, "hit_at_5": None,
               "hit_at_10": None, "reciprocal_rank": None}
    if ranked is not None and targets:
        relevant = [row["position"] for row in ranked
                    if row.get("semantic_entity_id", row.get("source_reference")) in targets]
        first = min(relevant) if relevant else None
        ranking.update(status="scored", reciprocal_rank=1 / first if first else 0.0)
        for k in (1, 5, 10):
            if retrieval["ranking_limit"] >= k:
                ranking[f"hit_at_{k}"] = first is not None and first <= k
    return {
        "state": ({"status": "pass" if retrieval["status"] == expected["status"] else "fail",
                   "expected": expected["status"], "actual": retrieval["status"]}
                  if "status" in expected else {"status": NOT_APPLICABLE}),
        "fields": fields, "evidence": evidence, "ranking": ranking,
        "checks": [_check(retrieval["payload"], check) for check in expected.get("checks", [])],
    }
