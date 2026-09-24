"""Project Query Bundle smoke runnable with the Python standard library alone."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.query_core.build import GENERATOR_VERSION, SCHEMA_VERSION, build_database
from tools.query_core.project_bundle import BUNDLE_FORMAT, inspect_pdf_project_bundle


def _run(arguments: list[str]) -> bytes:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(arguments, capture_output=True, env=environment, check=False)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


with tempfile.TemporaryDirectory(prefix="query bundle smoke ") as temporary:
    bundle = Path(temporary) / "受領 bundle with spaces"
    source = bundle / "sources" / "設備資料 日本語.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4\n% deterministic test bytes\n%%EOF\n")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    project_id = "日本語-project"
    identity = f"projects/{project_id}/source/{source.name}"
    document_id = "doc-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    database = build_database(
        {
            "project_id": project_id,
            "created_from": "pdf-pipeline/2",
            "binding_mode": "project",
            "documents": [{
                "id": document_id, "identity": identity,
                "title": "Legacy Technical PDF", "source_filename": source.name,
                "source_sha256": source_sha,
            }],
            "pdf_pages": [{
                "id": "page-1", "document_id": document_id, "page_number": 1,
                "width_points": 612, "height_points": 792, "rotation": 0,
                "provenance": "embedded_pdf_text",
            }],
            "search_content": [
                {"record_kind": "document", "record_id": document_id,
                 "content": "English fire resistance 日本語検索"},
            ],
        },
        bundle / "project.sqlite",
    )
    manifest = {
        "bundle_format": BUNDLE_FORMAT, "project_id": project_id,
        "binding_mode": "project", "created_from": "pdf-pipeline/2",
        "schema_version": SCHEMA_VERSION, "generator_version": GENERATOR_VERSION,
        "pipeline_version": "2",
        "database": {"path": "project.sqlite", "sha256": hashlib.sha256(database.read_bytes()).hexdigest()},
        "documents": [{
            "document_id": document_id, "identity": identity,
            "source_sha256": source_sha, "page_count": 1,
            "bundle_path": f"sources/{source.name}",
            "page_map": {"kind": "one_to_one", "source_page_start": 1,
                         "query_core_page_start": 1, "page_count": 1},
        }],
    }
    (bundle / "bundle.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert inspect_pdf_project_bundle(bundle) == manifest
    inspect_output = _run([sys.executable, "-S", "-m", "tools.query_core",
                           "inspect-pdf-project-bundle", str(bundle)])
    assert json.loads(inspect_output.decode("utf-8")) == manifest
    for query in ("English fire resistance", "日本語検索"):
        bundle_output = _run([sys.executable, "-S", "-m", "tools.query_core",
                              "search", str(bundle), query])
        direct_output = _run([sys.executable, "-S", "-m", "tools.query_core",
                              "search", str(database), query])
        assert bundle_output == direct_output
        assert bundle_output == _run([sys.executable, "-S", "-m", "tools.query_core",
                                      "search", str(bundle), query])
        assert json.loads(bundle_output.decode("utf-8"))[0]["record_id"] == document_id
    forbidden = {"fitz", "jsonschema", "pytest", "tzdata", "tools.query_core.package"}
    assert forbidden.isdisjoint(sys.modules)
    released_database = database.with_name("released-project.sqlite")
    database.rename(released_database)
    released_database.rename(database)

print(
    "ok: Project Query Bundle inspect/search; spaces; Japanese UTF-8; "
    "stdlib-only; database released"
)
