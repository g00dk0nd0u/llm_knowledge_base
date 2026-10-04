from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
from pathlib import Path

import fitz
import pytest

from tools.pdf_pipeline.pipeline import process_all
from tools.query_core.errors import QueryCoreError
from tools.query_core.package import extract_payload, inspect_pdf, package_pdf
from tools.query_core.pdf_adapter import _sha256, build_pdf_database
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


def _set_pipeline_contract(knowledge: Path, version: str) -> None:
    document_path = knowledge / "document.json"
    document = json.loads(document_path.read_text())
    document["pipeline_version"] = version
    document_path.write_text(json.dumps(document), encoding="utf-8")
    (knowledge / ".pdf-pipeline-v1").unlink(missing_ok=True)
    (knowledge / ".pdf-pipeline-v2").unlink(missing_ok=True)
    (knowledge / f".pdf-pipeline-v{version}").touch()


def _ruled_table_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    for x in (40, 140, 240):
        page.draw_line((x, 40), (x, 160))
    for y in (40, 100, 160):
        page.draw_line((40, y), (240, y))
    page.insert_text((55, 70), "Header")
    page.insert_text((155, 70), "日本語", fontname="japan")
    page.insert_text((55, 125), "line one")
    page.insert_text((55, 145), "line two")
    document.save(path)
    document.close()


def _table_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "projects/example/source/table.pdf"
    _ruled_table_pdf(source)
    process_all(tmp_path)
    manifest = json.loads((tmp_path / "projects/example/manifest.json").read_text())
    knowledge = tmp_path / manifest["documents"][0]["knowledge_path"]
    return source, knowledge, build_pdf_database(
        tmp_path, knowledge, tmp_path / "table.sqlite"
    )


def test_table_producer_and_validators_share_neutral_tolerance() -> None:
    from tools import table_geometry
    from tools.pdf_pipeline import pipeline
    from tools.query_core import pdf_adapter, query

    assert table_geometry.TABLE_EPSILON == 0.25
    assert pipeline.TABLE_EPSILON is table_geometry.TABLE_EPSILON
    assert pdf_adapter.TABLE_EPSILON is table_geometry.TABLE_EPSILON
    assert query.TABLE_EPSILON is table_geometry.TABLE_EPSILON


def test_v2_ruled_table_persists_authoritative_cells_and_round_trips(tmp_path: Path) -> None:
    source, knowledge, database = _table_bundle(tmp_path)
    with sqlite3.connect(database) as connection:
        table = connection.execute(
            "SELECT order_index,row_count,column_count,x_min,y_min,x_max,y_max,detection_method "
            "FROM pdf_tables"
        ).fetchone()
        assert table[:3] == (0, 2, 2)
        assert table[-1] == "pymupdf_lines_strict"
        cells = connection.execute(
            "SELECT row_index,column_index,text FROM pdf_table_cells ORDER BY row_index,column_index"
        ).fetchall()
        assert cells == [(0, 0, "Header"), (0, 1, "日本語"), (1, 0, "line one\nline two"), (1, 1, "")]
        assert connection.execute("SELECT count(*) FROM pdf_table_cell_spans").fetchone()[0] == 4
        assert connection.execute(
            "SELECT count(*) FROM search_content WHERE record_kind<>'pdf_text_block'"
        ).fetchone()[0] == 0
    rebuilt = build_pdf_database(tmp_path, knowledge, tmp_path / "rebuilt-table.sqlite")
    with sqlite3.connect(database) as left, sqlite3.connect(rebuilt) as right:
        for table_name in ("pdf_tables", "pdf_table_cells", "pdf_table_cell_spans"):
            assert left.execute(f"SELECT * FROM {table_name} ORDER BY 1,2").fetchall() == right.execute(
                f"SELECT * FROM {table_name} ORDER BY 1,2"
            ).fetchall()
    enhanced = package_pdf(source, database, tmp_path / "table-enhanced.pdf")
    extracted = extract_payload(enhanced, tmp_path / "table-extracted.sqlite")
    with sqlite3.connect(extracted) as connection:
        assert connection.execute("SELECT count(*) FROM pdf_tables").fetchone()[0] == 1
    with QueryCore(extracted) as core:
        assert core.has_pdf_table_capability() is True
        table = core.list_pdf_tables()[0]
        detail = core.get_pdf_table(table["table_id"])
        assert detail is not None
        cell = core.get_pdf_table_cell(detail["cells"][0]["cell_id"])
        assert cell is not None
        assert cell["source_spans"]
        assert core.search_pdf_table_cells("Header")[0]["cell_id"] == cell["cell_id"]


def test_pipeline_table_with_subpoint_span_overflow_builds_and_validates(tmp_path: Path) -> None:
    """Reproduce a real Pipeline v2 grid rejected by stricter Query Core readers."""
    source = tmp_path / "projects/example/source/edge.pdf"
    source.parent.mkdir(parents=True)
    with fitz.open() as document:
        page = document.new_page(width=300, height=200)
        for x in (40, 140, 240):
            page.draw_line((x, 40), (x, 160))
        for y in (40, 100, 160):
            page.draw_line((40, y), (240, y))
        font = fitz.Font("helv")
        page.insert_text((55, 100.18 + font.descender * 10), "Edge", fontsize=10)
        document.save(source)
    process_all(tmp_path)
    manifest = json.loads((tmp_path / "projects/example/manifest.json").read_text())
    knowledge = tmp_path / manifest["documents"][0]["knowledge_path"]
    sidecar = json.loads((knowledge / "pages/p0001.json").read_text())
    cell = sidecar["tables"][0]["cells"][0]
    ref = cell["span_refs"][0]
    span = sidecar["text_blocks"][ref["block_index"]]["lines"][ref["line_index"]]["spans"][ref["span_index"]]
    assert 0 < span["bbox"][3] - cell["bbox"][3] < 0.25
    database = build_pdf_database(tmp_path, knowledge, tmp_path / "edge.sqlite")
    validate_database(database)
    with QueryCore(database) as core:
        result = core.search_pdf_table_cells("Edge")[0]
        assert result["text"] == "Edge"
        assert result["source_spans"][0]["bbox"] == span["bbox"]
        assert result["bbox"] == cell["bbox"]


@pytest.mark.parametrize("edge", range(4))
@pytest.mark.parametrize("overflow", [0.25, 0.2501])
def test_table_containment_tolerance_matches_pipeline_in_adapter_and_database(
    tmp_path: Path, edge: int, overflow: float
) -> None:
    _source, knowledge, database = _table_bundle(tmp_path)
    sidecar_path = knowledge / "pages/p0001.json"
    sidecar = json.loads(sidecar_path.read_text())
    cell = sidecar["tables"][0]["cells"][0]
    ref = cell["span_refs"][0]
    span = sidecar["text_blocks"][ref["block_index"]]["lines"][ref["line_index"]]["spans"][ref["span_index"]]
    span["bbox"][edge] = cell["bbox"][edge] + (-overflow if edge < 2 else overflow)
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    with sqlite3.connect(database) as connection:
        column = ("x_min", "y_min", "x_max", "y_max")[edge]
        connection.execute(
            f"UPDATE pdf_text_spans SET {column}=? WHERE text='Header'",
            (span["bbox"][edge],),
        )
    if overflow <= 0.25:
        rebuilt = build_pdf_database(tmp_path, knowledge, tmp_path / "edge-rebuilt.sqlite")
        validate_database(rebuilt)
        validate_database(database)
    else:
        with pytest.raises(QueryCoreError, match="span_ref lies outside cell bbox"):
            build_pdf_database(tmp_path, knowledge, tmp_path / "invalid-edge.sqlite")
        with pytest.raises(QueryCoreError, match="span is not authoritative"):
            validate_database(database)


@pytest.mark.parametrize("damage", ["span_ref", "cell_text"])
def test_v2_table_rejects_malformed_source_traceability(
    tmp_path: Path, damage: str
) -> None:
    _source, knowledge, _database = _table_bundle(tmp_path)
    sidecar_path = knowledge / "pages/p0001.json"
    sidecar = json.loads(sidecar_path.read_text())
    cell = sidecar["tables"][0]["cells"][0]
    if damage == "span_ref":
        cell["span_refs"][0]["span_index"] = 999
        message = "span_ref does not resolve"
    else:
        cell["text"] = "not authoritative"
        message = "does not match authoritative source spans"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    with pytest.raises(QueryCoreError, match=message):
        build_pdf_database(tmp_path, knowledge, tmp_path / "invalid.sqlite")


def test_pdf_table_read_api_preserves_structure_traceability_and_search(
    tmp_path: Path,
) -> None:
    _source, _knowledge, database = _table_bundle(tmp_path)
    with sqlite3.connect(database) as connection:
        table_id = connection.execute("SELECT id FROM pdf_tables").fetchone()[0]
        cell_ids = dict(
            connection.execute(
                "SELECT text,id FROM pdf_table_cells ORDER BY row_index,column_index"
            )
        )
        ordinary_counts = tuple(
            connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("search_content", "search_fts")
        )

    with QueryCore(database) as core:
        assert core.has_pdf_table_capability() is True
        listed = core.list_pdf_tables()
        assert [item["table_id"] for item in listed] == [table_id]
        assert core.list_pdf_tables(document_identity="unknown") == []
        assert core.list_pdf_tables(pdf_page=2) == []
        assert (
            core.list_pdf_tables(
                document_identity="projects/example/source/table.pdf", pdf_page=1
            )
            == listed
        )
        with pytest.raises(QueryCoreError, match="positive integer"):
            core.list_pdf_tables(pdf_page=True)

        table = core.get_pdf_table(table_id)
        assert table is not None
        assert table["bbox"] == table["navigation"]["bbox"]
        assert table["navigation"]["source_kind"] == "pdf_table"
        assert [
            (cell["row_index"], cell["column_index"], cell["text"])
            for cell in table["cells"]
        ] == [
            (0, 0, "Header"),
            (0, 1, "日本語"),
            (1, 0, "line one\nline two"),
            (1, 1, ""),
        ]
        assert table["cells"][-1]["source_spans"] == []
        japanese = core.get_pdf_table_cell(cell_ids["日本語"])
        assert japanese is not None
        assert japanese["table"]["table_id"] == table_id
        assert japanese["navigation"]["source_kind"] == "pdf_table_cell"
        assert japanese["bbox"] == japanese["navigation"]["bbox"]
        span = japanese["source_spans"][0]
        assert span["span_id"]
        assert span["source_ref"] == {
            "block_index": 0,
            "line_index": 1,
            "span_index": 0,
        }
        assert span["text"] == "日本語"
        assert span["provenance"] == "embedded_pdf_text"
        assert len(span["bbox"]) == 4

        assert [hit["text"] for hit in core.search_pdf_table_cells("日本")] == [
            "日本語"
        ]
        assert [hit["text"] for hit in core.search_pdf_table_cells("one\nline")] == [
            "line one\nline two"
        ]
        assert [hit["text"] for hit in core.search_pdf_table_cells("e", limit=1)] == [
            "Header"
        ]
        traced_sql = []
        core.connection.set_trace_callback(traced_sql.append)
        assert [hit["text"] for hit in core.search_pdf_table_cells("e", limit=2)] == [
            "Header",
            "line one\nline two",
        ]
        assert any(
            "FROM pdf_table_cells" in statement and "LIMIT 2" in statement
            for statement in traced_sql
        )
        assert core.search_pdf_table_cells("  ") == []
        with pytest.raises(QueryCoreError, match="positive integer"):
            core.search_pdf_table_cells("e", limit=-1)
        assert core.get_pdf_table("unknown") is None
        assert core.get_pdf_table_cell("unknown") is None
        assert len(core.search_pdf_text("Header")) == 1

    with sqlite3.connect(database) as connection:
        assert (
            tuple(
                connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                for table in ("search_content", "search_fts")
            )
            == ordinary_counts
        )


def test_pdf_table_read_api_distinguishes_legacy_and_empty_capability(
    tmp_path: Path,
) -> None:
    _source, _knowledge, database = _table_bundle(tmp_path)
    empty = tmp_path / "empty.sqlite"
    shutil.copyfile(database, empty)
    with sqlite3.connect(empty) as connection:
        connection.execute("DELETE FROM pdf_table_cell_spans")
        connection.execute("DELETE FROM pdf_table_cells")
        connection.execute("DELETE FROM pdf_tables")
    with QueryCore(empty) as core:
        assert core.has_pdf_table_capability() is True
        assert core.list_pdf_tables() == []
        assert core.search_pdf_table_cells("anything") == []

    legacy = tmp_path / "legacy.sqlite"
    shutil.copyfile(database, legacy)
    with sqlite3.connect(legacy) as connection:
        connection.execute("DROP TABLE pdf_table_cell_spans")
        connection.execute("DROP TABLE pdf_table_cells")
        connection.execute("DROP TABLE pdf_tables")
    with QueryCore(legacy) as core:
        assert core.has_pdf_table_capability() is False
        assert core.list_pdf_tables() == []
        assert core.get_pdf_table("unknown") is None
        assert core.get_pdf_table_cell("unknown") is None
        assert core.search_pdf_table_cells("anything") == []

def test_validate_rejects_cross_cell_span_relation(tmp_path: Path) -> None:
    _source, _knowledge, database = _table_bundle(tmp_path)
    with sqlite3.connect(database) as connection:
        cells = connection.execute(
            "SELECT id FROM pdf_table_cells WHERE text IN ('Header','日本語') ORDER BY text"
        ).fetchall()
        header_cell, japanese_cell = cells[0][0], cells[1][0]
        other_span = connection.execute(
            "SELECT span_id FROM pdf_table_cell_spans WHERE cell_id=?", (japanese_cell,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE pdf_table_cell_spans SET span_id=? WHERE cell_id=?",
            (other_span, header_cell),
        )
    with pytest.raises(QueryCoreError, match="not authoritative for its page and bbox"):
        validate_database(database)


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


def test_synthetic_v2_build_rejects_malformed_table_contract(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, source, knowledge = processed_pdf
    _set_pipeline_contract(knowledge, "2")
    sidecar_path = knowledge / "pages/p0001.json"
    sidecar = json.loads(sidecar_path.read_text())
    sidecar.update(table_extraction={"status": "future"}, tables=[{"dummy": True}])
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    with pytest.raises(QueryCoreError, match="table extraction contract"):
        build_pdf_database(root, knowledge, tmp_path / "v2.sqlite")


@pytest.mark.parametrize(
    ("document_version", "markers"),
    [
        ("2", ("1",)),
        ("1", ("2",)),
        ("1", ("1", "2")),
        ("1", ("1", "3")),
        ("3", ("1",)),
    ],
)
def test_rejects_pipeline_version_marker_mismatch(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path,
    document_version: str, markers: tuple[str, ...],
) -> None:
    root, _source, knowledge = processed_pdf
    _set_pipeline_contract(knowledge, document_version)
    for marker in (knowledge / ".pdf-pipeline-v1", knowledge / ".pdf-pipeline-v2"):
        marker.unlink(missing_ok=True)
    for version in markers:
        (knowledge / f".pdf-pipeline-v{version}").touch()
    with pytest.raises(QueryCoreError, match="pipeline_version|marker|unowned"):
        build_pdf_database(root, knowledge, tmp_path / "bad-version.sqlite")


def test_single_pdf_build_protects_source_and_knowledge_outputs(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, source, knowledge = processed_pdf
    source_bytes = source.read_bytes()
    document = knowledge / "document.json"
    document_bytes = document.read_bytes()

    with pytest.raises(QueryCoreError, match="protected PDF Pipeline project inputs"):
        build_pdf_database(root, knowledge, source)
    assert source.read_bytes() == source_bytes

    with pytest.raises(QueryCoreError, match="protected PDF Pipeline project inputs"):
        build_pdf_database(root, knowledge, document)
    assert document.read_bytes() == document_bytes

    safe = build_pdf_database(root, knowledge, tmp_path / "safe.sqlite")
    assert validate_database(safe)["binding_mode"] == "single_document"


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


def _drop_pdf_text_tables(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE pdf_table_cell_spans")
    connection.execute("DROP TABLE pdf_table_cells")
    connection.execute("DROP TABLE pdf_tables")
    connection.execute("DROP TABLE pdf_text_spans")
    connection.execute("DROP TABLE pdf_text_lines")
    connection.execute("DROP TABLE pdf_text_blocks")


def test_optional_pdf_capability_preserves_legacy_v2_shapes(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, _source, knowledge = processed_pdf
    current = build_pdf_database(root, knowledge, tmp_path / "current.sqlite")

    pre_phase_1b = tmp_path / "pre-phase-1b.sqlite"
    shutil.copyfile(current, pre_phase_1b)
    with sqlite3.connect(pre_phase_1b) as connection:
        _drop_pdf_text_tables(connection)
        connection.execute("DROP TABLE pdf_pages")
    assert validate_database(pre_phase_1b)["schema_version"] == "2"

    phase_0 = tmp_path / "phase-0.sqlite"
    shutil.copyfile(current, phase_0)
    with sqlite3.connect(phase_0) as connection:
        _drop_pdf_text_tables(connection)
        for column in (
            "media_x_min", "media_y_min", "media_x_max", "media_y_max",
            "crop_x_min", "crop_y_min", "crop_x_max", "crop_y_max",
            "coordinate_space",
        ):
            connection.execute(f"ALTER TABLE pdf_pages DROP COLUMN {column}")
    assert validate_database(phase_0)["schema_version"] == "2"


def test_legacy_v2_without_pdf_table_capability_validates(tmp_path: Path) -> None:
    _source, _knowledge, database = _table_bundle(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE pdf_table_cell_spans")
        connection.execute("DROP TABLE pdf_table_cells")
        connection.execute("DROP TABLE pdf_tables")
    assert validate_database(database)["schema_version"] == "2"


@pytest.mark.parametrize("missing", ["pdf_table_cell_spans", "pdf_table_cells"])
def test_partial_pdf_table_capability_is_rejected(
    tmp_path: Path, missing: str
) -> None:
    _source, _knowledge, database = _table_bundle(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(f"DROP TABLE {missing}")
    with pytest.raises(QueryCoreError, match="incomplete PDF table capability"):
        validate_database(database)


def test_pipeline_v1_persists_no_table_rows(tmp_path: Path) -> None:
    _source, knowledge, _database = _table_bundle(tmp_path)
    _set_pipeline_contract(knowledge, "1")
    database = build_pdf_database(tmp_path, knowledge, tmp_path / "v1.sqlite")
    assert validate_database(database)["created_from"] == "pdf-pipeline/1"
    with sqlite3.connect(database) as connection:
        assert {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("pdf_tables", "pdf_table_cells", "pdf_table_cell_spans")
        } == {"pdf_tables": 0, "pdf_table_cells": 0, "pdf_table_cell_spans": 0}


def test_optional_pdf_capability_rejects_partial_schema(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, _source, knowledge = processed_pdf
    current = build_pdf_database(root, knowledge, tmp_path / "current.sqlite")
    partial = tmp_path / "partial.sqlite"
    shutil.copyfile(current, partial)
    with sqlite3.connect(partial) as connection:
        connection.execute("DROP TABLE pdf_text_spans")
    with pytest.raises(QueryCoreError, match="incomplete PDF text capability"):
        validate_database(partial)

    old_pages = tmp_path / "old-pages.sqlite"
    shutil.copyfile(current, old_pages)
    with sqlite3.connect(old_pages) as connection:
        connection.execute("ALTER TABLE pdf_pages DROP COLUMN media_x_min")
    with pytest.raises(QueryCoreError, match="pdf_pages missing columns: media_x_min"):
        validate_database(old_pages)

    missing_pages = tmp_path / "missing-pages.sqlite"
    shutil.copyfile(current, missing_pages)
    with sqlite3.connect(missing_pages) as connection:
        connection.execute("DROP TABLE pdf_pages")
    with pytest.raises(
        QueryCoreError, match="PDF text capability; missing table: pdf_pages"
    ):
        validate_database(missing_pages)


def test_adapter_cross_checks_project_id_from_source_path(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, _source, knowledge = processed_pdf
    assert build_pdf_database(root, knowledge, tmp_path / "valid.sqlite").exists()
    document_path = knowledge / "document.json"
    document = json.loads(document_path.read_text())
    document["project_id"] = "other"
    document_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(QueryCoreError, match="project_id does not match source_file"):
        build_pdf_database(root, knowledge, tmp_path / "mismatch.sqlite")


@pytest.mark.parametrize(
    "source_file",
    [
        "projects/example/source/../sample.pdf",
        "projects/example/./source/sample.pdf",
        "/projects/example/source/sample.pdf",
        "projects/example/not-source/sample.pdf",
        "projects/example/source",
    ],
)
def test_adapter_rejects_noncanonical_source_paths(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path, source_file: str
) -> None:
    root, _source, knowledge = processed_pdf
    document_path = knowledge / "document.json"
    document = json.loads(document_path.read_text())
    document["source_file"] = source_file
    document_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(QueryCoreError, match="source_file must match"):
        build_pdf_database(root, knowledge, tmp_path / "bad-path.sqlite")


def test_streaming_sha256_matches_one_shot_hash(tmp_path: Path) -> None:
    content = (bytes(range(256)) * 12_289) + b"bounded-memory-tail"
    source = tmp_path / "large.pdf"
    source.write_bytes(content)
    assert _sha256(source) == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    ("primitive", "value"),
    [("block", None), ("line", "inferred"), ("span", "pipeline_guess")],
)
def test_adapter_rejects_missing_or_unexpected_provenance(
    processed_pdf: tuple[Path, Path, Path],
    tmp_path: Path,
    primitive: str,
    value: str | None,
) -> None:
    root, _source, knowledge = processed_pdf
    sidecar = knowledge / "pages/p0001.json"
    data = json.loads(sidecar.read_text())
    target = data["text_blocks"][0]
    if primitive in {"line", "span"}:
        target = target["lines"][0]
    if primitive == "span":
        target = target["spans"][0]
    if value is None:
        target.pop("provenance")
    else:
        target["provenance"] = value
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(QueryCoreError, match=f"invalid {primitive} provenance"):
        build_pdf_database(root, knowledge, tmp_path / "bad.sqlite")


@pytest.mark.parametrize("bbox_kind", ["media_box", "crop_box", "block", "line", "span"])
@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_adapter_rejects_non_finite_bboxes(
    processed_pdf: tuple[Path, Path, Path],
    tmp_path: Path,
    bbox_kind: str,
    non_finite: float,
) -> None:
    root, _source, knowledge = processed_pdf
    sidecar = knowledge / "pages/p0001.json"
    data = json.loads(sidecar.read_text())
    if bbox_kind in {"media_box", "crop_box"}:
        data[bbox_kind][0] = non_finite
        document_path = knowledge / "document.json"
        summary = json.loads(document_path.read_text())
        summary["pages"][0][bbox_kind][0] = non_finite
        document_path.write_text(json.dumps(summary), encoding="utf-8")
    else:
        target = data["text_blocks"][0]
        if bbox_kind in {"line", "span"}:
            target = target["lines"][0]
        if bbox_kind == "span":
            target = target["spans"][0]
        target["bbox"][0] = non_finite
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(QueryCoreError, match="invalid .* bbox"):
        build_pdf_database(root, knowledge, tmp_path / "bad.sqlite")


@pytest.mark.parametrize("rotation", [0, 90])
def test_page_boxes_survive_pipeline_and_query_core(
    tmp_path: Path, rotation: int
) -> None:
    source = tmp_path / "projects/boxes/source/offset.pdf"
    source.parent.mkdir(parents=True)
    document = fitz.open()
    page = document.new_page(width=400, height=600)
    page.insert_text((50, 80), "Offset box text")
    document.xref_set_key(page.xref, "MediaBox", "[10 20 410 620]")
    document.xref_set_key(page.xref, "CropBox", "[40 100 340 550]")
    document.xref_set_key(page.xref, "Rotate", str(rotation))
    document.save(source)
    document.close()
    process_all(tmp_path)
    manifest = json.loads((tmp_path / "projects/boxes/manifest.json").read_text())
    knowledge = tmp_path / manifest["documents"][0]["knowledge_path"]
    sidecar = json.loads((knowledge / "pages/p0001.json").read_text())
    database = build_pdf_database(tmp_path, knowledge, tmp_path / "boxes.sqlite")
    row = sqlite3.connect(database).execute(
        "SELECT width_points,height_points,rotation,media_x_min,media_y_min,"
        "media_x_max,media_y_max,crop_x_min,crop_y_min,crop_x_max,crop_y_max,"
        "coordinate_space FROM pdf_pages"
    ).fetchone()
    assert row == (
        sidecar["width_points"], sidecar["height_points"], rotation,
        *sidecar["media_box"], *sidecar["crop_box"], "pdf_points_top_left",
    )
    assert sidecar["media_box"] == [-30.0, -70.0, 370.0, 530.0]
    assert sidecar["crop_box"] == [0.0, 0.0, 300.0, 450.0]
