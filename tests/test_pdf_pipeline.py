from __future__ import annotations

import hashlib
import json
from pathlib import Path

import fitz
import jsonschema
import pytest

from tools.pdf_pipeline.pipeline import PipelineError, document_id, process_all, render_pages


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


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "projects" / "example" / "source").mkdir(parents=True)
    (tmp_path / "schema").mkdir()
    source_schema = Path(__file__).parents[1] / "schema"
    for name in ("document.schema.json", "manifest.schema.json"):
        (tmp_path / "schema" / name).write_bytes((source_schema / name).read_bytes())
    return tmp_path


def load_outputs(repository: Path) -> tuple[dict, dict, Path]:
    manifest = json.loads((repository / "projects/example/manifest.json").read_text())
    knowledge = repository / manifest["documents"][0]["knowledge_path"]
    document = json.loads((knowledge / "document.json").read_text())
    return manifest, document, knowledge


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
    jsonschema.validate(manifest, json.loads((repository / "schema/manifest.schema.json").read_text()))


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


def test_textless_page_is_not_reported_as_success(repository: Path) -> None:
    make_pdf(repository / "projects/example/source/scan.pdf", [""])
    process_all(repository)
    _manifest, document, knowledge = load_outputs(repository)
    page = document["pages"][0]
    assert page["extraction_status"] == "no_text"
    assert page["vision_recommended"] is True
    assert "No embedded text" in (knowledge / "pages/p0001.md").read_text()


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


def test_render_rejects_non_source_and_knowledge_output(repository: Path) -> None:
    outside = repository / "outside.pdf"
    make_pdf(outside, ["outside"])
    with pytest.raises(PipelineError, match="inside projects"):
        render_pages(repository, outside, pages="1", all_pages=False, dpi=300, output=Path("artifacts"))
    source = repository / "projects/example/source/sample.pdf"
    make_pdf(source, ["source"])
    with pytest.raises(PipelineError, match="knowledge"):
        render_pages(repository, source, pages="1", all_pages=False, dpi=300, output=Path("projects/example/knowledge/images"))
