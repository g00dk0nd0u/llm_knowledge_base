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


def test_synthetic_v2_build_ignores_additive_tables_and_packages(
    processed_pdf: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, source, knowledge = processed_pdf
    _set_pipeline_contract(knowledge, "2")
    sidecar_path = knowledge / "pages/p0001.json"
    sidecar = json.loads(sidecar_path.read_text())
    sidecar.update(table_extraction={"status": "future"}, tables=[{"dummy": True}])
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    database = build_pdf_database(root, knowledge, tmp_path / "v2.sqlite")
    assert validate_database(database)["created_from"] == "pdf-pipeline/2"
    enhanced = package_pdf(source, database, tmp_path / "v2-enhanced.pdf")
    assert inspect_pdf(enhanced)["schema_version"] == 2


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
