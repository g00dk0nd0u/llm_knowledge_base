from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import fitz
import jsonschema
import pytest

from tools.pdf_pipeline import pipeline
from tools.pdf_pipeline.pipeline import PipelineError, document_id, process_all, render_pages
from tools.pdf_pipeline.report import build_report


def make_pdf(path: Path, texts: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    document = fitz.open()
    for text in texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.set_metadata({"title": "Synthetic title", "author": "Test suite"})
    document.save(path)
    document.close()


def make_ruled_table(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page()
    shape = page.new_shape()
    for x in (50, 150, 250, 350):
        shape.draw_line((x, 50), (x, 200))
    for y in (50, 100, 150, 200):
        shape.draw_line((50, y), (350, y))
    shape.finish()
    shape.commit()
    values = (("Header", "日本語", ""), ("A", "line one\nline two", "C"), ("D", "E", "F"))
    for row, cells in enumerate(values):
        for column, value in enumerate(cells):
            if value:
                page.insert_text((60 + column * 100, 75 + row * 50), value, fontname="japan" if value == "日本語" else "helv")
    document.save(path)
    document.close()


def make_large_ruled_table(path: Path, rows: int = 10, columns: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=600, height=500)
    left, top, cell_width, cell_height = 40, 40, 50, 35
    shape = page.new_shape()
    for column in range(columns + 1):
        x = left + column * cell_width
        shape.draw_line((x, top), (x, top + rows * cell_height))
    for row in range(rows + 1):
        y = top + row * cell_height
        shape.draw_line((left, y), (left + columns * cell_width, y))
    shape.finish()
    shape.commit()
    for row in range(rows):
        for column in range(columns):
            page.insert_text(
                (left + column * cell_width + 5, top + row * cell_height + 20),
                f"{row}:{column}",
                fontsize=7,
            )
    document.save(path)
    document.close()


def draw_grid(page: fitz.Page, bbox: tuple[float, float, float, float]) -> None:
    left, top, right, bottom = bbox
    middle_x = (left + right) / 2
    middle_y = (top + bottom) / 2
    shape = page.new_shape()
    for x in (left, middle_x, right):
        shape.draw_line((x, top), (x, bottom))
    for y in (top, middle_y, bottom):
        shape.draw_line((left, y), (right, y))
    shape.finish()
    shape.commit()


def convert_output_to_legacy_v1(repository: Path, knowledge: Path) -> None:
    manifest_path = repository / "projects/example/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["pipeline_version"] = "1"
    manifest["documents"][0]["pipeline_version"] = "1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    document_path = knowledge / "document.json"
    document = json.loads(document_path.read_text())
    document["pipeline_version"] = "1"
    document_path.write_text(json.dumps(document), encoding="utf-8")
    (knowledge / ".pdf-pipeline-v2").rename(knowledge / ".pdf-pipeline-v1")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "projects" / "example" / "source").mkdir(parents=True)
    (tmp_path / "schema").mkdir()
    source_schema = Path(__file__).parents[1] / "schema"
    for name in ("document.schema.json", "manifest.schema.json", "pdf_page.schema.json"):
        (tmp_path / "schema" / name).write_bytes((source_schema / name).read_bytes())
    return tmp_path


def load_outputs(repository: Path) -> tuple[dict, dict, Path]:
    manifest = json.loads((repository / "projects/example/manifest.json").read_text())
    knowledge = repository / manifest["documents"][0]["knowledge_path"]
    document = json.loads((knowledge / "document.json").read_text())
    return manifest, document, knowledge


def test_intake_report_reads_processed_text_and_table_outputs(repository: Path) -> None:
    make_pdf(
        repository / "projects/example/source/text.pdf",
        ["Normal embedded text with enough characters to be classified as extracted.", "tiny", ""],
    )
    make_ruled_table(repository / "projects/example/source/table.pdf")
    process_all(repository)

    report = build_report(repository / "projects/example")

    assert report["document_count"] == 2
    assert report["page_count"] == 4
    assert report["extracted"] >= 1
    assert report["minimal_text"] >= 1
    assert report["no_text"] == 1
    assert report["accepted_table_count"] == 1
    assert report["pages_with_accepted_tables"] == 1


def test_process_extracts_pages_identity_hash_and_valid_schema(repository: Path) -> None:
    pdf = repository / "projects/example/source/nested/sample.pdf"
    make_pdf(pdf, ["First page source text with enough characters for extraction.", "Second page source text with enough characters."])

    result = process_all(repository)
    manifest, document, knowledge = load_outputs(repository)

    expected_path = "projects/example/source/nested/sample.pdf"
    assert result.processed == 1
    assert document["document_id"] == document_id(expected_path)
    assert document["source_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert document["page_count"] == 2
    assert document["pdf_metadata"]["title"] == "Synthetic title"
    assert document["pages"][0]["extraction_status"] == "extracted"
    assert "First page source text" in (knowledge / "pages/p0001.md").read_text()
    assert "page: 1" in (knowledge / "pages/p0001.md").read_text()
    assert "Second page source text" in (knowledge / "pages/p0002.md").read_text()
    jsonschema.validate(document, json.loads((repository / "schema/document.schema.json").read_text()))
    page = json.loads((knowledge / "pages/p0001.json").read_text())
    jsonschema.validate(page, json.loads((repository / "schema/pdf_page.schema.json").read_text()))
    jsonschema.validate(manifest, json.loads((repository / "schema/manifest.schema.json").read_text()))


def test_ruled_table_uses_source_spans_and_is_deterministic(repository: Path) -> None:
    pdf = repository / "projects/example/source/table.pdf"
    make_ruled_table(pdf)
    process_all(repository)
    manifest, _document, knowledge = load_outputs(repository)
    first = (knowledge / "pages/p0001.json").read_bytes()
    page = json.loads(first)

    assert manifest["pipeline_version"] == "2"
    assert page["table_extraction"] == {
        "accepted_count": 1, "algorithm": "pymupdf_lines_strict", "algorithm_version": "1",
        "candidate_count": 1, "library": "PyMuPDF", "library_version": "1.28.2",
        "rejected_count": 0, "rejection_counts": {}, "status": "completed",
    }
    table = page["tables"][0]
    assert (table["row_count"], table["column_count"], table["provenance"]) == (3, 3, "derived_pdf_table")
    assert [cell["text"] for cell in table["cells"]] == [
        "Header", "日本語", "", "A", "line one\nline two", "C", "D", "E", "F"
    ]
    assert all(cell["row_span"] == cell["column_span"] == 1 for cell in table["cells"])
    assert table["cells"][0]["span_refs"] == [{"block_index": 0, "line_index": 0, "span_index": 0}]
    assert process_all(repository).unchanged == 1
    assert (knowledge / "pages/p0001.json").read_bytes() == first


def test_large_ruled_table_assigns_each_span_to_one_cell(repository: Path) -> None:
    make_large_ruled_table(repository / "projects/example/source/large-table.pdf")
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    page = json.loads((knowledge / "pages/p0001.json").read_text())

    table = page["tables"][0]
    assert (table["row_count"], table["column_count"]) == (10, 10)
    assert len(table["cells"]) == 100
    assert [cell["text"] for cell in table["cells"]] == [
        f"{row}:{column}" for row in range(10) for column in range(10)
    ]


def test_two_tables_are_ordered_top_to_bottom_then_left_to_right(
    repository: Path,
) -> None:
    pdf = repository / "projects/example/source/two-tables.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=500)
    bottom_left = (40.0, 260.0, 240.0, 380.0)
    top_right = (320.0, 40.0, 520.0, 160.0)
    for bbox, prefix in ((bottom_left, "bottom"), (top_right, "top")):
        draw_grid(page, bbox)
        left, top, right, bottom = bbox
        for row in range(2):
            for column in range(2):
                page.insert_text(
                    (left + column * (right - left) / 2 + 10,
                     top + row * (bottom - top) / 2 + 30),
                    f"{prefix}-{row}-{column}",
                    fontsize=8,
                )
    document.save(pdf)
    document.close()

    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    sidecar = knowledge / "pages/p0001.json"
    canonical = sidecar.read_bytes()
    tables = json.loads(canonical)["tables"]
    assert [table["order_index"] for table in tables] == [0, 1]
    assert [table["bbox"] for table in tables] == [list(top_right), list(bottom_left)]
    assert process_all(repository).unchanged == 1
    assert sidecar.read_bytes() == canonical


def test_background_rectangles_are_not_accepted_as_tables(repository: Path) -> None:
    pdf = repository / "projects/example/source/background.pdf"
    document = fitz.open()
    page = document.new_page()
    page.draw_rect((40, 40, 550, 750), color=(0.8, 0.8, 0.8), fill=(0.95, 0.95, 0.95))
    page.draw_rect((70, 100, 250, 180), color=(0.7, 0.7, 0.7))
    page.insert_text((80, 140), "Ordinary prose inside a decorative panel")
    document.save(pdf)
    document.close()

    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    page_data = json.loads((knowledge / "pages/p0001.json").read_text())
    assert page_data["tables"] == []
    assert page_data["text_blocks"][0]["text"] == "Ordinary prose inside a decorative panel"


@pytest.mark.parametrize(
    ("candidate_cells", "text_point", "reason"),
    [
        (
            [[(50.0, 50.0, 150.0, 100.0), None],
             [(50.0, 100.0, 150.0, 150.0), (150.0, 100.0, 250.0, 150.0)]],
            (70, 80),
            "merged_or_missing_cell",
        ),
        (
            [[(50.0, 50.0, 150.0, 100.0), (150.0, 50.0, 250.0, 100.0)],
             [(50.0, 100.0, 150.0, 150.0), (150.0, 100.0, 250.0, 150.0)]],
            (130, 80),
            "ambiguous_span_mapping",
        ),
    ],
)
def test_ambiguous_candidate_is_rejected_without_losing_text(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate_cells: list[list[tuple[float, float, float, float] | None]],
    text_point: tuple[int, int],
    reason: str,
) -> None:
    pdf = repository / "projects/example/source/rejected-table.pdf"
    document = fitz.open()
    page = document.new_page()
    draw_grid(page, (50.0, 50.0, 250.0, 150.0))
    text = "Text crossing boundary" if reason == "ambiguous_span_mapping" else "Source text"
    page.insert_text(text_point, text, fontsize=12)
    document.save(pdf)
    document.close()
    candidate = SimpleNamespace(
        bbox=(50.0, 50.0, 250.0, 150.0),
        row_count=2,
        col_count=2,
        rows=[SimpleNamespace(cells=cells) for cells in candidate_cells],
    )
    monkeypatch.setattr(
        fitz.Page,
        "find_tables",
        lambda *_args, **_kwargs: SimpleNamespace(tables=[candidate]),
    )

    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    page_data = json.loads((knowledge / "pages/p0001.json").read_text())
    assert page_data["tables"] == []
    assert page_data["table_extraction"]["rejection_counts"] == {reason: 1}
    assert text in page_data["text_blocks"][0]["text"]


@pytest.mark.parametrize("corruption", ["missing_tables", "accepted_count", "span_ref"])
def test_corrupt_v2_table_contract_is_rebuilt(
    repository: Path, corruption: str
) -> None:
    pdf = repository / "projects/example/source/table.pdf"
    make_ruled_table(pdf)
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    sidecar = knowledge / "pages/p0001.json"
    canonical = sidecar.read_bytes()
    page = json.loads(canonical)
    if corruption == "missing_tables":
        page.pop("tables")
    elif corruption == "accepted_count":
        page["table_extraction"]["accepted_count"] = 2
    else:
        page["tables"][0]["cells"][0]["span_refs"][0]["span_index"] = 999
    sidecar.write_text(json.dumps(page), encoding="utf-8")

    assert process_all(repository).processed == 1
    assert sidecar.read_bytes() == canonical


def test_aligned_text_without_drawings_has_no_table(repository: Path) -> None:
    make_pdf(repository / "projects/example/source/prose.pdf", ["A       B       C\nD       E       F"])
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    page = json.loads((knowledge / "pages/p0001.json").read_text())
    assert page["tables"] == []
    assert page["table_extraction"]["status"] == "no_candidates"


def test_rotated_ruled_table_stays_in_page_evidence_coordinates(repository: Path) -> None:
    pdf = repository / "projects/example/source/rotated-table.pdf"
    make_ruled_table(pdf)
    with fitz.open(pdf) as source:
        source[0].set_rotation(90)
        source.save(pdf.with_suffix(".rotated.pdf"))
    pdf.with_suffix(".rotated.pdf").replace(pdf)
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    page = json.loads((knowledge / "pages/p0001.json").read_text())
    assert page["rotation"] == 90
    assert page["coordinate_space"] == "pdf_points_top_left"
    assert page["table_extraction"]["accepted_count"] == 1
    assert page["tables"][0]["bbox"] == [50.0, 50.0, 350.0, 200.0]


def test_table_extractor_exception_is_non_fatal(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = repository / "projects/example/source/table.pdf"
    make_ruled_table(pdf)

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("environment-dependent details must not be persisted")

    monkeypatch.setattr(fitz.Page, "find_tables", fail)
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    page = json.loads((knowledge / "pages/p0001.json").read_text())
    assert page["text_blocks"]
    assert page["tables"] == []
    assert page["table_extraction"]["status"] == "extraction_error"
    assert page["table_extraction"]["rejection_counts"] == {"extraction_error": 1}
    assert "environment-dependent" not in (knowledge / "pages/p0001.json").read_text()


def test_v1_tree_migrates_to_v2(repository: Path) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["Legacy source text"])
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    convert_output_to_legacy_v1(repository, knowledge)

    assert process_all(repository).processed == 1
    migrated_manifest, migrated_document, migrated = load_outputs(repository)
    assert migrated_manifest["pipeline_version"] == migrated_document["pipeline_version"] == "2"
    assert (migrated / ".pdf-pipeline-v2").is_file()
    assert not (migrated / ".pdf-pipeline-v1").exists()


@pytest.mark.parametrize(
    ("markers", "message"),
    [
        ((".pdf-pipeline-v2", ".pdf-pipeline-v3"), "ambiguous"),
        ((".pdf-pipeline-v1", ".pdf-pipeline-v3"), "ambiguous"),
        ((".pdf-pipeline-v3",), "unsupported"),
        ((".pdf-pipeline-v1", ".pdf-pipeline-v2"), "ambiguous"),
    ],
)
def test_current_output_rejects_unsafe_ownership_markers(
    repository: Path, markers: tuple[str, ...], message: str
) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["Source text"])
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    for marker in (".pdf-pipeline-v1", ".pdf-pipeline-v2", ".pdf-pipeline-v3"):
        (knowledge / marker).unlink(missing_ok=True)
    for marker in markers:
        (knowledge / marker).touch()

    with pytest.raises(PipelineError, match=message):
        process_all(repository)
    assert knowledge.is_dir()


@pytest.mark.parametrize(
    ("markers", "message"),
    [
        ((".pdf-pipeline-v2", ".pdf-pipeline-v3"), "ambiguous"),
        ((".pdf-pipeline-v1", ".pdf-pipeline-v3"), "ambiguous"),
        ((".pdf-pipeline-v3",), "unsupported"),
        ((".pdf-pipeline-v1", ".pdf-pipeline-v2"), "ambiguous"),
    ],
)
def test_stale_cleanup_rejects_unsafe_ownership_markers(
    repository: Path, markers: tuple[str, ...], message: str
) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["Source text"])
    process_all(repository)
    manifest_path = repository / "projects/example/manifest.json"
    manifest_before = manifest_path.read_bytes()
    _manifest, _document, knowledge = load_outputs(repository)
    for marker in (".pdf-pipeline-v1", ".pdf-pipeline-v2", ".pdf-pipeline-v3"):
        (knowledge / marker).unlink(missing_ok=True)
    for marker in markers:
        (knowledge / marker).touch()
    pdf.unlink()

    with pytest.raises(PipelineError, match=message):
        process_all(repository)
    assert knowledge.is_dir()
    assert manifest_path.read_bytes() == manifest_before


def test_removed_source_cleans_up_valid_legacy_v1_knowledge(repository: Path) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["Legacy source text"])
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    convert_output_to_legacy_v1(repository, knowledge)
    pdf.unlink()

    result = process_all(repository)
    assert result.removed == 1
    assert not knowledge.exists()
    manifest = json.loads((repository / "projects/example/manifest.json").read_text())
    assert manifest["pipeline_version"] == "2"
    assert manifest["documents"] == []


def test_removed_source_never_deletes_unmanaged_stale_knowledge(repository: Path) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["Source text"])
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)
    (knowledge / ".pdf-pipeline-v2").unlink()
    sentinel = knowledge / "human-authored.md"
    sentinel.write_text("preserve\n", encoding="utf-8")
    pdf.unlink()

    result = process_all(repository)
    assert result.removed == 0
    assert knowledge.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_v1_migration_apply_failure_restores_previous_state(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["Legacy source text"])
    process_all(repository)
    manifest_path = repository / "projects/example/manifest.json"
    _manifest, _document, knowledge = load_outputs(repository)
    convert_output_to_legacy_v1(repository, knowledge)
    manifest_before = manifest_path.read_bytes()
    knowledge_before = {
        path.relative_to(knowledge): path.read_bytes()
        for path in knowledge.rglob("*")
        if path.is_file()
    }
    source_before = pdf.read_bytes()
    doc_id = document_id("projects/example/source/sample.pdf")
    real_replace = pipeline.os.replace

    def fail_staged_migration(source: Path | str, destination: Path | str) -> None:
        if Path(source).name == doc_id and Path(destination) == knowledge:
            raise OSError("injected migration apply failure")
        real_replace(source, destination)

    monkeypatch.setattr(pipeline.os, "replace", fail_staged_migration)
    with pytest.raises(PipelineError, match="failed to apply project transaction"):
        process_all(repository)

    assert pdf.read_bytes() == source_before
    assert manifest_path.read_bytes() == manifest_before
    assert knowledge_before == {
        path.relative_to(knowledge): path.read_bytes()
        for path in knowledge.rglob("*")
        if path.is_file()
    }
    assert (knowledge / ".pdf-pipeline-v1").is_file()
    assert not (knowledge / ".pdf-pipeline-v2").exists()


def test_idempotency_update_and_stale_cleanup(repository: Path) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["Original text that is long enough to count as extracted content."])
    process_all(repository)
    manifest1, document1, knowledge = load_outputs(repository)
    snapshot = {p.relative_to(repository): p.read_bytes() for p in repository.rglob("*") if p.is_file()}

    result = process_all(repository)
    assert result.unchanged == 1
    assert snapshot == {p.relative_to(repository): p.read_bytes() for p in repository.rglob("*") if p.is_file()}

    make_pdf(pdf, ["Updated source content that changes the PDF and its hash.", "New second page has text as well."])
    process_all(repository)
    manifest2, document2, updated_knowledge = load_outputs(repository)
    assert document2["document_id"] == document1["document_id"]
    assert document2["source_sha256"] != document1["source_sha256"]
    assert manifest2["documents"][0]["page_count"] == 2
    assert updated_knowledge == knowledge

    pdf.unlink()
    result = process_all(repository)
    assert result.removed == 1
    assert not knowledge.exists()
    assert json.loads((repository / "projects/example/manifest.json").read_text())["documents"] == []


@pytest.mark.parametrize("damaged", ["index", "page", "sidecar", "corrupt_sidecar", "document"])
def test_incomplete_generated_tree_is_rebuilt(repository: Path, damaged: str) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["First page source text.", "Second page source text."])
    process_all(repository)
    _manifest, _document, knowledge = load_outputs(repository)

    if damaged == "index":
        (knowledge / "index.md").unlink()
    elif damaged == "page":
        (knowledge / "pages/p0002.md").unlink()
    elif damaged == "sidecar":
        (knowledge / "pages/p0002.json").unlink()
    elif damaged == "corrupt_sidecar":
        (knowledge / "pages/p0002.json").write_text("{corrupt", encoding="utf-8")
    else:
        (knowledge / "document.json").write_text("{corrupt", encoding="utf-8")

    assert process_all(repository).processed == 1
    assert (knowledge / "index.md").stat().st_size > 0
    assert (knowledge / "pages/p0001.md").stat().st_size > 0
    assert (knowledge / "pages/p0002.md").stat().st_size > 0
    assert (knowledge / "pages/p0001.json").stat().st_size > 0
    assert (knowledge / "pages/p0002.json").stat().st_size > 0
    json.loads((knowledge / "document.json").read_text(encoding="utf-8"))
    assert process_all(repository).unchanged == 1


def test_textless_page_is_not_reported_as_success(repository: Path) -> None:
    make_pdf(repository / "projects/example/source/scan.pdf", [""])
    process_all(repository)
    _manifest, document, knowledge = load_outputs(repository)
    page = document["pages"][0]
    assert page["extraction_status"] == "no_text"
    assert page["vision_recommended"] is True
    assert "No embedded text" in (knowledge / "pages/p0001.md").read_text()
    structured = json.loads((knowledge / "pages/p0001.json").read_text())
    assert structured["extraction_status"] == "no_text"
    assert structured["text_blocks"] == []


def test_structured_pages_preserve_sizes_rotation_coordinates_and_japanese(
    repository: Path,
) -> None:
    pdf = repository / "projects/example/source/mixed.pdf"
    document = fitz.open()
    a4 = document.new_page(width=595, height=842)
    a4.insert_text((72, 72), "A4 portrait embedded text")
    landscape = document.new_page(width=1684, height=1191)
    landscape.insert_text((100, 120), "Large format landscape")
    rotated = document.new_page(width=300, height=500)
    rotated.insert_text((50, 80), "Rotated page text")
    rotated.set_rotation(90)
    japanese = document.new_page(width=612, height=792)
    japanese.insert_text((72, 72), "日本語の埋め込みテキスト", fontname="japan")
    document.save(pdf)
    document.close()

    process_all(repository)
    _manifest, summary, knowledge = load_outputs(repository)
    pages = [
        json.loads((knowledge / f"pages/p{number:04d}.json").read_text())
        for number in range(1, 5)
    ]

    assert [(page["width_points"], page["height_points"]) for page in pages] == [
        (595.0, 842.0),
        (1684.0, 1191.0),
        (300.0, 500.0),
        (612.0, 792.0),
    ]
    assert pages[2]["rotation"] == 90
    assert pages[2]["media_box"] == [0.0, 0.0, 300.0, 500.0]
    assert all(page["coordinate_space"] == "pdf_points_top_left" for page in pages)
    assert summary["pages"][2]["structured_page"] == "pages/p0003.json"
    assert "日本語の埋め込みテキスト" in pages[3]["text_blocks"][0]["text"]
    assert pages[2]["text_blocks"][0]["bbox"] == pytest.approx(
        [50.0, 68.175, 136.834, 83.289], abs=0.01
    )
    page_schema = json.loads((repository / "schema/pdf_page.schema.json").read_text())
    for page in pages:
        jsonschema.validate(page, page_schema)


def test_structured_text_preserves_block_line_span_hierarchy(repository: Path) -> None:
    pdf = repository / "projects/example/source/structure.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "First line\nSecond line")
    page.insert_text((300, 300), "Separate block")
    page.insert_text((72, 400), "Small", fontsize=9)
    page.insert_text((100, 400), " Large", fontsize=15)
    document.save(pdf)
    document.close()

    process_all(repository)
    _manifest, _summary, knowledge = load_outputs(repository)
    structured = json.loads((knowledge / "pages/p0001.json").read_text())
    blocks = structured["text_blocks"]
    assert len(blocks) >= 3
    assert len(blocks[0]["lines"]) == 2
    assert sum(len(line["spans"]) for block in blocks for line in block["lines"]) >= 4
    assert [block["order_index"] for block in blocks] == list(range(len(blocks)))
    first_span = blocks[0]["lines"][0]["spans"][0]
    assert first_span["provenance"] == "embedded_pdf_text"
    assert first_span["font_name"] == "Helvetica"
    assert first_span["font_size"] == 11.0
    assert isinstance(first_span["font_flags"], int)


@pytest.mark.parametrize("rotation", [0, 90])
def test_media_box_is_normalized_to_unrotated_text_coordinates(
    repository: Path, rotation: int
) -> None:
    pdf = repository / f"projects/example/source/offset-{rotation}.pdf"
    document = fitz.open()
    page = document.new_page(width=400, height=600)
    page.insert_text((50, 80), "Offset box text")
    document.xref_set_key(page.xref, "MediaBox", "[10 20 410 620]")
    document.xref_set_key(page.xref, "CropBox", "[40 100 340 550]")
    document.xref_set_key(page.xref, "Rotate", str(rotation))
    document.save(pdf)
    document.close()

    with fitz.open(pdf) as source:
        source_page = source[0]
        raw_media_box = list(source_page.mediabox)
        source_page.set_rotation(0)
        expected_media_box = list(source_page.mediabox * source_page.transformation_matrix)
        expected_crop_box = list(source_page.rect)

    process_all(repository)
    _manifest, _summary, knowledge = load_outputs(repository)
    structured = json.loads((knowledge / "pages/p0001.json").read_text())

    assert raw_media_box == [10.0, 20.0, 410.0, 620.0]
    assert structured["media_box"] == expected_media_box == [-30.0, -70.0, 370.0, 530.0]
    assert structured["media_box"] != raw_media_box
    assert structured["crop_box"] == expected_crop_box == [0.0, 0.0, 300.0, 450.0]
    assert structured["text_blocks"][0]["bbox"] == pytest.approx(
        [10.0, 18.175, 80.928, 33.289], abs=0.01
    )
    assert structured["rotation"] == rotation
    assert structured["coordinate_space"] == "pdf_points_top_left"


def test_corrupt_pdf_fails_without_replacing_valid_knowledge(repository: Path) -> None:
    good = repository / "projects/example/source/good.pdf"
    make_pdf(good, ["Known-good content with sufficient text for extraction status."])
    process_all(repository)
    manifest_before = (repository / "projects/example/manifest.json").read_bytes()
    knowledge_before = {p: p.read_bytes() for p in (repository / "projects/example/knowledge").rglob("*") if p.is_file()}
    (repository / "projects/example/source/bad.pdf").write_bytes(b"not a pdf")

    with pytest.raises(PipelineError, match="corrupt or invalid"):
        process_all(repository)
    assert (repository / "projects/example/manifest.json").read_bytes() == manifest_before
    assert knowledge_before == {p: p.read_bytes() for p in (repository / "projects/example/knowledge").rglob("*") if p.is_file()}


def test_selected_page_rendering(repository: Path) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["page one", "page two", "page three"])
    outputs = render_pages(
        repository, pdf, pages="1,3", all_pages=False, dpi=300, output=Path("artifacts/vision")
    )
    assert [path.name for path in outputs] == ["sample-p0001-300dpi.png", "sample-p0003-300dpi.png"]
    assert all(path.read_bytes().startswith(b"\x89PNG") for path in outputs)
    with pytest.raises(PipelineError, match="within 1-3"):
        render_pages(repository, pdf, pages="4", all_pages=False, dpi=300, output=Path("artifacts"))


def test_render_restricts_repository_outputs_to_artifact_roots(
    repository: Path, tmp_path: Path
) -> None:
    outside = repository / "outside.pdf"
    make_pdf(outside, ["outside"])
    with pytest.raises(PipelineError, match="inside projects"):
        render_pages(repository, outside, pages="1", all_pages=False, dpi=300, output=Path("artifacts"))
    source = repository / "projects/example/source/sample.pdf"
    make_pdf(source, ["source"])
    rejected = [
        Path("projects/example/source/renders"),
        Path("projects/example/knowledge/images"),
        Path("schema/rendered"),
    ]
    for destination in rejected:
        with pytest.raises(PipelineError, match="artifact root"):
            render_pages(
                repository, source, pages="1", all_pages=False, dpi=300, output=destination
            )
        assert not (repository / destination).exists()

    tracked = repository / "schema"
    alias = repository / "artifacts/tracked-alias"
    alias.parent.mkdir()
    alias.symlink_to(tracked, target_is_directory=True)
    with pytest.raises(PipelineError, match="artifact root"):
        render_pages(repository, source, pages="1", all_pages=False, dpi=300, output=alias)

    external = tmp_path.parent / f"{tmp_path.name}-external-render"
    outputs = render_pages(
        repository, source, pages="1", all_pages=False, dpi=300, output=external.resolve()
    )
    assert outputs[0].is_file()


def test_process_refuses_to_replace_unmanaged_directory(repository: Path) -> None:
    pdf = repository / "projects/example/source/sample.pdf"
    make_pdf(pdf, ["Source text that is sufficiently long for normal extraction."])
    doc_id = document_id("projects/example/source/sample.pdf")
    output = repository / f"projects/example/knowledge/sample--{doc_id[4:16]}"
    output.mkdir(parents=True)
    sentinel = output / "human-authored.md"
    sentinel.write_text("preserve exactly\n")

    with pytest.raises(PipelineError, match="unmanaged knowledge directory"):
        process_all(repository)

    assert output.is_dir()
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_text() == "preserve exactly\n"
    assert not (repository / "projects/example/manifest.json").exists()


def test_multi_document_apply_failure_rolls_back_project(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = repository / "projects/example/source/first.pdf"
    second = repository / "projects/example/source/second.pdf"
    make_pdf(first, ["Original first document text with enough characters to extract."])
    make_pdf(second, ["Original second document text with enough characters to extract."])
    process_all(repository)
    manifest_path = repository / "projects/example/manifest.json"
    manifest_before = manifest_path.read_bytes()
    knowledge_root = repository / "projects/example/knowledge"
    knowledge_before = {
        path.relative_to(knowledge_root): path.read_bytes()
        for path in knowledge_root.rglob("*")
        if path.is_file()
    }

    make_pdf(first, ["Updated first document text that must be rolled back on failure."])
    make_pdf(second, ["Updated second document text that triggers injected apply failure."])
    second_id = document_id("projects/example/source/second.pdf")
    real_replace = pipeline.os.replace

    def fail_second_staged_replace(source: Path | str, destination: Path | str) -> None:
        if Path(source).name == second_id:
            raise OSError("injected replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(pipeline.os, "replace", fail_second_staged_replace)
    with pytest.raises(PipelineError, match="failed to apply project transaction"):
        process_all(repository)

    assert manifest_path.read_bytes() == manifest_before
    assert knowledge_before == {
        path.relative_to(knowledge_root): path.read_bytes()
        for path in knowledge_root.rglob("*")
        if path.is_file()
    }
