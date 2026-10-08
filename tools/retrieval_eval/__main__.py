from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.query_core import QueryCoreError

from . import EvaluationError, run_evaluation


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.retrieval_eval")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--database", type=Path, required=True)
    run.add_argument("--cases", type=Path, required=True)
    run.add_argument("--output", type=Path, help="new JSON file; existing files are never overwritten")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        suite = json.loads(args.cases.read_text(encoding="utf-8"), object_pairs_hook=_object)
        result = run_evaluation(args.database, suite)
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        if args.output:
            # Exclusive creation also protects database/PDF/case files, including
            # symlinks and hard links, if an output path points at an input.
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        print(encoded, end="")
    except (QueryCoreError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
