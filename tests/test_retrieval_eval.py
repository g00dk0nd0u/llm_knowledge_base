"""Evaluation mechanics over reused architectural fixtures, not quality claims."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from test_query_core_architectural_retrieval_acceptance import architectural_database, pdf_database
from test_query_core_drawing_references import _records as reference_records, _reference
from test_query_core_semantic import semantic_contract_records
from test_query_core_semantic_table_adapter import _records as table_records
from tools.query_core import QueryCore
from tools.query_core.build import build_database
from tools.retrieval_eval import CASE_FORMAT, EvaluationError, run_evaluation, score_result
from tools.retrieval_eval.baseline import execute_baseline, observe
from tools.retrieval_eval.contract import validate_suite

ROOT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).parent / "fixtures" / "retrieval_eval"


def suite(name: str) -> dict:
    return json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))


def case(operation="search_semantic_entities", arguments=None, expected=None) -> dict:
    return {"case_id": "test", "baseline": {"operation": operation, "arguments": arguments or {"query": "D-101"}},
            "expected": expected or {}}


def single(value: dict) -> dict:
    return {"format": CASE_FORMAT, "version": 1, "cases": [value]}


@pytest.fixture
def lexical_database(tmp_path: Path) -> Path:
    records = table_records(["建具番号", "備考"], [["D-105", ""]])
    records["search_content"] = [{
        "record_kind": "pdf_text_block", "record_id": row["id"], "content": row["text"],
    } for row in records["pdf_text_blocks"]]
    return build_database(records, tmp_path / "lexical.sqlite")


@pytest.fixture
def reference_database(tmp_path: Path) -> Path:
    records = reference_records(tmp_path)
    records["drawing_references"] = [
        _reference("eval-resolved", resolution_state="exact", target_view_id="view-roof", target_sheet_id="sheet-a421"),
        _reference("eval-ambiguous", resolution_state="ambiguous"),
    ]
    return build_database(records, tmp_path / "reference.sqlite")


@pytest.mark.parametrize(("fixture_name", "suite_name"), [
    ("architectural_database", "architectural"), ("pdf_database", "pdf-native"),
    ("lexical_database", "lexical"), ("reference_database", "references"),
])
def test_architectural_proof_cases(request, fixture_name, suite_name):
    database = request.getfixturevalue(fixture_name)
    result = run_evaluation(database, suite(suite_name))
    for row in result["results"]:
        metrics = row["metrics"]
        assert metrics["state"]["status"] == "pass", row["case"]["case_id"]
        assert all(check["status"] == "pass" for check in metrics["checks"])
        assert "miss" not in metrics["fields"].values()
        if metrics["evidence"]["status"] == "scored":
            assert metrics["evidence"]["expected_missing"] == []
            assert metrics["evidence"]["recall"] in {1.0, None}
    assert result["aggregate"]["mrr"] in {1.0, None}


def test_structured_context_has_no_ranking_and_measures_extra_evidence(architectural_database):
    results = run_evaluation(architectural_database, suite("architectural"))["results"]
    plan = next(r for r in results if r["case"]["case_id"] == "plan-occurrence")
    assert plan["retrieval"]["ranking"] is None
    assert plan["metrics"]["ranking"]["status"] == "not_applicable"
    assert plan["metrics"]["evidence"]["returned_count"] == 3
    assert plan["metrics"]["evidence"]["precision"] == 1 / 3
    assert len(plan["metrics"]["evidence"]["unexpected_returned"]) == 2
    schedule = next(r for r in results if r["case"]["case_id"] == "schedule-page-only")
    assert schedule["metrics"]["fields"]["bbox"] == "not_applicable"
    assert schedule["retrieval"]["payload"]["drawing_occurrences"][0]["navigation"]


@pytest.mark.parametrize("query", ["D-102", "D-10", "D-999", ""])
def test_composed_operation_never_expands_ambiguous_or_contains_results(architectural_database, monkeypatch, query):
    def forbidden(*args, **kwargs):
        pytest.fail("Context must not run without exact_unique discovery")
    monkeypatch.setattr(QueryCore, "get_architectural_evidence_context", forbidden)
    c = case("semantic_discovery_context", {"query": query, "limit": 1})
    with QueryCore(architectural_database) as core:
        expected = core.search_semantic_entities(query, limit=1)
        actual = execute_baseline(core, c)
    assert actual["payload"] == {"discovery": expected, "context": None}
    assert actual["status"] == expected["status"]


def test_exact_unique_with_contains_candidates_selects_only_exact_identity(architectural_database):
    with sqlite3.connect(architectural_database) as connection:
        connection.execute("INSERT INTO semantic_entities (id,entity_class,label,instance_or_type,resolution_state,provenance) "
                           "VALUES ('contains-only','Door','Mention D-101','instance','unresolved','test')")
    with QueryCore(architectural_database) as core:
        actual = execute_baseline(core, case("semantic_discovery_context"))
        discovery = actual["payload"]["discovery"]
        assert discovery["status"] == "exact_unique"
        assert len(discovery["candidates"]) == 2
        assert actual["payload"]["context"] == core.get_architectural_evidence_context("semantic-door-101")


def test_capability_unavailable_is_preserved(architectural_database, monkeypatch):
    with sqlite3.connect(architectural_database) as connection:
        for table in ("semantic_relationships", "semantic_properties", "semantic_bindings", "semantic_entities"):
            connection.execute(f"DROP TABLE {table}")
    monkeypatch.setattr(QueryCore, "get_architectural_evidence_context", lambda *a: pytest.fail("no expansion"))
    result = run_evaluation(architectural_database, single(case("semantic_discovery_context", expected={"status": "capability_unavailable"})))
    assert result["results"][0]["metrics"]["state"]["status"] == "pass"


def test_human_question_does_not_route_or_change_retrieval(architectural_database):
    c = case(expected={"semantic_entity_ids": ["semantic-door-101"]})
    c["human_question"] = "Use a model and search an unrelated roof instead."
    first = run_evaluation(architectural_database, single(c))["results"][0]
    c["human_question"] = "Which Door has an electric lock?"
    second = run_evaluation(architectural_database, single(c))["results"][0]
    assert first["retrieval"] == second["retrieval"]
    assert first["metrics"] == second["metrics"]


def test_ranking_cutoffs_reciprocal_rank_and_conditional_mrr(architectural_database):
    c = case(expected={"semantic_entity_ids": ["wanted"]})
    retrieval = {"status": "candidates", "observed": observe(None), "payload": {}, "ranking_limit": 10,
                 "ranking": [{"position": i, "semantic_entity_id": "wanted" if i == 6 else str(i)} for i in range(1, 11)]}
    ranking = score_result(c, retrieval)["ranking"]
    assert ranking == {"status": "scored", "hit_at_1": False, "hit_at_5": False, "hit_at_10": True, "reciprocal_rank": 1 / 6}
    retrieval["ranking_limit"] = 5
    retrieval["ranking"] = retrieval["ranking"][:5]
    ranking = score_result(c, retrieval)["ranking"]
    assert ranking["hit_at_10"] is None
    assert ranking["reciprocal_rank"] == 0
    cases = [case(expected={"semantic_entity_ids": ["semantic-door-101"]}),
             case("get_architectural_evidence_context", {"semantic_entity_id": "semantic-door-101"}, {"semantic_entity_ids": ["semantic-door-101"]}),
             case(arguments={"query": "D-999"}, expected={"semantic_entity_ids": ["semantic-door-101"]})]
    for i, c in enumerate(cases):
        c["case_id"] = str(i)
    result = run_evaluation(architectural_database, {"format": CASE_FORMAT, "version": 1, "cases": cases})
    assert result["aggregate"] == {"ranked_case_count": 2, "mrr": 0.5}


def test_wrong_drawing_and_source_bbox_cannot_pass_location_check(architectural_database):
    c = suite("architectural")["cases"][3]
    with QueryCore(architectural_database) as core:
        retrieval = execute_baseline(core, c)
    changed = deepcopy(retrieval)
    for location in changed["observed"]["evidence"]:
        location["document_identity"] = "wrong-drawing.pdf"
    changed["observed"]["document_identities"] = ["wrong-drawing.pdf"]
    metrics = score_result(c, changed)
    assert metrics["fields"]["semantic_entity_ids"] == "hit"
    assert metrics["fields"]["pdf_pages"] == "hit"
    assert metrics["fields"]["document_identities"] == "miss"
    assert metrics["fields"]["bbox"] == "miss"
    assert metrics["evidence"]["precision"] == metrics["evidence"]["recall"] == 0
    changed = deepcopy(retrieval)
    for location in changed["observed"]["evidence"]:
        if location.get("bbox"):
            location["bbox"][0] += 0.001
    assert score_result(c, changed)["fields"]["bbox"] == "miss"


def test_evidence_set_missing_noise_dedup_and_page_only():
    expected = [{"document_identity": "source.pdf", "pdf_page": p} for p in (1, 2)]
    c = case(expected={"evidence": expected})
    rows = [{"navigation": {"document": {"identity": "source.pdf", "source_filename": "source.pdf"},
                            "source_kind": "evidence", "source_id": str(p), "pdf_page": p, "can_zoom": False}}
            for p in range(1, 41)]
    retrieval = {"status": "returned", "payload": rows, "observed": observe(rows + rows), "ranking": None}
    metrics = score_result(c, retrieval)
    assert metrics["evidence"]["returned_count"] == 40
    assert metrics["evidence"]["precision"] == 2 / 40
    assert metrics["evidence"]["recall"] == 1
    assert len(metrics["evidence"]["unexpected_returned"]) == 38
    assert metrics["fields"]["bbox"] == "not_applicable"
    retrieval["observed"] = observe(rows[:1])
    assert score_result(c, retrieval)["evidence"]["expected_missing"] == expected[1:]


def test_unspecified_expectations_and_zero_denominators():
    retrieval = {"status": "no_match", "payload": None, "observed": observe(None), "ranking": None}
    metrics = score_result(case(), retrieval)
    assert set(metrics["fields"].values()) == {"not_applicable"}
    assert metrics["state"]["status"] == metrics["evidence"]["status"] == "not_applicable"
    metrics = score_result(case(expected={"evidence": [{"evidence_id": "wanted"}]}), retrieval)
    assert metrics["evidence"]["precision"] is None
    assert metrics["evidence"]["recall"] == 0
    assert metrics["evidence"]["expected_missing"] == [{"evidence_id": "wanted"}]
    metrics = score_result(case(expected={"evidence": []}), retrieval)
    assert metrics["evidence"]["precision"] is metrics["evidence"]["recall"] is None


@pytest.mark.parametrize("state", ["ambiguous", "conflict", "insufficient_evidence", "no_match"])
def test_state_scoring_does_not_reward_forced_resolution(state):
    c = case(expected={"status": state})
    retrieval = {"status": state, "payload": None, "observed": observe(None), "ranking": None}
    assert score_result(c, retrieval)["state"]["status"] == "pass"
    retrieval["status"] = "ok"
    assert score_result(c, retrieval)["state"]["status"] == "fail"


def test_checks_distinguish_property_relationship_and_missing_path(architectural_database):
    c = suite("architectural")["cases"][1]
    with QueryCore(architectural_database) as core:
        retrieval = execute_baseline(core, c)
    retrieval["payload"]["context"]["properties"] = []
    retrieval["payload"]["context"]["relationships"] = []
    assert all(r["status"] == "fail" for r in score_result(c, retrieval)["checks"])
    c["expected"]["checks"] = [{"path": ["missing"], "equals": None}]
    assert score_result(c, retrieval)["checks"] == [{"status": "fail", "reason": "path_missing"}]


def test_exact_checks_keep_boolean_and_numeric_source_values_distinct():
    c = case(expected={"checks": [{"path": [], "equals": {"flag": True}}]})
    retrieval = {"status": "returned", "payload": {"flag": 1}, "observed": observe(None), "ranking": None}
    assert score_result(c, retrieval)["checks"][0]["status"] == "fail"
    retrieval["payload"] = {"flag": True, "extra": 1}
    assert score_result(c, retrieval)["checks"][0]["status"] == "fail"


def test_direct_missing_context_and_limit_one_metrics(architectural_database):
    missing = case("get_architectural_evidence_context", {"semantic_entity_id": "missing"}, {"status": "not_found"})
    retrieval = run_evaluation(architectural_database, single(missing))["results"][0]["retrieval"]
    assert retrieval["payload"] is retrieval["query_core_status"] is retrieval["ranking"] is None
    limited = case(arguments={"query": "D-101", "limit": 1}, expected={"semantic_entity_ids": ["semantic-door-101"]})
    ranking = run_evaluation(architectural_database, single(limited))["results"][0]["metrics"]["ranking"]
    assert ranking["hit_at_1"] is True
    assert ranking["hit_at_5"] is ranking["hit_at_10"] is None


@pytest.mark.parametrize("data", [b'{"format": "a", "format": "b"}', b'\xff', b'{broken'])
def test_cli_rejects_malformed_json_without_evaluation(tmp_path, data):
    path = tmp_path / "cases.json"
    path.write_bytes(data)
    result = subprocess.run([sys.executable, "-S", "-m", "tools.retrieval_eval", "run",
                            "--database", "does-not-exist.sqlite", "--cases", str(path)],
                            cwd=ROOT, capture_output=True)
    assert result.returncode == 2
    assert result.stdout == b""
    assert json.loads(result.stderr)["error"]


@pytest.mark.parametrize("change", [
    lambda s: s.update(version=True), lambda s: s.update(version=2),
    lambda s: s.update(format="other"), lambda s: s.update(cases=[]),
    lambda s: s["cases"].append(deepcopy(s["cases"][0])),
    lambda s: s["cases"][0]["baseline"].update(operation="llm_router"),
    lambda s: s["cases"][0]["baseline"].update(arguments={"limit": 2}),
    lambda s: s["cases"][0]["baseline"]["arguments"].update(limit=True),
    lambda s: s["cases"][0]["baseline"]["arguments"].update(limit=0),
    lambda s: s["cases"][0]["baseline"]["arguments"].update(page=1),
    lambda s: s["cases"][0]["expected"].update(unknown=1),
    lambda s: s["cases"][0]["expected"].update(pdf_pages=[0]),
    lambda s: s["cases"][0]["expected"].update(evidence=[{"bbox": [1, 2, 3, 4]}]),
    lambda s: s["cases"][0]["expected"].update(evidence=[{"evidence_id": "e", "bbox": [1, 2, 3, float("nan")]}]),
    lambda s: s["cases"][0]["expected"].update(evidence=[{"evidence_id": "e", "bbox": [True, 2, 3, 4]}]),
    lambda s: s["cases"][0]["expected"].update(source_references=[{"id": "e"}]),
    lambda s: s["cases"][0]["expected"].update(checks=[{"path": [], "equals": 1, "contains": 1}]),
    lambda s: s["cases"][0]["expected"].update(checks=[{"path": [-1], "equals": 1}]),
])
def test_malformed_cases_are_rejected(change):
    value = single(case())
    change(value)
    with pytest.raises(EvaluationError):
        validate_suite(value)


def test_evaluation_is_repeatable_read_only_and_retains_native_facts(architectural_database):
    source = architectural_database.parent / "door-schedule.pdf"
    before = (architectural_database.read_bytes(), source.read_bytes())
    with sqlite3.connect(architectural_database) as connection:
        facts = tuple(connection.iterdump())
    cases = suite("architectural")
    first = run_evaluation(architectural_database, cases)
    second = run_evaluation(architectural_database, cases)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    with QueryCore(architectural_database) as core:
        raw = core.get_architectural_evidence_context("semantic-door-101")
    assert first["results"][1]["retrieval"]["payload"]["context"] == raw
    assert (architectural_database.read_bytes(), source.read_bytes()) == before
    with sqlite3.connect(architectural_database) as connection:
        assert tuple(connection.iterdump()) == facts
    assert "elapsed" not in json.dumps(first)


def test_cli_is_stdlib_only_canonical_json_and_cannot_overwrite_inputs(architectural_database, tmp_path):
    args = [sys.executable, "-S", "-m", "tools.retrieval_eval", "run", "--database", str(architectural_database),
            "--cases", str(CASES / "architectural.json")]
    first = subprocess.run(args, cwd=ROOT, check=True, capture_output=True)
    second = subprocess.run(args, cwd=ROOT, check=True, capture_output=True)
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["format"] == "architectural-retrieval-results"
    output = tmp_path / "result.json"
    subprocess.run(args + ["--output", str(output)], cwd=ROOT, check=True, capture_output=True)
    assert output.read_bytes() == first.stdout
    for target in (architectural_database, architectural_database.parent / "door-schedule.pdf", CASES / "architectural.json", output):
        before = target.read_bytes()
        failed = subprocess.run(args + ["--output", str(target)], cwd=ROOT, capture_output=True)
        assert failed.returncode == 2
        assert failed.stdout == b""
        assert json.loads(failed.stderr)["error"]
        assert target.read_bytes() == before
    imported = subprocess.run([sys.executable, "-S", "-c", "import tools.retrieval_eval; import sys; "
        "assert not any(n.startswith(('fitz','pymupdf','jsonschema','tools.pdf_pipeline','tools.query_core.fixtures',"
        "'tools.query_core.project_bundle')) for n in sys.modules)"], cwd=ROOT, capture_output=True)
    assert imported.returncode == 0, imported.stderr
