"""Subprocess proofs for the zero-install Query Core runtime boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tools.query_core.build import build_database


def test_neutral_table_geometry_imports_no_producer_or_consumer() -> None:
    result = subprocess.run(
        [
            sys.executable, "-S", "-c",
            "import tools.table_geometry; import sys; "
            "assert tools.table_geometry.TABLE_EPSILON == 0.25; "
            "assert not any(name.startswith(('tools.pdf_pipeline', 'tools.query_core', "
            "'fitz', 'pymupdf', 'jsonschema')) for name in sys.modules)",
        ],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


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
            "assert not any(name == 'tools.pdf_pipeline' or "
            "name.startswith('tools.pdf_pipeline.') for name in sys.modules); "
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
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "cp1252"
    for query in ("Loading Dock", "データホール"):
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                "from tools.query_core.cli import main; import sys; "
                "assert main(sys.argv[1:]) == 0; "
                "assert not any(name == 'tools.pdf_pipeline' or "
                "name.startswith('tools.pdf_pipeline.') for name in sys.modules); "
                "assert not any(name.startswith(('fitz', 'pymupdf', 'jsonschema')) "
                "for name in sys.modules)",
                "search",
                str(database),
                query,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
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
