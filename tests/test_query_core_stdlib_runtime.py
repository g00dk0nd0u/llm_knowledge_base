"""Subprocess proofs for the zero-install Query Core runtime boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.query_core.build import build_database


def _runtime_database(path: Path) -> Path:
    return build_database(
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
        path,
    )


def test_public_runtime_import_is_stdlib_only() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "from tools.query_core import QueryCore, QueryCoreError; "
            "import sys; "
            "assert 'fitz' not in sys.modules; "
            "assert 'jsonschema' not in sys.modules; "
            "assert 'tools.query_core.package' not in sys.modules; "
            "assert 'tools.query_core.project_bundle' not in sys.modules; "
            "print('ok')",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok\n"


def test_cli_sqlite_search_with_spaces_and_japanese_is_stdlib_only(
    tmp_path: Path,
) -> None:
    database = _runtime_database(
        tmp_path / "query runtime smoke" / "project.sqlite"
    )
    for query in ("Loading Dock", "データホール"):
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-m",
                "tools.query_core",
                "search",
                str(database),
                query,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload == [
            {
                "record_kind": "document",
                "record_id": "document-smoke",
                "content": "Loading Dock データホール",
            }
        ]
