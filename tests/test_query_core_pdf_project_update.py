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


def _canonical_tables(database: Path) -> dict[str, list[tuple]]:
    ordering = {
        "metadata": "key",
        "documents": "id",
        "pdf_pages": "id",
        "pdf_text_blocks": "id",
        "pdf_text_lines": "id",
        "pdf_text_spans": "id",
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
    source_bytes, manifest_bytes = source.read_bytes(), manifest.read_bytes()
    with pytest.raises(QueryCoreError, match="protected PDF Pipeline project inputs"):
        update_pdf_project_database(root, Path("projects/example"), source)
    with pytest.raises(QueryCoreError, match="protected PDF Pipeline project inputs"):
        update_pdf_project_database(root, Path("projects/example"), manifest)
    assert source.read_bytes() == source_bytes
    assert manifest.read_bytes() == manifest_bytes
