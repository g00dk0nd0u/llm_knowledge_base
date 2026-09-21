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


def _offset_box_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=400, height=600)
    page.insert_text((50, 80), "project offset box")
    document.xref_set_key(page.xref, "MediaBox", "[10 20 410 620]")
    document.xref_set_key(page.xref, "CropBox", "[40 100 340 550]")
    document.xref_set_key(page.xref, "Rotate", "90")
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


@pytest.mark.parametrize(
    "knowledge_path",
    [
        "projects/example/knowledge/../other",
        "projects/example/./knowledge/foo",
        "/projects/example/knowledge/foo",
        "projects/other/knowledge/foo",
    ],
)
def test_rejects_noncanonical_knowledge_paths(
    project: Path, knowledge_path: str
) -> None:
    manifest_path = project / "projects/example/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["documents"][0]["knowledge_path"] = knowledge_path
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(QueryCoreError, match="knowledge_path must be under"):
        build_pdf_project_database(
            project, Path("projects/example"), project / "bad.sqlite"
        )


def test_rejects_missing_and_unowned_knowledge_directories(project: Path) -> None:
    manifest_path = project / "projects/example/manifest.json"
    original = json.loads(manifest_path.read_text())
    manifest = json.loads(json.dumps(original))
    manifest["documents"][0]["knowledge_path"] = (
        "projects/example/knowledge/does-not-exist"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(QueryCoreError, match="knowledge directory does not exist"):
        build_pdf_project_database(
            project, Path("projects/example"), project / "missing.sqlite"
        )

    manifest_path.write_text(json.dumps(original), encoding="utf-8")
    knowledge = project / original["documents"][0]["knowledge_path"]
    (knowledge / ".pdf-pipeline-v1").unlink()
    with pytest.raises(QueryCoreError, match="knowledge directory is unowned"):
        build_pdf_project_database(
            project, Path("projects/example"), project / "unowned.sqlite"
        )


def test_rejects_knowledge_symlink_escape(project: Path) -> None:
    manifest_path = project / "projects/example/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    outside = project / "outside"
    outside.mkdir()
    escaped = project / "projects/example/knowledge/escaped"
    escaped.symlink_to(outside, target_is_directory=True)
    manifest["documents"][0]["knowledge_path"] = (
        "projects/example/knowledge/escaped"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(QueryCoreError, match="resolves outside"):
        build_pdf_project_database(
            project, Path("projects/example"), project / "escaped.sqlite"
        )


@pytest.mark.parametrize(
    "project_directory",
    [Path("projects/example/.."), Path("example")],
)
def test_rejects_malformed_project_directory(
    project: Path, project_directory: Path
) -> None:
    with pytest.raises(QueryCoreError, match="projects/<project-id>"):
        build_pdf_project_database(
            project, project_directory, project / "bad-project.sqlite"
        )


def test_rejects_stale_actual_source_sha(project: Path) -> None:
    source = project / "projects/example/source/a.pdf"
    source.write_bytes(source.read_bytes() + b"stale source bytes")
    with pytest.raises(QueryCoreError, match="source PDF byte SHA-256 mismatch"):
        build_pdf_project_database(
            project, Path("projects/example"), project / "stale.sqlite"
        )


def test_project_preserves_nonzero_media_and_distinct_crop_boxes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "projects/boxes/source/offset.pdf"
    _offset_box_pdf(source)
    process_all(tmp_path)
    manifest = json.loads(
        (tmp_path / "projects/boxes/manifest.json").read_text()
    )
    knowledge = tmp_path / manifest["documents"][0]["knowledge_path"]
    sidecar = json.loads((knowledge / "pages/p0001.json").read_text())
    database = build_pdf_project_database(
        tmp_path, Path("projects/boxes"), tmp_path / "boxes.sqlite"
    )
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT width_points,height_points,rotation,media_x_min,media_y_min,"
            "media_x_max,media_y_max,crop_x_min,crop_y_min,crop_x_max,crop_y_max,"
            "coordinate_space FROM pdf_pages"
        ).fetchone()
    assert row == (
        sidecar["width_points"],
        sidecar["height_points"],
        sidecar["rotation"],
        *sidecar["media_box"],
        *sidecar["crop_box"],
        "pdf_points_top_left",
    )
    assert sidecar["media_box"] == [-30.0, -70.0, 370.0, 530.0]
    assert sidecar["crop_box"] == [0.0, 0.0, 300.0, 450.0]


def test_manifest_array_order_is_not_semantic(project: Path) -> None:
    ordered = build_pdf_project_database(
        project, Path("projects/example"), project / "ordered.sqlite"
    )
    manifest_path = project / "projects/example/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["documents"].reverse()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reversed_database = build_pdf_project_database(
        project, Path("projects/example"), project / "reversed.sqlite"
    )
    tables = (
        "documents",
        "pdf_pages",
        "pdf_text_blocks",
        "pdf_text_lines",
        "pdf_text_spans",
        "evidence",
        "search_content",
    )
    with sqlite3.connect(ordered) as left, sqlite3.connect(
        reversed_database
    ) as right:
        for table in tables:
            order = "record_id" if table == "search_content" else "id"
            query = f"SELECT * FROM {table} ORDER BY {order}"
            assert left.execute(query).fetchall() == right.execute(query).fetchall()
    with QueryCore(ordered) as left, QueryCore(reversed_database) as right:
        assert left.search_pdf_text("shared") == right.search_pdf_text("shared")


def test_project_build_protects_pipeline_owned_output_locations(
    project: Path,
) -> None:
    project_directory = project / "projects/example"
    source = project_directory / "source/a.pdf"
    manifest = project_directory / "manifest.json"
    manifest_data = json.loads(manifest.read_text())
    document = project / manifest_data["documents"][0]["knowledge_path"] / "document.json"
    original = {
        source: source.read_bytes(),
        manifest: manifest.read_bytes(),
        document: document.read_bytes(),
    }
    new_source = project_directory / "source/new.sqlite"
    new_knowledge = project_directory / "knowledge/new.sqlite"
    source_alias = project / "source-alias"
    knowledge_alias = project / "knowledge-alias"
    source_alias.symlink_to(project_directory / "source", target_is_directory=True)
    knowledge_alias.symlink_to(
        project_directory / "knowledge", target_is_directory=True
    )
    outputs = (
        source,
        new_source,
        manifest,
        document,
        new_knowledge,
        source_alias / "aliased.sqlite",
        knowledge_alias / "aliased.sqlite",
    )
    for output in outputs:
        with pytest.raises(
            QueryCoreError, match="protected PDF Pipeline project inputs"
        ):
            build_pdf_project_database(
                project, Path("projects/example"), output
            )

    for path, content in original.items():
        assert path.read_bytes() == content
    assert not new_source.exists()
    assert not new_knowledge.exists()
    assert not (source_alias / "aliased.sqlite").exists()
    assert not (knowledge_alias / "aliased.sqlite").exists()


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
    def identities(database: Path) -> dict[str, dict[str, object]]:
        with sqlite3.connect(database) as connection:
            documents = connection.execute(
                "SELECT id,identity,source_sha256 FROM documents ORDER BY identity"
            ).fetchall()
            result = {
                identity: {"document": (document_id, identity, source_sha256)}
                for document_id, identity, source_sha256 in documents
            }
            queries = {
                "pages": (
                    "SELECT p.id,d.identity FROM pdf_pages p "
                    "JOIN documents d ON d.id=p.document_id"
                ),
                "blocks": (
                    "SELECT b.id,d.identity FROM pdf_text_blocks b "
                    "JOIN pdf_pages p ON p.id=b.page_id "
                    "JOIN documents d ON d.id=p.document_id"
                ),
                "lines": (
                    "SELECT l.id,d.identity FROM pdf_text_lines l "
                    "JOIN pdf_text_blocks b ON b.id=l.block_id "
                    "JOIN pdf_pages p ON p.id=b.page_id "
                    "JOIN documents d ON d.id=p.document_id"
                ),
                "spans": (
                    "SELECT s.id,d.identity FROM pdf_text_spans s "
                    "JOIN pdf_text_lines l ON l.id=s.line_id "
                    "JOIN pdf_text_blocks b ON b.id=l.block_id "
                    "JOIN pdf_pages p ON p.id=b.page_id "
                    "JOIN documents d ON d.id=p.document_id"
                ),
                "evidence": (
                    "SELECT e.id,d.identity FROM evidence e "
                    "JOIN documents d ON d.id=e.document_id"
                ),
            }
            for kind, query in queries.items():
                for record_id, identity in connection.execute(query):
                    result[identity].setdefault(kind, []).append(record_id)
            return result

    first = build_pdf_project_database(
        project, Path("projects/example"), project / "revision-a.sqlite"
    )
    before = identities(first)

    source_a = project / "projects/example/source/a.pdf"
    source_a.unlink()
    _pdf(source_a, "replacement revision text")
    process_all(project)
    revised = build_pdf_project_database(
        project, Path("projects/example"), project / "revision-b.sqlite"
    )
    after = identities(revised)
    changed = "projects/example/source/a.pdf"
    unchanged = "projects/example/source/b.pdf"
    assert before[changed]["document"][:2] == after[changed]["document"][:2]
    assert before[changed]["document"][2] != after[changed]["document"][2]
    for kind in ("pages", "blocks", "lines", "spans", "evidence"):
        assert before[changed][kind] != after[changed][kind]
    assert before[unchanged] == after[unchanged]
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
