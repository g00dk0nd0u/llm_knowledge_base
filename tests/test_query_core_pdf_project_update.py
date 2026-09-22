from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import fitz
import pytest

from tools.pdf_pipeline.pipeline import process_all
from tools.query_core.errors import QueryCoreError
from tools.query_core.project_pdf_adapter import build_pdf_project_database
from tools.query_core.project_pdf_update import update_pdf_project_database
from tools.query_core.query import QueryCore, validate_database


def _pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontname="japan" if "日本" in text else "helv")
    document.save(path)
    document.close()


def _table_pdf(path: Path, prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    for x in (40, 140, 240):
        page.draw_line((x, 40), (x, 160))
    for y in (40, 100, 160):
        page.draw_line((40, y), (240, y))
    for point, suffix in (((55, 70), "1"), ((155, 70), "2"), ((55, 130), "3"), ((155, 130), "4")):
        page.insert_text(point, f"{prefix}-{suffix}")
    document.save(path)
    document.close()


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    _pdf(tmp_path / "projects/example/source/a.pdf", "alpha old searchable")
    _pdf(tmp_path / "projects/example/source/b.pdf", "beta removable 日本語")
    process_all(tmp_path)
    database = build_pdf_project_database(
        tmp_path, Path("projects/example"), tmp_path / "project.sqlite"
    )
    return tmp_path, database


def _manifest(root: Path) -> dict:
    return json.loads((root / "projects/example/manifest.json").read_text())


def _knowledge_for(root: Path, identity: str) -> Path:
    entry = next(
        item for item in _manifest(root)["documents"] if item["source_file"] == identity
    )
    return root / entry["knowledge_path"]


def _set_project_pipeline_contract(root: Path, version: str) -> None:
    manifest_path = root / "projects/example/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["pipeline_version"] = version
    for entry in manifest["documents"]:
        entry["pipeline_version"] = version
        knowledge = root / entry["knowledge_path"]
        document_path = knowledge / "document.json"
        document = json.loads(document_path.read_text())
        document["pipeline_version"] = version
        document_path.write_text(json.dumps(document), encoding="utf-8")
        (knowledge / ".pdf-pipeline-v1").unlink(missing_ok=True)
        (knowledge / ".pdf-pipeline-v2").unlink(missing_ok=True)
        (knowledge / f".pdf-pipeline-v{version}").touch()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _canonical_tables(database: Path) -> dict[str, list[tuple]]:
    ordering = {
        "metadata": "key",
        "documents": "id",
        "pdf_pages": "id",
        "pdf_text_blocks": "id",
        "pdf_text_lines": "id",
        "pdf_text_spans": "id",
        "pdf_tables": "id",
        "pdf_table_cells": "id",
        "pdf_table_cell_spans": "cell_id,order_index",
        "evidence": "id",
        "search_content": "record_kind,record_id",
    }
    result: dict[str, list[tuple]] = {}
    with sqlite3.connect(database) as connection:
        for table, order_by in ordering.items():
            columns = [
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
                if row[1] != "rowid"
            ]
            result[table] = connection.execute(
                f"SELECT {','.join(columns)} FROM {table} ORDER BY {order_by}"
            ).fetchall()
    return result


def _document_table_rows(database: Path, identity: str) -> dict[str, list[tuple]]:
    with sqlite3.connect(database) as connection:
        document_id = connection.execute(
            "SELECT id FROM documents WHERE identity=?", (identity,)
        ).fetchone()[0]
        return {
            "pdf_tables": connection.execute(
                "SELECT t.* FROM pdf_tables t JOIN pdf_pages p ON p.id=t.page_id "
                "WHERE p.document_id=? ORDER BY t.id", (document_id,)
            ).fetchall(),
            "pdf_table_cells": connection.execute(
                "SELECT c.* FROM pdf_table_cells c JOIN pdf_tables t ON t.id=c.table_id "
                "JOIN pdf_pages p ON p.id=t.page_id WHERE p.document_id=? ORDER BY c.id",
                (document_id,),
            ).fetchall(),
            "pdf_table_cell_spans": connection.execute(
                "SELECT l.* FROM pdf_table_cell_spans l JOIN pdf_table_cells c ON c.id=l.cell_id "
                "JOIN pdf_tables t ON t.id=c.table_id JOIN pdf_pages p ON p.id=t.page_id "
                "WHERE p.document_id=? ORDER BY l.cell_id,l.order_index", (document_id,)
            ).fetchall(),
        }


def _legacy_table_project(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "projects/example/source"
    _table_pdf(source / "a.pdf", "legacy-a")
    process_all(tmp_path)
    database = build_pdf_project_database(
        tmp_path, Path("projects/example"), tmp_path / "legacy-project.sqlite"
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE pdf_table_cell_spans")
        connection.execute("DROP TABLE pdf_table_cells")
        connection.execute("DROP TABLE pdf_tables")
    assert validate_database(database)["schema_version"] == "2"
    return source, database


def test_legacy_without_table_capability_noop_is_byte_preserving(
    tmp_path: Path,
) -> None:
    _source, database = _legacy_table_project(tmp_path)
    original = database.read_bytes()
    result = update_pdf_project_database(
        tmp_path, Path("projects/example"), database
    )
    assert result.added == result.changed == result.removed == ()
    assert result.unchanged == ("projects/example/source/a.pdf",)
    assert database.read_bytes() == original
    with sqlite3.connect(database) as connection:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert names.isdisjoint(
        {"pdf_tables", "pdf_table_cells", "pdf_table_cell_spans"}
    )


@pytest.mark.parametrize("mutation", ["added", "removed"])
def test_legacy_without_table_capability_mutation_requires_full_rebuild(
    tmp_path: Path, mutation: str
) -> None:
    source, database = _legacy_table_project(tmp_path)
    if mutation == "added":
        _table_pdf(source / "b.pdf", "added-b")
    else:
        (source / "a.pdf").unlink()
    process_all(tmp_path)
    original = database.read_bytes()
    with pytest.raises(QueryCoreError, match="FULL REBUILD REQUIRED") as error:
        update_pdf_project_database(tmp_path, Path("projects/example"), database)
    assert str(error.value) == (
        "FULL REBUILD REQUIRED: mutating incremental updates require the "
        "complete PDF table capability"
    )
    assert database.read_bytes() == original
    with sqlite3.connect(database) as connection:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert names.isdisjoint(
        {"pdf_tables", "pdf_table_cells", "pdf_table_cell_spans"}
    )


def test_real_tables_incremental_update_matches_full_rebuild(tmp_path: Path) -> None:
    source = tmp_path / "projects/example/source"
    for name, prefix in (("a.pdf", "old-a"), ("b.pdf", "stable-b"), ("d.pdf", "removed-d")):
        _table_pdf(source / name, prefix)
    process_all(tmp_path)
    database = build_pdf_project_database(
        tmp_path, Path("projects/example"), tmp_path / "project-tables.sqlite"
    )
    with sqlite3.connect(database) as connection:
        assert all(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0
                   for table in ("pdf_tables", "pdf_table_cells", "pdf_table_cell_spans"))
    stable_before = _document_table_rows(database, "projects/example/source/b.pdf")
    old_a = _document_table_rows(database, "projects/example/source/a.pdf")
    old_d = _document_table_rows(database, "projects/example/source/d.pdf")

    (source / "a.pdf").unlink()
    _table_pdf(source / "a.pdf", "new-a")
    _table_pdf(source / "c.pdf", "added-c")
    (source / "d.pdf").unlink()
    process_all(tmp_path)
    result = update_pdf_project_database(tmp_path, Path("projects/example"), database)
    assert result.changed == ("projects/example/source/a.pdf",)
    assert result.unchanged == ("projects/example/source/b.pdf",)
    assert result.added == ("projects/example/source/c.pdf",)
    assert result.removed == ("projects/example/source/d.pdf",)
    assert _document_table_rows(database, "projects/example/source/b.pdf") == stable_before
    new_a = _document_table_rows(database, "projects/example/source/a.pdf")
    new_c = _document_table_rows(database, "projects/example/source/c.pdf")
    assert all(new_a.values()) and all(new_c.values())
    with sqlite3.connect(database) as connection:
        current_ids = {row[0] for row in connection.execute("SELECT id FROM pdf_tables")}
        assert current_ids.isdisjoint(row[0] for row in old_a["pdf_tables"])
        assert current_ids.isdisjoint(row[0] for row in old_d["pdf_tables"])
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    fresh = build_pdf_project_database(
        tmp_path, Path("projects/example"), tmp_path / "fresh-tables.sqlite"
    )
    incremental = _canonical_tables(database)
    rebuilt = _canonical_tables(fresh)
    for table in ("pdf_tables", "pdf_table_cells", "pdf_table_cell_spans"):
        assert incremental[table] == rebuilt[table]


def test_noop_reuses_previous_rows_and_ignores_unchanged_sidecar_damage(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    original = database.read_bytes()
    first = update_pdf_project_database(root, Path("projects/example"), database)
    assert first.added == first.changed == first.removed == ()
    assert first.unchanged == (
        "projects/example/source/a.pdf",
        "projects/example/source/b.pdf",
    )
    assert database.read_bytes() == original

    sidecar = (
        _knowledge_for(root, "projects/example/source/a.pdf")
        / "pages/p0001.json"
    )
    sidecar.write_text("{broken sidecar", encoding="utf-8")
    second = update_pdf_project_database(root, Path("projects/example"), database)
    assert second.unchanged == first.unchanged
    assert database.read_bytes() == original
    validate_database(database)


def test_unchanged_source_byte_change_requires_pipeline_refresh(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    original = database.read_bytes()
    source = root / "projects/example/source/a.pdf"
    source.write_bytes(source.read_bytes() + b"stale bytes")
    with pytest.raises(QueryCoreError, match="source PDF byte SHA-256 mismatch"):
        update_pdf_project_database(root, Path("projects/example"), database)
    assert database.read_bytes() == original


def test_stale_manifest_sha_is_rejected_without_replacing_database(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    original = database.read_bytes()
    manifest_path = root / "projects/example/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["documents"][0]["source_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(QueryCoreError, match="source_sha256 mismatch"):
        update_pdf_project_database(root, Path("projects/example"), database)
    assert database.read_bytes() == original


def test_added_changed_removed_update_matches_fresh_full_rebuild(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    (root / "projects/example/source/a.pdf").unlink()
    _pdf(root / "projects/example/source/a.pdf", "alpha replacement searchable")
    (root / "projects/example/source/b.pdf").unlink()
    _pdf(root / "projects/example/source/c.pdf", "gamma added 日本語追加")
    process_all(root)

    result = update_pdf_project_database(root, Path("projects/example"), database)
    assert result.added == ("projects/example/source/c.pdf",)
    assert result.changed == ("projects/example/source/a.pdf",)
    assert result.removed == ("projects/example/source/b.pdf",)
    assert result.unchanged == ()

    fresh = build_pdf_project_database(
        root, Path("projects/example"), root / "fresh.sqlite"
    )
    assert _canonical_tables(database) == _canonical_tables(fresh)

    with QueryCore(database) as incremental, QueryCore(fresh) as rebuilt:
        assert incremental.search_pdf_text("alpha old") == []
        assert incremental.search_pdf_text("beta removable") == []
        replacement = incremental.search_pdf_text("alpha replacement")
        japanese = incremental.search_pdf_text("日本語追加")
        assert len(replacement) == 1
        assert len(japanese) == 1
        assert replacement == rebuilt.search_pdf_text("alpha replacement")
        assert japanese == rebuilt.search_pdf_text("日本語追加")
        assert replacement[0]["navigation"] == rebuilt.search_pdf_text(
            "alpha replacement"
        )[0]["navigation"]


def test_rename_is_removed_plus_added_not_identity_reuse(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    source = root / "projects/example/source/a.pdf"
    renamed = root / "projects/example/source/archive/a-renamed.pdf"
    renamed.parent.mkdir(parents=True)
    renamed.write_bytes(source.read_bytes())
    source.unlink()
    process_all(root)
    result = update_pdf_project_database(root, Path("projects/example"), database)
    assert result.added == ("projects/example/source/archive/a-renamed.pdf",)
    assert result.removed == ("projects/example/source/a.pdf",)
    assert result.changed == ()
    assert result.unchanged == ("projects/example/source/b.pdf",)


def test_changed_sidecar_failure_preserves_previous_database_bytes(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    original = database.read_bytes()
    source = root / "projects/example/source/a.pdf"
    source.unlink()
    _pdf(source, "alpha changed before corruption")
    process_all(root)
    sidecar = (
        _knowledge_for(root, "projects/example/source/a.pdf")
        / "pages/p0001.json"
    )
    sidecar.write_text("{broken changed sidecar", encoding="utf-8")

    with pytest.raises(QueryCoreError):
        update_pdf_project_database(root, Path("projects/example"), database)
    assert database.read_bytes() == original
    validate_database(database)


def test_mid_update_failure_after_valid_delta_preserves_previous_database_bytes(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    original = database.read_bytes()
    _pdf(root / "projects/example/source/c.pdf", "valid delta before failure")
    _pdf(root / "projects/example/source/d.pdf", "broken delta")
    process_all(root)
    broken = (
        _knowledge_for(root, "projects/example/source/d.pdf")
        / "pages/p0001.json"
    )
    broken.write_text("{broken delta sidecar", encoding="utf-8")

    with pytest.raises(QueryCoreError):
        update_pdf_project_database(root, Path("projects/example"), database)
    assert database.read_bytes() == original
    validate_database(database)


def test_all_removed_produces_valid_empty_project(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    for source in (root / "projects/example/source").glob("*.pdf"):
        source.unlink()
    process_all(root)
    result = update_pdf_project_database(root, Path("projects/example"), database)
    assert result.removed == (
        "projects/example/source/a.pdf",
        "projects/example/source/b.pdf",
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    with QueryCore(database) as core:
        assert core.search_pdf_text("alpha") == []
        assert core.search_pdf_text("日本語") == []


def test_empty_to_populated_update_matches_fresh_full_rebuild(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects/empty"
    (project_dir / "source").mkdir(parents=True)
    (project_dir / "knowledge").mkdir()
    (project_dir / "manifest.json").write_text(
        json.dumps(
            {"project_id": "empty", "pipeline_version": "2", "documents": []}
        ),
        encoding="utf-8",
    )
    database = build_pdf_project_database(
        tmp_path, Path("projects/empty"), tmp_path / "empty.sqlite"
    )
    _pdf(project_dir / "source/first.pdf", "first populated token")
    process_all(tmp_path)
    result = update_pdf_project_database(
        tmp_path, Path("projects/empty"), database
    )
    assert result.added == ("projects/empty/source/first.pdf",)
    fresh = build_pdf_project_database(
        tmp_path, Path("projects/empty"), tmp_path / "fresh-empty.sqlite"
    )
    assert _canonical_tables(database) == _canonical_tables(fresh)


def test_added_identical_bytes_remain_distinct_logical_documents(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    source = root / "projects/example/source/a.pdf"
    copy = root / "projects/example/source/archive/a-copy.pdf"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(source.read_bytes())
    process_all(root)
    result = update_pdf_project_database(root, Path("projects/example"), database)
    assert result.added == ("projects/example/source/archive/a-copy.pdf",)
    with QueryCore(database) as core:
        hits = core.search_pdf_text("alpha old searchable")
    assert len(hits) == 2
    assert len({hit["document"]["identity"] for hit in hits}) == 2


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("project_id", "other", "project_id mismatch"),
        ("generator_version", "query-core/999", "generator_version mismatch"),
        ("created_from", "other-adapter/1", "created_from mismatch"),
    ],
)
def test_rejects_ineligible_incremental_base_metadata(
    project: tuple[Path, Path], key: str, value: str, message: str
) -> None:
    root, database = project
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE metadata SET value=? WHERE key=?", (value, key))
        connection.commit()
    with pytest.raises(QueryCoreError, match=message):
        update_pdf_project_database(root, Path("projects/example"), database)


def test_rejects_non_pdf_native_incremental_base(project: tuple[Path, Path]) -> None:
    root, database = project
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO source_models "
            "(id,role,title,model_identity_kind,model_identity) VALUES (?,?,?,?,?)",
            ("model-1", "host", "host", "path", "host.rvt"),
        )
        connection.commit()
    with pytest.raises(QueryCoreError, match="source_models contains rows"):
        update_pdf_project_database(root, Path("projects/example"), database)


def test_rejects_noncanonical_cached_search_content(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE search_content SET content='tampered cache' WHERE rowid=("
            "SELECT min(rowid) FROM search_content)"
        )
        connection.commit()
    with pytest.raises(QueryCoreError, match="cached search_content"):
        update_pdf_project_database(root, Path("projects/example"), database)


def test_delta_update_detects_fts_external_content_corruption(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO search_fts(search_fts) VALUES('delete-all')")
        connection.commit()
    corrupted = database.read_bytes()
    _pdf(root / "projects/example/source/c.pdf", "trigger delta after corrupt fts")
    process_all(root)
    with pytest.raises(QueryCoreError, match="FTS5 search index"):
        update_pdf_project_database(root, Path("projects/example"), database)
    assert database.read_bytes() == corrupted


def test_rejects_missing_incremental_base(tmp_path: Path) -> None:
    _pdf(tmp_path / "projects/example/source/a.pdf", "one")
    process_all(tmp_path)
    with pytest.raises(QueryCoreError, match="does not exist"):
        update_pdf_project_database(
            tmp_path, Path("projects/example"), tmp_path / "missing.sqlite"
        )


def test_update_keeps_pipeline_owned_inputs_protected(
    project: tuple[Path, Path],
) -> None:
    root, _database = project
    source = root / "projects/example/source/a.pdf"
    manifest = root / "projects/example/manifest.json"
    document = _knowledge_for(root, "projects/example/source/a.pdf") / "document.json"
    source_bytes = source.read_bytes()
    manifest_bytes = manifest.read_bytes()
    document_bytes = document.read_bytes()
    for protected in (source, manifest, document):
        with pytest.raises(
            QueryCoreError, match="protected PDF Pipeline project inputs"
        ):
            update_pdf_project_database(root, Path("projects/example"), protected)
    assert source.read_bytes() == source_bytes
    assert manifest.read_bytes() == manifest_bytes
    assert document.read_bytes() == document_bytes


def test_pipeline_generation_is_incremental_compatibility_boundary(
    project: tuple[Path, Path],
) -> None:
    root, v2_database = project
    v2_result = update_pdf_project_database(
        root, Path("projects/example"), v2_database
    )
    assert v2_result.added == v2_result.changed == v2_result.removed == ()

    _set_project_pipeline_contract(root, "1")
    with pytest.raises(QueryCoreError, match="FULL REBUILD REQUIRED"):
        update_pdf_project_database(root, Path("projects/example"), v2_database)

    v1_database = build_pdf_project_database(
        root, Path("projects/example"), root / "v1-project.sqlite"
    )
    result = update_pdf_project_database(root, Path("projects/example"), v1_database)
    assert result.added == result.changed == result.removed == ()
    assert validate_database(v1_database)["created_from"] == "pdf-pipeline/1"

    _set_project_pipeline_contract(root, "2")
    with pytest.raises(QueryCoreError, match="FULL REBUILD REQUIRED"):
        update_pdf_project_database(root, Path("projects/example"), v1_database)
