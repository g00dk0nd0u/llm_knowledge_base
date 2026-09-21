from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import fitz
import pytest

from tools.pdf_pipeline.pipeline import process_all
from tools.query_core.errors import QueryCoreError
from tools.query_core.package import package_pdf
from tools.query_core.project_pdf_adapter import build_pdf_project_database
from tools.query_core.query import QueryCore, validate_database


def _pdf(path: Path, text: str, *, width: int = 595, rotation: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=width, height=842)
    page.insert_text((72, 72), text, fontname="japan" if "日本" in text else "helv")
    page.set_rotation(rotation)
    document.save(path)
    document.close()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _pdf(tmp_path / "projects/example/source/b.pdf", "shared 共通 日本語検索")
    _pdf(tmp_path / "projects/example/source/a.pdf", "shared English project", width=400, rotation=90)
    process_all(tmp_path)
    return tmp_path


def test_project_build_search_hierarchy_determinism_and_package_boundary(
    project: Path,
) -> None:
    output = build_pdf_project_database(
        project, Path("projects/example"), project / "project.sqlite"
    )
    metadata = validate_database(output)
    assert metadata == {
        "binding_mode": "project", "created_from": "pdf-pipeline/1",
        "generator_version": "query-core/2.0", "project_id": "example",
        "schema_version": "2",
    }
    with QueryCore(output) as core:
        english = core.search_pdf_text("English")
        japanese = core.search_pdf_text("日本語")
        repeated = core.search_pdf_text("shared")
        assert english[0]["document"]["identity"].endswith("/a.pdf")
        assert japanese[0]["document"]["identity"].endswith("/b.pdf")
        assert len(repeated) == 2
        assert all(hit["bbox"] == hit["navigation"]["bbox"] for hit in english + japanese)
    with sqlite3.connect(output) as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 2
        assert all(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= 2 for table in (
            "pdf_text_blocks", "pdf_text_lines", "pdf_text_spans"
        ))
        tables = (
            "documents", "pdf_pages", "pdf_text_blocks", "pdf_text_lines",
            "pdf_text_spans", "evidence", "search_content",
        )
        first = {table: connection.execute(
            f"SELECT * FROM {table} ORDER BY {'record_id' if table == 'search_content' else 'id'}"
        ).fetchall() for table in tables}
    rebuilt = build_pdf_project_database(project, project / "projects/example", project / "again.sqlite")
    with sqlite3.connect(rebuilt) as connection:
        assert first == {table: connection.execute(
            f"SELECT * FROM {table} ORDER BY {'record_id' if table == 'search_content' else 'id'}"
        ).fetchall() for table in first}
    with pytest.raises(QueryCoreError, match="project-bound"):
        package_pdf(project / "projects/example/source/a.pdf", output, project / "bad.pdf")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda m: m.update(project_id="other"), "project_id"),
        (lambda m: m["documents"].__setitem__(1, {**m["documents"][1], "document_id": m["documents"][0]["document_id"]}), "duplicate manifest document_id"),
        (lambda m: m["documents"].__setitem__(1, {**m["documents"][1], "source_file": m["documents"][0]["source_file"]}), "duplicate manifest source_file"),
        (lambda m: m["documents"].__setitem__(1, {**m["documents"][1], "knowledge_path": m["documents"][0]["knowledge_path"]}), "duplicate manifest knowledge_path"),
        (lambda m: m["documents"][0].update(source_sha256="0" * 64), "source_sha256 mismatch"),
        (lambda m: m["documents"][0].update(page_count=99), "page_count mismatch"),
        (lambda m: m["documents"][0].update(source_file="projects/other/source/a.pdf"), "source_file must be under"),
        (lambda m: m["documents"][0].update(knowledge_path="projects/other/knowledge/a"), "knowledge_path must be under"),
    ],
)
def test_manifest_rejections(project: Path, mutation, message: str) -> None:
    path = project / "projects/example/manifest.json"
    manifest = json.loads(path.read_text())
    mutation(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(QueryCoreError, match=message):
        build_pdf_project_database(project, Path("projects/example"), project / "bad.sqlite")


def test_empty_project_and_mid_build_failure_preserves_output(tmp_path: Path, project: Path) -> None:
    empty = tmp_path / "empty/projects/empty"
    empty.mkdir(parents=True)
    (empty / "manifest.json").write_text(json.dumps({
        "project_id": "empty", "pipeline_version": "1", "documents": []
    }), encoding="utf-8")
    database = build_pdf_project_database(tmp_path / "empty", Path("projects/empty"), tmp_path / "empty.sqlite")
    with QueryCore(database) as core:
        assert core.search_pdf_text("anything") == []

    output = build_pdf_project_database(
        project, Path("projects/example"), project / "existing.sqlite"
    )
    original = output.read_bytes()
    manifest_path = project / "projects/example/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    second_document = project / manifest["documents"][1]["knowledge_path"] / "document.json"
    damaged = json.loads(second_document.read_text())
    damaged["page_count"] = 999
    second_document.write_text(json.dumps(damaged), encoding="utf-8")
    with pytest.raises(QueryCoreError):
        build_pdf_project_database(project, Path("projects/example"), output)
    assert output.read_bytes() == original
    validate_database(output)


def test_identical_bytes_remain_distinct(tmp_path: Path) -> None:
    first = tmp_path / "projects/copies/source/a.pdf"
    second = tmp_path / "projects/copies/source/archive/a-copy.pdf"
    _pdf(first, "duplicate bytes searchable")
    second.parent.mkdir(parents=True)
    second.write_bytes(first.read_bytes())
    process_all(tmp_path)
    database = build_pdf_project_database(tmp_path, Path("projects/copies"), tmp_path / "copies.sqlite")
    with QueryCore(database) as core:
        hits = core.search_pdf_text("duplicate bytes")
    assert len(hits) == 2
    assert len({hit["document"]["identity"] for hit in hits}) == 2


def test_twenty_document_project(tmp_path: Path) -> None:
    for index in range(20):
        _pdf(
            tmp_path / f"projects/scale/source/{index:02d}.pdf",
            f"bounded scale token {index:02d}",
        )
    process_all(tmp_path)
    first = build_pdf_project_database(
        tmp_path, Path("projects/scale"), tmp_path / "scale.sqlite"
    )
    second = build_pdf_project_database(
        tmp_path, Path("projects/scale"), tmp_path / "scale-again.sqlite"
    )
    with sqlite3.connect(first) as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 20
        for table in ("documents", "pdf_pages", "pdf_text_blocks", "pdf_text_lines", "pdf_text_spans", "evidence"):
            count, distinct = connection.execute(
                f"SELECT count(*), count(DISTINCT id) FROM {table}"
            ).fetchone()
            assert count == distinct
    with QueryCore(first) as core:
        assert len(core.search_pdf_text("bounded scale token")) == 20
    with sqlite3.connect(first) as left, sqlite3.connect(second) as right:
        assert left.execute("SELECT id FROM pdf_text_blocks ORDER BY id").fetchall() == right.execute(
            "SELECT id FROM pdf_text_blocks ORDER BY id"
        ).fetchall()


def test_revision_replacement_and_removed_document(project: Path) -> None:
    first = build_pdf_project_database(
        project, Path("projects/example"), project / "revision-a.sqlite"
    )
    with sqlite3.connect(first) as connection:
        before = connection.execute(
            "SELECT id,identity,source_sha256 FROM documents ORDER BY identity"
        ).fetchall()
        before_blocks = connection.execute(
            "SELECT b.id,d.identity FROM pdf_text_blocks b "
            "JOIN pdf_pages p ON p.id=b.page_id "
            "JOIN documents d ON d.id=p.document_id ORDER BY d.identity"
        ).fetchall()

    source_a = project / "projects/example/source/a.pdf"
    source_a.unlink()
    _pdf(source_a, "replacement revision text")
    process_all(project)
    revised = build_pdf_project_database(
        project, Path("projects/example"), project / "revision-b.sqlite"
    )
    with sqlite3.connect(revised) as connection:
        after = connection.execute(
            "SELECT id,identity,source_sha256 FROM documents ORDER BY identity"
        ).fetchall()
        after_blocks = connection.execute(
            "SELECT b.id,d.identity FROM pdf_text_blocks b "
            "JOIN pdf_pages p ON p.id=b.page_id "
            "JOIN documents d ON d.id=p.document_id ORDER BY d.identity"
        ).fetchall()
    assert before[0][:2] == after[0][:2]
    assert before[0][2] != after[0][2]
    assert before[1] == after[1]
    assert before_blocks[0][0] != after_blocks[0][0]
    assert before_blocks[1] == after_blocks[1]
    with QueryCore(revised) as core:
        assert core.search_pdf_text("English project") == []
        assert len(core.search_pdf_text("replacement revision")) == 1

    (project / "projects/example/source/b.pdf").unlink()
    process_all(project)
    removed = build_pdf_project_database(
        project, Path("projects/example"), project / "removed.sqlite"
    )
    with sqlite3.connect(removed) as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    with QueryCore(removed) as core:
        assert core.search_pdf_text("日本語") == []
