"""Cross-platform Query Core smoke runnable without site-packages or pytest."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.query_core.build import build_database


def run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run a smoke child and preserve all diagnostics on failure."""
    result = subprocess.run(command, check=False, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(
            "child process failed\n"
            f"return code: {result.returncode}\n"
            f"command arguments: {command!r}\n"
            f"stdout:\n{result.stdout.decode('utf-8', errors='replace')}\n"
            f"stderr:\n{result.stderr.decode('utf-8', errors='replace')}"
        )
    return result


root = Path("artifacts") / "query runtime smoke"
database = build_database(
    {
        "project_id": "runtime-smoke",
        "created_from": "stdlib runtime smoke",
        "binding_mode": "project",
        "documents": [
            {
                "id": "document-smoke",
                "identity": "runtime-smoke-document",
                "title": "Runtime Smoke",
                "source_filename": "runtime-smoke.pdf",
                "source_sha256": "0" * 64,
            }
        ],
        "search_content": [
            {
                "record_kind": "document",
                "record_id": "document-smoke",
                "content": "Loading Dock データホール",
            }
        ],
        "semantic_entities": [{
            "id": "semantic-smoke", "entity_class": "Door", "label": "D-001",
            "instance_or_type": "instance", "resolution_state": "exact",
            "provenance": "stdlib-smoke",
        }],
        "semantic_bindings": [{
            "id": "binding-smoke", "semantic_entity_id": "semantic-smoke",
            "source_kind": "element", "source_id": None,
            "resolution_state": "unresolved", "provenance": "stdlib-smoke",
        }],
    },
    root / "project.sqlite",
)

direct = run(
    [
        sys.executable,
        "-S",
        "-c",
        "from tools.query_core import QueryCore, QueryCoreError; print('ok')",
    ],
)
assert direct.stdout.decode("utf-8").splitlines() == ["ok"]

expected = [
    {
        "record_kind": "document",
        "record_id": "document-smoke",
        "content": "Loading Dock データホール",
    }
]
for query in ("Loading Dock", "データホール"):
    search = run(
        [
            sys.executable,
            "-S",
            "-m",
            "tools.query_core",
            "search",
            str(database),
            query,
        ],
    )
    assert json.loads(search.stdout.decode("utf-8")) == expected

context = run([
    sys.executable, "-S", "-m", "tools.query_core", "architectural-context",
    str(database), "semantic-smoke",
])
assert json.loads(context.stdout.decode("utf-8"))["entity"]["label"] == "D-001"

print("ok: direct import; path with spaces; English and Japanese SQLite search")
