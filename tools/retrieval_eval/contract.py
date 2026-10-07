"""Versioned, JSON-compatible evaluation inputs; no query interpretation."""

from __future__ import annotations

import math
from typing import Any

CASE_FORMAT = "architectural-retrieval-cases"
RESULT_FORMAT = "architectural-retrieval-results"
VERSION = 1

# Arguments are the existing public Query Core signatures, not a routing schema.
OPERATIONS = {
    "search_semantic_entities": ({"query": str}, {"entity_class": str, "limit": int}),
    "semantic_discovery_context": ({"query": str}, {"entity_class": str, "limit": int}),
    "get_architectural_evidence_context": ({"semantic_entity_id": str}, {}),
    "get_semantic_spatial_context": ({"entity_id": str}, {}),
    "get_drawing_reference": ({"reference_id": str}, {}),
    "search_text": ({"query": str}, {"limit": int}),
    "search_pdf_text": ({"query": str}, {"limit": int}),
}
FIELD_EXPECTATIONS = {
    "semantic_entity_ids": "semantic_entity_id",
    "document_identities": "document_identity",
    "sheet_ids": "sheet_id",
    "sheet_numbers": "sheet_number",
    "view_ids": "view_id",
    "pdf_pages": "pdf_page",
    "evidence_ids": "evidence_id",
    "source_references": "source_reference",
}
LOCATION_FIELDS = {
    "document_id", "document_identity", "sheet_id", "sheet_number", "view_id",
    "pdf_page", "bbox", "coordinate_space", "evidence_id", "source_reference",
}


class EvaluationError(ValueError):
    """An invalid case or unsupported explicit operation."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationError(message)


def _object(value: Any, required: set[str], optional: set[str], path: str) -> None:
    _require(isinstance(value, dict), f"{path}: expected object")
    _require(required <= value.keys(), f"{path}: missing fields {sorted(required - value.keys())}")
    _require(value.keys() <= required | optional,
             f"{path}: unknown fields {sorted(value.keys() - required - optional)}")


def _string(value: Any, path: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), f"{path}: expected nonempty string")


def _json(value: Any) -> None:
    if isinstance(value, dict):
        _require(all(isinstance(k, str) for k in value), "JSON object keys must be strings")
        for child in value.values():
            _json(child)
    elif isinstance(value, list):
        for child in value:
            _json(child)
    else:
        _require(value is None or type(value) in {str, bool, int, float}, "expected JSON value")
        if isinstance(value, float):
            _require(math.isfinite(value), "nonfinite JSON number")


def _reference(value: Any, path: str) -> None:
    _object(value, {"kind", "id"}, set(), path)
    for key in ("kind", "id"):
        _string(value[key], f"{path}.{key}")


def _page(value: Any, path: str) -> None:
    _require(type(value) is int and value > 0, f"{path}: expected one-based page")


def validate_case(case: Any) -> None:
    _json(case)
    _object(case, {"case_id", "baseline", "expected"},
            {"human_question", "task_category", "notes"}, "case")
    _string(case["case_id"], "case_id")
    for key in ("human_question", "task_category", "notes"):
        if key in case:
            _string(case[key], key)
    baseline = case["baseline"]
    _object(baseline, {"operation", "arguments"}, set(), "baseline")
    _string(baseline["operation"], "baseline.operation")
    _require(baseline["operation"] in OPERATIONS, "unsupported baseline operation")
    required, optional = OPERATIONS[baseline["operation"]]
    args = baseline["arguments"]
    _object(args, set(required), set(optional), "baseline.arguments")
    for key, value in args.items():
        if key == "entity_class" and value is None:
            continue
        _require(type(value) is (required | optional)[key], f"baseline.arguments.{key}: invalid type")
        if key == "limit":
            _require(value > 0, "baseline.arguments.limit: expected positive integer")
    expected = case["expected"]
    _object(expected, set(), set(FIELD_EXPECTATIONS) | {"status", "evidence", "checks"}, "expected")
    if "status" in expected:
        _string(expected["status"], "expected.status")
    for field in FIELD_EXPECTATIONS:
        if field not in expected:
            continue
        _require(isinstance(expected[field], list), f"expected.{field}: expected list")
        for value in expected[field]:
            if field == "source_references":
                _reference(value, field)
            elif field == "pdf_pages":
                _page(value, field)
            else:
                _string(value, field)
    if "evidence" in expected:
        _require(isinstance(expected["evidence"], list), "expected.evidence: expected list")
    for region in expected.get("evidence", []):
        _object(region, set(), LOCATION_FIELDS, "expected.evidence[]")
        _require("evidence_id" in region or "source_reference" in region
                 or {"document_identity", "pdf_page"} <= region.keys(),
                 "evidence needs a source ID or document identity + page")
        for key, value in region.items():
            if key == "source_reference":
                _reference(value, key)
            elif key == "pdf_page":
                _page(value, key)
            elif key != "bbox":
                _string(value, key)
        bbox = region.get("bbox")
        if bbox is not None:
            _require(isinstance(bbox, list) and len(bbox) == 4
                     and all(type(v) in {int, float} for v in bbox), "bbox: expected four coordinates")
            _require(bbox[0] <= bbox[2] and bbox[1] <= bbox[3], "bbox: inverted coordinates")
            _require({"document_identity", "pdf_page", "coordinate_space"} <= region.keys(),
                     "exact bbox requires document identity, page and coordinate_space")
    if "checks" in expected:
        _require(isinstance(expected["checks"], list), "expected.checks: expected list")
    for check in expected.get("checks", []):
        _object(check, {"path"}, {"equals", "contains"}, "expected.checks[]")
        _require(("equals" in check) != ("contains" in check), "check needs exactly one of equals/contains")
        path = check["path"]
        _require(isinstance(path, list) and all(
            isinstance(p, str) or (type(p) is int and p >= 0) for p in path), "check path: invalid JSON path")


def validate_suite(suite: Any) -> None:
    _json(suite)
    _object(suite, {"format", "version", "cases"}, set(), "suite")
    _require(suite["format"] == CASE_FORMAT, "unsupported case format")
    _require(type(suite["version"]) is int and suite["version"] == VERSION, "unsupported case version")
    _require(isinstance(suite["cases"], list) and bool(suite["cases"]), "cases: expected nonempty list")
    ids = set()
    for case in suite["cases"]:
        validate_case(case)
        _require(case["case_id"] not in ids, "duplicate case_id")
        ids.add(case["case_id"])
