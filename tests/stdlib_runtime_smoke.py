"""Cross-platform Query Core smoke runnable without site-packages or pytest."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.query_core.build import build_database


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
    },
    root / "project.sqlite",
)

direct = subprocess.run(
    [
        sys.executable,
        "-S",
        "-c",
        "from tools.query_core import QueryCore, QueryCoreError; print('ok')",
    ],
    check=True,
    capture_output=True,
    text=True,
    encoding="utf-8",
)
assert direct.stdout == "ok\n"

expected = [
    {
        "record_kind": "document",
        "record_id": "document-smoke",
        "content": "Loading Dock データホール",
    }
]
for query in ("Loading Dock", "データホール"):
    search = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "tools.query_core",
            "search",
            str(database),
            query,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert json.loads(search.stdout) == expected

print("ok: direct import; path with spaces; English and Japanese SQLite search")
