from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import fitz
import pytest

from tools.pdf_pipeline.pipeline import process_all
from tools.query_core.errors import QueryCoreError
from tools.query_core.package import extract_payload, inspect_pdf, package_pdf
from tools.query_core.pdf_adapter import build_pdf_database
from tools.query_core.query import QueryCore, validate_database


@pytest.fixture
def processed_pdf(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "projects/example/source/sample.pdf"
    source.parent.mkdir(parents=True)
    document = fitz.open()
    first = document.new_page(width=595, height=842)
    first.insert_text((72, 72), "English phrase across spans: ")
    first.insert_text((210, 72), "Query Core")
    first.insert_text((72, 100), "Second line and block")
    second = document.new_page(width=300, height=500)
    second.insert_text((40, 80), "日本語検索テスト", fontname="japan")
    second.set_rotation(90)
    document.new_page(width=612, height=792)
    document.save(source)
    document.close()
    process_all(tmp_path)
    manifest = json.loads((tmp_path / "projects/example/manifest.json").read_text())
    knowledge = tmp_path / manifest["documents"][0]["knowledge_path"]
    return tmp_path, source, knowledge


def test_single_pdf_build_search_navigation_determinism_and_package(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, source, knowledge = processed_pdf
    database = build_pdf_database(root, knowledge, tmp_path / "query.sqlite")
    metadata = validate_database(database)
    assert metadata["schema_version"] == "2"
    assert metadata["generator_version"] == "query-core/2.0"
    assert metadata["binding_mode"] == "single_document"

    with QueryCore(database) as core:
        english = core.search_pdf_text("Query Core")
        japanese = core.search_pdf_text("日本語検索")
        assert len(english) == len(japanese) == 1
        assert english[0]["pdf_page"] == 1
        assert japanese[0]["pdf_page"] == 2
        assert english[0]["bbox"] == english[0]["navigation"]["bbox"]
        assert english[0]["navigation"]["coordinate_space"] == "pdf_points_top_left"
        assert english[0]["navigation"]["can_zoom"] is True
        assert (
            english[0]["document"]["identity"] == "projects/example/source/sample.pdf"
        )

    connection = sqlite3.connect(database)
    counts = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("pdf_text_blocks", "pdf_text_lines", "pdf_text_spans")
    }
    assert all(counts.values())
    assert (
        connection.execute(
            "SELECT count(*) FROM search_content WHERE record_kind='pdf_text_block'"
        ).fetchone()[0]
        == counts["pdf_text_blocks"]
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM pdf_text_blocks b JOIN pdf_pages p ON p.id=b.page_id WHERE p.page_number=3"
        ).fetchone()[0]
        == 0
    )
    page = connection.execute(
        "SELECT width_points,height_points,rotation,media_x_min,crop_x_min,coordinate_space "
        "FROM pdf_pages WHERE page_number=2"
    ).fetchone()
    assert page[:3] == (300.0, 500.0, 90)
    assert page[-1] == "pdf_points_top_left"
    identities = {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        for table in (
            "pdf_pages",
            "pdf_text_blocks",
            "pdf_text_lines",
            "pdf_text_spans",
            "evidence",
        )
    }
    connection.close()

    rebuilt = build_pdf_database(root, knowledge, tmp_path / "rebuilt.sqlite")
    other = sqlite3.connect(rebuilt)
    assert identities == {
        table: other.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        for table in identities
    }
    other.close()

    enhanced = package_pdf(source, database, tmp_path / "enhanced.pdf")
    assert source.read_bytes() != enhanced.read_bytes()
    assert inspect_pdf(enhanced)["schema_version"] == 2
    assert (
        extract_payload(enhanced, tmp_path / "extracted.sqlite").read_bytes()
        == database.read_bytes()
    )


@pytest.mark.parametrize("damage", ["sidecar_sha", "sidecar_identity", "source_sha"])
def test_adapter_rejects_stale_or_tampered_inputs(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path, damage: str
) -> None:
    root, source, knowledge = processed_pdf
    if damage == "source_sha":
        source.write_bytes(source.read_bytes() + b"tampered")
    else:
        sidecar = knowledge / "pages/p0001.json"
        data = json.loads(sidecar.read_text())
        data["source_sha256" if damage == "sidecar_sha" else "document_id"] = "0" * 64
        sidecar.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(QueryCoreError):
        build_pdf_database(root, knowledge, tmp_path / "bad.sqlite")
