"""Optional stdlib-only observations of existing architectural retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.query_core import QueryCore

from .baseline import execute_baseline
from .contract import CASE_FORMAT, RESULT_FORMAT, VERSION, EvaluationError, validate_suite
from .metrics import score_result

__all__ = ["CASE_FORMAT", "RESULT_FORMAT", "VERSION", "EvaluationError", "run_evaluation", "score_result"]


def run_evaluation(database: Path, suite: dict[str, Any]) -> dict[str, Any]:
    validate_suite(suite)
    results = []
    with QueryCore(database) as core:
        for case in suite["cases"]:
            retrieval = execute_baseline(core, case)
            results.append({"case": case, "retrieval": retrieval, "metrics": score_result(case, retrieval)})
    ranks = [r["metrics"]["ranking"]["reciprocal_rank"] for r in results
             if r["metrics"]["ranking"]["reciprocal_rank"] is not None]
    return {
        "format": RESULT_FORMAT, "version": VERSION, "results": results,
        "aggregate": {"ranked_case_count": len(ranks), "mrr": sum(ranks) / len(ranks) if ranks else None},
    }
