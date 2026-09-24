"""Zero-install PDF report smoke, including adverse Windows output encoding."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> None:
    repository = Path(__file__).parents[1]
    with tempfile.TemporaryDirectory(prefix="pdf report ") as temporary:
        root = Path(temporary)
        project = root / "projects" / "日本語 project with spaces"
        pages = project / "knowledge" / "legacy" / "pages"
        pages.mkdir(parents=True)
        identity = {
            "document_id": "doc-" + "a" * 24,
            "source_file": "projects/日本語 project with spaces/source/設備 資料.pdf",
            "source_sha256": "b" * 64,
        }
        page_summary = {
            "page": 1,
            "structured_page": "pages/p0001.json",
            "extraction_status": "no_text",
            "text_char_count": 0,
            "image_count": 1,
            "drawing_count": 0,
            "vision_recommended": True,
        }
        sidecar = {
            **identity,
            "page": 1,
            "table_extraction": {
                "status": "extraction_error",
                "candidate_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "rejection_counts": {"extraction_error": 1},
            },
            "tables": [],
        }
        document = {
            **identity,
            "project_id": project.name,
            "pipeline_version": "2",
            "page_count": 1,
            "pages": [page_summary],
        }
        entry = {
            **identity,
            "knowledge_path": f"projects/{project.name}/knowledge/legacy",
            "page_count": 1,
            "pipeline_version": "2",
        }
        (pages / "p0001.json").write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
        (pages.parent / "document.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        (project / "manifest.json").write_text(json.dumps({"project_id": project.name, "pipeline_version": "2", "documents": [entry]}, ensure_ascii=False), encoding="utf-8")

        command = [sys.executable, "-S", "-m", "tools.pdf_pipeline", "report", str(project), "--format", "json"]
        environment = {**os.environ, "PYTHONPATH": str(repository), "PYTHONIOENCODING": "cp1252"}
        outputs = []
        for _ in range(2):
            completed = subprocess.run(command, cwd=repository, env=environment, capture_output=True, check=False)
            assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
            outputs.append(completed.stdout.decode("utf-8"))
        assert outputs[0] == outputs[1]
        report = json.loads(outputs[0])
        assert report["documents"][0]["source_file"] == identity["source_file"]
        assert report["table_extraction_error_pages"] == 1
        assert report["table_rejection_counts"]["extraction_error"] == 1
        assert report["rejected_table_candidate_count"] == 0
        assert report["review_pages"][0]["table_extraction_status"] == "extraction_error"
    print("ok: stdlib PDF report; UTF-8 Japanese; path with spaces; extraction_error")


if __name__ == "__main__":
    main()
