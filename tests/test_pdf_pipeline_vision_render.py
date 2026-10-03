"""Phase 5B uses only synthetic source PDFs, without OCR or Vision."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import fitz
import pytest

from tools.pdf_pipeline import pipeline
from tools.pdf_pipeline.pipeline import PipelineError, render_pages
from tools.pdf_pipeline.vision_render import render_vision_candidate
from tools.query_core.build import build_database
from tools.query_core.query import QueryCore, SCHEMA_VERSION


ENTITY = "selected-region"
BBOX = [20.0, 30.0, 100.0, 70.0]


def make_case(root: Path, rotation: int = 0, cropped: bool = False) -> dict:
    pdf = root / "projects/example/source/synthetic.pdf"
    pdf.parent.mkdir(parents=True)
    with fitz.open() as document:
        page = document.new_page(width=300, height=200)
        offset_x, offset_y = (40, 25) if cropped else (0, 0)
        page.draw_rect(fitz.Rect(BBOX) + (offset_x, offset_y, offset_x, offset_y),
                       color=None, fill=(1, 0, 0))
        page.draw_rect((30 + offset_x, 40 + offset_y, 40 + offset_x, 50 + offset_y),
                       color=None, fill=(0, 0, 1))
        # A distractor outside the selected region proves we don't render a page.
        page.draw_rect((160, 100, 180, 120), color=None, fill=(0, 1, 0))
        if cropped:
            page.set_cropbox(fitz.Rect(40, 25, 260, 175))
        page.set_rotation(rotation)
        document.save(pdf)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    records = {
        "project_id": "renderer-test", "created_from": "synthetic", "binding_mode": "project",
        "documents": [{"id": "document", "identity": "original/synthetic.pdf",
                       "title": "Synthetic drawing", "source_filename": "synthetic.pdf", "source_sha256": sha}],
        "evidence": [{"id": f"evidence-{scope}", "document_id": "document", "pdf_page": 1,
                      **dict(zip(("x_min", "y_min", "x_max", "y_max"),
                                 BBOX if scope == "region" else [None] * 4)),
                      "coordinate_space": "pdf_points_top_left" if scope == "region" else None}
                     for scope in ("page", "region")],
        "semantic_entities": [{"id": f"selected-{scope}", "entity_class": "Door",
                               "label": "Unsafe label / 日本語", "instance_or_type": "instance",
                               "resolution_state": "exact", "provenance": "synthetic",
                               "evidence_id": f"evidence-{scope}"} for scope in ("page", "region")],
    }
    database = build_database(records, root / "artifacts/project.sqlite")
    with QueryCore(database) as core:
        candidates = {scope: core.get_vision_evidence_candidates(f"selected-{scope}")["candidates"][0]
                      for scope in ("page", "region")}
    return {"root": root, "pdf": pdf, "database": database, "candidates": candidates}


@pytest.fixture
def case(tmp_path: Path) -> dict:
    return make_case(tmp_path)


def render(case: dict, scope: str = "region", **kwargs) -> dict:
    return render_vision_candidate(
        case["root"], case["database"], f"selected-{scope}",
        case["candidates"][scope]["candidate_id"], case["pdf"],
        output=kwargs.pop("output", Path("artifacts/vision")), **kwargs,
    )


def override(case: dict, monkeypatch: pytest.MonkeyPatch, scope: str = "region") -> dict:
    # Invalid Phase 5A surfaces would normally be omitted. Inject at the boundary
    # to prove the renderer independently rejects them without unsafe fallback.
    routed = {"candidates": [deepcopy(case["candidates"][scope])]}
    monkeypatch.setattr(QueryCore, "get_vision_evidence_candidates", lambda *_: routed)
    return routed["candidates"][0]


def output_file(case: dict, result: dict) -> Path:
    return case["root"] / result["output_path"]


def test_page_candidate_matches_existing_full_page_render(case: dict) -> None:
    result = render(case, "page", dpi=144)
    assert result["bbox"] is None
    assert result["coordinate_space"] is None
    assert result["input_scope"] == "page"
    assert (result["pixel_width"], result["pixel_height"]) == (600, 400)
    existing = render_pages(case["root"], case["pdf"], pages="1", all_pages=False,
                            dpi=144, output=Path("artifacts/existing"))[0]
    assert existing.name == "synthetic-p0001-144dpi.png"
    assert output_file(case, result).read_bytes() == existing.read_bytes()
    pixmap = fitz.Pixmap(str(output_file(case, result)))
    assert pixmap.pixel(340, 220) == (0, 255, 0)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
def test_exact_region_geometry_and_content_for_all_rotations(
    tmp_path: Path, rotation: int, cropped: bool,
) -> None:
    case = make_case(tmp_path, rotation, cropped)
    before = case["pdf"].read_bytes()
    result = render(case, dpi=144)
    width, height = ((160, 80) if rotation in (0, 180) else (80, 160))
    assert (result["pixel_width"], result["pixel_height"]) == (width, height)
    assert result["bbox"] == BBOX
    assert result["coordinate_space"] == "pdf_points_top_left"
    assert result["renderer"]["page_rotation"] == rotation
    pixmap = fitz.Pixmap(str(output_file(case, result)))
    blue_point = {0: (30, 30), 90: (50, 30), 180: (130, 50), 270: (30, 130)}[rotation]
    assert pixmap.pixel(*blue_point) == (0, 0, 255)
    assert all(pixmap.pixel(x, y) == (255, 0, 0)
               for x, y in [(4, 4), (width - 5, 4), (4, height - 5), (width - 5, height - 5)])
    # Every pixel is inside the red rectangle or its blue marker, with no page
    # background, green distractor, context padding, or missing evidence.
    samples = pixmap.samples
    assert all(samples[index + 1] == 0 for index in range(0, len(samples), 3))
    assert case["pdf"].read_bytes() == before


@pytest.mark.parametrize("coordinate", [None, "pdf_points", "model_mm", ""])
def test_region_coordinate_space_rechecked(case: dict, monkeypatch, coordinate) -> None:
    candidate = override(case, monkeypatch)
    candidate["coordinate_space"] = coordinate
    if coordinate == "":
        del candidate["coordinate_space"]
    with pytest.raises(PipelineError, match="unsupported coordinate space"):
        render(case)
    assert not (case["root"] / "artifacts/vision").exists()


@pytest.mark.parametrize("bbox", [None, [], [1, 2, 3], "1,2,3,4", [True, 1, 3, 4],
                                  [1, 2, 1, 4], [1, 2, 3, 2], [3, 2, 1, 4],
                                  [float("nan"), 0, 1, 1], [0, 0, float("inf"), 1],
                                  [0, float("-inf"), 1, 1], [0, 0, "3", 4]])
def test_invalid_bbox_has_no_page_fallback(case: dict, monkeypatch, bbox) -> None:
    override(case, monkeypatch)["bbox"] = bbox
    with pytest.raises(PipelineError, match="invalid bbox"):
        render(case)
    assert not (case["root"] / "artifacts/vision").exists()


@pytest.mark.parametrize("bbox", [[-1, 30, 100, 70], [20, -1, 100, 70],
                                  [20, 30, 301, 70], [20, 30, 100, 201],
                                  [400, 300, 500, 400]])
def test_outside_bbox_rejected_without_clipping(case: dict, monkeypatch, bbox) -> None:
    override(case, monkeypatch)["bbox"] = bbox
    with pytest.raises(PipelineError, match="out-of-bounds bbox"):
        render(case)
    assert not (case["root"] / "artifacts/vision").exists()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_bounds_use_unrotated_crop_extent(tmp_path, monkeypatch, rotation) -> None:
    case = make_case(tmp_path, rotation, cropped=True)
    override(case, monkeypatch)["bbox"] = [20, 30, 221, 70]
    with pytest.raises(PipelineError, match="out-of-bounds bbox"):
        render(case)


@pytest.mark.parametrize("scope,bbox,message", [("page", BBOX, "null bbox"),
                                               ("other", None, "input scope")])
def test_inconsistent_scope_rejected(case, monkeypatch, scope, bbox, message) -> None:
    candidate = override(case, monkeypatch)
    candidate.update(input_scope=scope, bbox=bbox)
    with pytest.raises(PipelineError, match=message):
        render(case)


@pytest.mark.parametrize("sha,message", [(None, "SHA missing"), ("", "SHA missing"),
                                         ("bad", "SHA malformed"), (123, "SHA malformed"),
                                         ("A" * 64, "SHA malformed"), ("0" * 64, "SHA mismatch")])
def test_source_sha_required_and_exact(case, monkeypatch, sha, message) -> None:
    override(case, monkeypatch)["document"]["source_sha256"] = sha
    with pytest.raises(PipelineError, match=message):
        render(case)
    assert not (case["root"] / "artifacts/vision").exists()


def test_same_filename_different_pdf_is_rejected(case) -> None:
    original = case["pdf"].read_bytes()
    with fitz.open(stream=original, filetype="pdf") as document:
        document[0].insert_text((10, 10), "different bytes")
        case["pdf"].write_bytes(document.tobytes())
    with pytest.raises(PipelineError, match="source SHA mismatch"):
        render(case)


def test_renamed_copy_uses_authoritative_metadata_and_same_output(case) -> None:
    first = render(case, dpi=72)
    copy = case["root"] / "renamed.bin"
    copy.write_bytes(case["pdf"].read_bytes())
    case["pdf"] = copy
    assert render(case, dpi=72) == first
    assert first["document"] == case["candidates"]["region"]["document"]


def test_verified_buffer_is_used_even_if_source_changes(case, monkeypatch) -> None:
    real_read = Path.read_bytes
    original = real_read(case["pdf"])

    def replace_after_read(path):
        data = real_read(path)
        if path == case["pdf"]:
            path.write_bytes(b"externally replaced after hash input read")
        return data

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    result = render(case, dpi=72)
    assert result["document"]["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert fitz.Pixmap(str(output_file(case, result))).pixel(4, 4) == (255, 0, 0)


def test_missing_candidate_and_entity(case) -> None:
    for entity, candidate, message in [
        (ENTITY, "absent", "candidate not found"),
        ("absent", case["candidates"]["region"]["candidate_id"], "semantic entity not found"),
    ]:
        with pytest.raises(PipelineError, match=message):
            render_vision_candidate(case["root"], case["database"], entity, candidate,
                                    case["pdf"], output=Path("artifacts/vision"))


def test_duplicate_candidate_rejected(case, monkeypatch) -> None:
    candidate = case["candidates"]["region"]
    monkeypatch.setattr(QueryCore, "get_vision_evidence_candidates",
                        lambda *_: {"candidates": [candidate, deepcopy(candidate)]})
    with pytest.raises(PipelineError, match="duplicate candidate ID"):
        render(case)


@pytest.mark.parametrize("number", [0, -1, 2, 1.5, True, None])
def test_page_range_rechecked(case, monkeypatch, number) -> None:
    override(case, monkeypatch)["pdf_page"] = number
    with pytest.raises(PipelineError, match="page outside PDF"):
        render(case)


@pytest.mark.parametrize("dpi", [35, 1201, 72.5, True])
def test_dpi_bounds(case, dpi) -> None:
    with pytest.raises(PipelineError, match="DPI"):
        render(case, dpi=dpi)


@pytest.mark.parametrize("dpi", [36, 1200])
def test_dpi_endpoints_accepted(case, dpi) -> None:
    assert render(case, dpi=dpi)["dpi"] == dpi


def test_missing_pdf_is_explicit(case) -> None:
    case["pdf"].unlink()
    with pytest.raises(PipelineError, match="source PDF not found"):
        render(case)


@pytest.mark.parametrize("kind", ["corrupt", "password"])
def test_unopenable_or_encrypted_pdf(case, monkeypatch, kind) -> None:
    if kind == "corrupt":
        case["pdf"].write_bytes(b"not a PDF")
    else:
        with fitz.open(case["pdf"]) as document:
            data = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                                    owner_pw="owner", user_pw="secret")
        case["pdf"].write_bytes(data)
    candidate = override(case, monkeypatch)
    candidate["document"]["source_sha256"] = hashlib.sha256(case["pdf"].read_bytes()).hexdigest()
    with pytest.raises(PipelineError, match="cannot open PDF" if kind == "corrupt" else "password-protected PDF"):
        render(case)


def test_deterministic_names_bytes_metadata_and_input_immutability(case, monkeypatch) -> None:
    candidate = override(case, monkeypatch)
    candidate["document"]["source_filename"] = "危険 / drawing:?*.pdf"
    snapshot = deepcopy(candidate)
    before = {path: path.read_bytes() for path in [case["pdf"], case["database"]]}
    first = render(case)
    first_bytes = output_file(case, first).read_bytes()
    assert render(case) == first
    assert output_file(case, first).read_bytes() == first_bytes
    assert first["output_sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert re.fullmatch(r"[A-Za-z0-9._-]+", output_file(case, first).name)
    assert candidate["candidate_id"] in output_file(case, first).name
    assert output_file(case, first).name.endswith("-p0001-300dpi.png")
    assert first["renderer"] == {"name": "tools.pdf_pipeline.vision_render", "library": "PyMuPDF",
                                 "library_version": "1.28.2", "page_rotation": 0, "alpha": False}
    assert json.loads(json.dumps(first, allow_nan=False)) == first
    assert candidate == snapshot
    assert before == {path: path.read_bytes() for path in before}
    assert SCHEMA_VERSION == 2
    with sqlite3.connect(case["database"]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("failure", ["save", "replace", "render"])
def test_atomic_failure_preserves_previous_output_and_cleans_staging(case, monkeypatch, failure) -> None:
    first = render(case, dpi=72)
    target = output_file(case, first)
    before = target.read_bytes()

    def fail(*_args, **_kwargs):
        raise OSError("injected failure")

    if failure == "save":
        def partial_save(_pixmap, path):
            Path(path).write_bytes(b"partial PNG")
            fail()
        monkeypatch.setattr(fitz.Pixmap, "save", partial_save)
    elif failure == "replace":
        monkeypatch.setattr(pipeline.os, "replace", fail)
    else:
        monkeypatch.setattr(fitz.Page, "get_pixmap", fail)
    with pytest.raises(PipelineError, match="render failure"):
        render(case, dpi=72)
    assert target.read_bytes() == before
    assert list(target.parent.iterdir()) == [target]


def test_atomic_publication_uses_complete_sibling_file(case, monkeypatch) -> None:
    real_replace = pipeline.os.replace
    seen = []

    def inspect_replace(source, target):
        source, target = Path(source), Path(target)
        assert source.parent == target.parent
        assert source.read_bytes().startswith(b"\x89PNG")
        assert not target.exists()
        seen.append(target)
        real_replace(source, target)

    monkeypatch.setattr(pipeline.os, "replace", inspect_replace)
    result = render(case, dpi=72)
    assert seen == [output_file(case, result)]


@pytest.mark.parametrize("output", ["projects/example/source", "projects/example/knowledge",
                                    "tests/fixtures", "schema", ".", "../escape"])
def test_unsafe_output_rejected(case, output) -> None:
    with pytest.raises(PipelineError, match="artifact root"):
        render(case, output=Path(output))


def test_symlink_to_tracked_directory_is_rejected(case) -> None:
    alias = case["root"] / "artifacts/alias"
    alias.symlink_to(case["root"] / "projects/example/source", target_is_directory=True)
    with pytest.raises(PipelineError, match="artifact root"):
        render(case, output=alias)


def test_output_target_cannot_replace_input_via_symlink(case) -> None:
    result = render(case, dpi=72)
    target = output_file(case, result)
    target.unlink()
    before = case["database"].read_bytes()
    target.symlink_to(case["database"])
    with pytest.raises(PipelineError, match="would replace a read-only input"):
        render(case, dpi=72)
    assert case["database"].read_bytes() == before


def test_absolute_external_temporary_output_accepted(case, tmp_path) -> None:
    external = tmp_path.parent / f"{tmp_path.name}-render"
    result = render(case, output=external, dpi=72)
    assert Path(result["output_path"]).is_absolute()
    assert Path(result["output_path"]).is_file()


def test_cli_json_and_explicit_error(case) -> None:
    command = [sys.executable, "-m", "tools.pdf_pipeline", "--repo-root", str(case["root"]),
               "render-candidate", "artifacts/project.sqlite", ENTITY,
               case["candidates"]["region"]["candidate_id"], "--pdf",
               "projects/example/source/synthetic.pdf", "--dpi", "72", "--output", "artifacts/vision"]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout) == render(case, dpi=72)
    command[8] = "absent"
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 1
    assert failed.stdout == ""
    assert "candidate not found" in failed.stderr


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_existing_render_selection_and_bytes_unchanged(tmp_path, rotation) -> None:
    case = make_case(tmp_path, rotation)
    with fitz.open(case["pdf"]) as document:
        second = document.new_page(width=200, height=300)
        second.insert_text((40, 40), "second page")
        data = document.tobytes()
    case["pdf"].write_bytes(data)
    outputs = render_pages(case["root"], case["pdf"], pages="2,1-2,1", all_pages=False,
                            dpi=72, output=Path("artifacts/regression"))
    assert [path.name for path in outputs] == ["synthetic-p0001-72dpi.png", "synthetic-p0002-72dpi.png"]
    with fitz.open(case["pdf"]) as document:
        for number, path in enumerate(outputs):
            expected = document[number].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False).tobytes("png")
            assert path.read_bytes() == expected
    all_pages = render_pages(case["root"], case["pdf"], pages=None, all_pages=True,
                             dpi=72, output=Path("artifacts/regression"))
    assert all_pages == outputs
    for dpi in (35, 1201):
        with pytest.raises(PipelineError, match="DPI"):
            render_pages(case["root"], case["pdf"], pages="1", all_pages=False,
                         dpi=dpi, output=Path("artifacts/regression"))


def test_unrepresentable_integer_bbox_fails_explicitly(case, monkeypatch) -> None:
    override(case, monkeypatch)["bbox"] = [0, 0, 10 ** 1000, 1]
    with pytest.raises(PipelineError, match="invalid bbox"):
        render(case)


def test_tiny_positive_region_is_not_rejected_by_table_tolerance(case, monkeypatch) -> None:
    override(case, monkeypatch)["bbox"] = [20, 30, 20.125, 30.125]
    result = render(case, dpi=72)
    assert result["bbox"] == [20, 30, 20.125, 30.125]
    assert (result["pixel_width"], result["pixel_height"]) == (1, 1)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_exact_page_extent_allowed_as_region(tmp_path, monkeypatch, rotation) -> None:
    case = make_case(tmp_path, rotation)
    override(case, monkeypatch)["bbox"] = [0, 0, 300, 200]
    result = render(case, dpi=72)
    assert result["input_scope"] == "region"
    assert (result["pixel_width"], result["pixel_height"]) == (
        (300, 200) if rotation in (0, 180) else (200, 300)
    )


def test_query_core_import_and_selected_commands_remain_stdlib_only(case) -> None:
    imported = subprocess.run(
        [sys.executable, "-S", "-c", "import sys; import tools.query_core.query; "
         "assert 'fitz' not in sys.modules and 'pymupdf' not in sys.modules"],
        check=True, capture_output=True, text=True,
    )
    assert imported.stdout == imported.stderr == ""
    for command, argument in [("semantic-find", "selected-region"),
                              ("architectural-context", ENTITY), ("vision-candidates", ENTITY)]:
        completed = subprocess.run(
            [sys.executable, "-S", "-m", "tools.query_core", command,
             str(case["database"]), argument], check=True, capture_output=True, text=True,
        )
        assert completed.stderr == ""
        assert json.loads(completed.stdout) is not None
