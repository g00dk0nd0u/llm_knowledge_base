from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.pdf_pipeline.report import ReportError, build_report


def _fixture(root: Path) -> Path:
    project = root / "projects" / "案件 with spaces"
    knowledge = project / "knowledge" / "legacy"
    pages_dir = knowledge / "pages"
    pages_dir.mkdir(parents=True)
    identity = {
        "document_id": "doc-" + "a" * 24,
        "source_file": "projects/案件 with spaces/source/設備 資料.pdf",
        "source_sha256": "b" * 64,
    }
    specifications = [
        ("extracted", 80, 0, 0, False, "completed", 1, 1, {}),
        ("minimal_text", 12, 0, 0, True, "completed", 2, 0, {"invalid_shape": 1, "overlapping_candidate": 1}),
        ("no_text", 0, 1, 200, True, "extraction_error", 1, 0, {"extraction_error": 1}),
    ]
    pages = []
    for number, spec in enumerate(specifications, 1):
        status, chars, images, drawings, vision, table_status, candidates, accepted, reasons = spec
        page = {
            "page": number,
            "structured_page": f"pages/p{number:04d}.json",
            "extraction_status": status,
            "text_char_count": chars,
            "image_count": images,
            "drawing_count": drawings,
            "vision_recommended": vision,
        }
        pages.append(page)
        sidecar = {
            **identity,
            "page": number,
            "table_extraction": {
                "status": table_status,
                "candidate_count": candidates,
                "accepted_count": accepted,
                "rejected_count": candidates - accepted,
                "rejection_counts": reasons,
            },
            "tables": [{}] * accepted,
        }
        (pages_dir / f"p{number:04d}.json").write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    document = {**identity, "project_id": project.name, "pipeline_version": "2", "page_count": 3, "pages": pages}
    (knowledge / "document.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    manifest_entry = {**identity, "knowledge_path": "projects/案件 with spaces/knowledge/legacy", "page_count": 3, "pipeline_version": "2"}
    (project / "manifest.json").write_text(json.dumps({"project_id": project.name, "pipeline_version": "2", "documents": [manifest_entry]}, ensure_ascii=False), encoding="utf-8")
    return project


def test_report_aggregates_factual_page_and_table_metadata(tmp_path: Path) -> None:
    report = build_report(_fixture(tmp_path))

    assert report["document_count"] == 1
    assert (report["page_count"], report["extracted"], report["minimal_text"], report["no_text"]) == (3, 1, 1, 1)
    assert report["pages_with_images"] == 1
    assert report["pages_with_many_drawings"] == 1
    assert report["vision_recommended_pages"] == 2
    assert report["pages_with_accepted_tables"] == 1
    assert report["accepted_table_count"] == 1
    assert report["table_candidate_count"] == 4
    assert report["rejected_table_candidate_count"] == 3
    assert report["table_extraction_error_pages"] == 1
    assert report["table_rejection_counts"]["invalid_shape"] == 1
    assert report["table_rejection_counts"]["overlapping_candidate"] == 1
    assert report["table_rejection_counts"]["extraction_error"] == 1
    assert [page["page"] for page in report["review_pages"]] == [2, 3]
    assert report["review_pages"][1]["reasons"] == ["no_text", "contains_raster_image", "many_vector_drawings", "vision_recommended", "table_candidates_rejected", "table_extraction_error"]


def test_report_cli_is_deterministic_and_stdlib_only_under_dash_s(tmp_path: Path) -> None:
    project = _fixture(tmp_path)
    repository = Path(__file__).parents[1]
    command = [sys.executable, "-S", "-m", "tools.pdf_pipeline", "report", str(project), "--format", "json"]
    environment = {**os.environ, "PYTHONPATH": str(repository)}
    first = subprocess.run(command, cwd=repository, env=environment, text=True, encoding="utf-8", capture_output=True, check=False)
    second = subprocess.run(command, cwd=repository, env=environment, text=True, encoding="utf-8", capture_output=True, check=False)

    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["documents"][0]["source_file"].endswith("設備 資料.pdf")
    probe = subprocess.run([sys.executable, "-S", "-c", "import sys; from tools.pdf_pipeline.cli import main; assert not {'fitz','jsonschema','pytest'} & set(sys.modules)"], cwd=repository, env=environment, text=True, capture_output=True, check=False)
    assert probe.returncode == 0, probe.stderr


@pytest.mark.parametrize("target", ["manifest", "document", "sidecar"])
def test_report_fails_with_affected_path_for_bad_generated_json(tmp_path: Path, target: str) -> None:
    project = _fixture(tmp_path)
    paths = {
        "manifest": project / "manifest.json",
        "document": project / "knowledge/legacy/document.json",
        "sidecar": project / "knowledge/legacy/pages/p0001.json",
    }
    paths[target].write_text("{broken", encoding="utf-8")
    with pytest.raises(ReportError, match=paths[target].name):
        build_report(project)
