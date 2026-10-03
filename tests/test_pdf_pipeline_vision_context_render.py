"""Phase 5D fixed context policy against synthetic PDFs; no Vision execution."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import fitz
import pytest

from test_pdf_pipeline_vision_render import output_file, override, render as render_exact
from tools.pdf_pipeline import pipeline
from tools.pdf_pipeline.pipeline import PipelineError
from tools.pdf_pipeline.vision_render import render_vision_context_candidate
from tools.query_core.build import build_database
from tools.query_core.query import QueryCore, SCHEMA_VERSION
from tools.vision import VisionContractError, build_vision_inspection_request


EVIDENCE = [300.125, 220.25, 380.625, 260.75]
CONTEXT = [156.125, 76.25, 524.625, 404.75]


def make_context_case(root: Path, rotation: int = 0, cropped: bool = False) -> dict:
    pdf = root / "projects/example/source/synthetic.pdf"
    pdf.parent.mkdir(parents=True)
    with fitz.open() as document:
        page = document.new_page(width=900 if cropped else 800,
                                 height=700 if cropped else 600)
        offset = (40, 25, 40, 25) if cropped else (0, 0, 0, 0)
        for bbox, color in [(EVIDENCE, (1, 0, 0)),
                            ([320, 230, 330, 240], (0, 0, 1)),
                            ([410, 280, 420, 290], (0, 1, 0)),
                            ([600, 450, 620, 470], (0, 0, 0))]:
            page.draw_rect(fitz.Rect(bbox) + offset, color=None, fill=color)
        if cropped:
            page.set_cropbox(fitz.Rect(40, 25, 840, 625))
        page.set_rotation(rotation)
        document.save(pdf)
    records = {
        "project_id": "context-renderer-test", "created_from": "synthetic", "binding_mode": "project",
        "documents": [{"id": "document", "identity": "original/synthetic.pdf",
                       "title": "Synthetic context drawing",
                       "source_filename": "synthetic.pdf",
                       "source_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}],
        "evidence": [{"id": f"evidence-{scope}", "document_id": "document", "pdf_page": 1,
                      **dict(zip(("x_min", "y_min", "x_max", "y_max"),
                                 EVIDENCE if scope == "region" else [None] * 4)),
                      "coordinate_space": "pdf_points_top_left" if scope == "region" else None}
                     for scope in ("region", "page")],
        "semantic_entities": [{"id": f"selected-{scope}", "entity_class": "Door",
                               "label": "Synthetic context region",
                               "instance_or_type": "instance", "resolution_state": "exact",
                               "provenance": "synthetic", "evidence_id": f"evidence-{scope}"}
                              for scope in ("region", "page")],
    }
    database = build_database(records, root / "artifacts/project.sqlite")
    with QueryCore(database) as core:
        candidates = {scope: core.get_vision_evidence_candidates(f"selected-{scope}")["candidates"][0]
                      for scope in ("region", "page")}
    return {"root": root, "pdf": pdf, "database": database, "candidates": candidates}


@pytest.fixture
def case(tmp_path: Path) -> dict:
    return make_context_case(tmp_path)


def render(case: dict, scope: str = "region", **kwargs) -> dict:
    return render_vision_context_candidate(
        case["root"], case["database"], f"selected-{scope}",
        case["candidates"][scope]["candidate_id"], case["pdf"],
        output=kwargs.pop("output", Path("artifacts/vision-context")), **kwargs,
    )


def assert_context(result: dict, evidence: list, context: list, edges: list[str]) -> None:
    assert result["stage"] == "V1"
    assert result["input_scope"] == "region"
    assert result["coordinate_space"] == "pdf_points_top_left"
    assert result["policy"] == "fixed_margin_v1"
    assert result["margin_pt"] == 144.0
    assert result["evidence_bbox"] == evidence
    assert result["context_bbox"] == context
    assert result["clamped_edges"] == edges
    assert context[0] <= evidence[0] < evidence[2] <= context[2]
    assert context[1] <= evidence[1] < evidence[3] <= context[3]
    assert context != evidence
    assert "bbox" not in result


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
def test_context_geometry_and_visible_markers(tmp_path, rotation, cropped) -> None:
    case = make_context_case(tmp_path, rotation, cropped)
    result = render(case, dpi=72)
    assert_context(result, EVIDENCE, CONTEXT, [])
    dimensions = (369, 329) if rotation in (0, 180) else (329, 369)
    assert (result["pixel_width"], result["pixel_height"]) == dimensions
    assert result["renderer"]["page_rotation"] == rotation
    image = fitz.Pixmap(str(output_file(case, result)))
    blue = {0: (169, 159), 90: (170, 169), 180: (200, 170), 270: (159, 200)}
    green = {0: (259, 209), 90: (120, 259), 180: (110, 120), 270: (209, 110)}
    assert image.pixel(*blue[rotation]) == (0, 0, 255)
    assert image.pixel(*green[rotation]) == (0, 255, 0)
    assert image.pixel(5, 5) == (255, 255, 255)
    # The black distractor outside the context is never included.
    samples = image.samples
    assert all(any(samples[index:index + 3]) for index in range(0, len(samples), 3))
    with fitz.open(case["pdf"]) as document:
        page = document[0]
        expected = page.get_pixmap(matrix=fitz.Matrix(1, 1),
                                   clip=fitz.Rect(CONTEXT) * page.rotation_matrix,
                                   alpha=False).tobytes("png")
    assert output_file(case, result).read_bytes() == expected
    assert result["output_sha256"] == hashlib.sha256(expected).hexdigest()
    assert render(case, dpi=72) == result


@pytest.mark.parametrize("bbox,context,edges", [
    ([10.25, 220.25, 90.75, 260.75], [0, 76.25, 234.75, 404.75], ["left"]),
    ([300.125, 10.25, 380.625, 50.75], [156.125, 0, 524.625, 194.75], ["top"]),
    ([730.125, 220.25, 790.625, 260.75], [586.125, 76.25, 800, 404.75], ["right"]),
    ([300.125, 550.25, 380.625, 590.75], [156.125, 406.25, 524.625, 600], ["bottom"]),
    ([0, 0, 20, 30], [0, 0, 164, 174], ["left", "top"]),
    ([780, 0, 800, 30], [636, 0, 800, 174], ["top", "right"]),
    ([0, 570, 20, 600], [0, 426, 164, 600], ["left", "bottom"]),
    ([780, 570, 800, 600], [636, 426, 800, 600], ["right", "bottom"]),
    ([144, 144, 656, 456], [0, 0, 800, 600], []),
    ([1, 1, 799, 599], [0, 0, 800, 600], ["left", "top", "right", "bottom"]),
])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
def test_exact_clamp_at_edges_and_corners(tmp_path, monkeypatch, bbox, context, edges,
                                        rotation, cropped) -> None:
    case = make_context_case(tmp_path, rotation, cropped)
    override(case, monkeypatch)["bbox"] = bbox
    result = render(case, dpi=72)
    assert_context(result, bbox, context, edges)
    with fitz.open(case["pdf"]) as document:
        expected = document[0].get_pixmap(matrix=fitz.Matrix(1, 1),
                                         clip=fitz.Rect(context) * document[0].rotation_matrix,
                                         alpha=False)
    assert (result["pixel_width"], result["pixel_height"]) == (expected.width, expected.height)
    assert output_file(case, result).read_bytes() == expected.tobytes("png")


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
def test_no_additional_context_rejected(tmp_path, monkeypatch, rotation, cropped) -> None:
    case = make_context_case(tmp_path, rotation, cropped)
    override(case, monkeypatch)["bbox"] = [0, 0, 800, 600]
    with pytest.raises(PipelineError, match="adds no context"):
        render(case)
    assert not (case["root"] / "artifacts/vision-context").exists()


def test_page_candidate_rejected(case) -> None:
    with pytest.raises(PipelineError, match="page candidate rejected"):
        render(case, "page")
    assert not (case["root"] / "artifacts/vision-context").exists()


@pytest.mark.parametrize("bbox", [None, [], [1, 2, 3], "1,2,3,4", [True, 1, 3, 4],
                                  [1, 2, 1, 4], [1, 2, 3, 2], [3, 2, 1, 4],
                                  [float("nan"), 0, 1, 1], [0, 0, float("inf"), 1],
                                  [0, float("-inf"), 1, 1], [0, 0, "3", 4],
                                  [0, 0, 10 ** 1000, 1]])
def test_invalid_bbox_rejected_before_expansion(case, monkeypatch, bbox) -> None:
    override(case, monkeypatch)["bbox"] = bbox
    with pytest.raises(PipelineError, match="invalid bbox"):
        render(case)
    assert not (case["root"] / "artifacts/vision-context").exists()


@pytest.mark.parametrize("bbox", [[-1, 30, 100, 70], [20, -1, 100, 70],
                                  [20, 30, 801, 70], [20, 30, 100, 601],
                                  [900, 700, 1000, 800]])
def test_evidence_outside_crop_is_never_clamped(case, monkeypatch, bbox) -> None:
    override(case, monkeypatch)["bbox"] = bbox
    with pytest.raises(PipelineError, match="out-of-bounds bbox"):
        render(case)
    assert not (case["root"] / "artifacts/vision-context").exists()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_evidence_in_media_box_but_outside_offset_crop_rejected(tmp_path, monkeypatch, rotation) -> None:
    case = make_context_case(tmp_path, rotation, cropped=True)
    override(case, monkeypatch)["bbox"] = [790, 30, 810, 70]
    with pytest.raises(PipelineError, match="out-of-bounds bbox"):
        render(case)


@pytest.mark.parametrize("space", [None, "pdf_points", "model_mm", ""])
def test_coordinate_space_rejected(case, monkeypatch, space) -> None:
    override(case, monkeypatch)["coordinate_space"] = space
    with pytest.raises(PipelineError, match="unsupported coordinate space"):
        render(case)


def test_unknown_scope_rejected(case, monkeypatch) -> None:
    override(case, monkeypatch)["input_scope"] = "other"
    with pytest.raises(PipelineError, match="unsupported candidate input scope"):
        render(case)


@pytest.mark.parametrize("sha,message", [(None, "SHA missing"), ("", "SHA missing"),
                                         ("bad", "SHA malformed"), (123, "SHA malformed"),
                                         ("A" * 64, "SHA malformed"), ("0" * 64, "SHA mismatch")])
def test_source_sha_rechecked(case, monkeypatch, sha, message) -> None:
    override(case, monkeypatch)["document"]["source_sha256"] = sha
    with pytest.raises(PipelineError, match=message):
        render(case)


def test_wrong_source_bytes_rejected(case) -> None:
    original = case["pdf"].read_bytes()
    with fitz.open(stream=original, filetype="pdf") as document:
        document[0].insert_text((10, 10), "different bytes")
        changed = document.tobytes()
    case["pdf"].write_bytes(changed)
    with pytest.raises(PipelineError, match="source SHA mismatch"):
        render(case)
    assert not (case["root"] / "artifacts/vision-context").exists()


def test_verified_bytes_used_even_if_path_changes(case, monkeypatch) -> None:
    baseline = render(case, dpi=72)
    real_read = Path.read_bytes
    original = real_read(case["pdf"])

    def replace_after_read(path):
        data = real_read(path)
        if path == case["pdf"]:
            path.write_bytes(b"externally replaced")
        return data

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    assert render(case, dpi=72) == baseline
    assert baseline["document"]["source_sha256"] == hashlib.sha256(original).hexdigest()


def test_renamed_copy_preserves_canonical_document(case) -> None:
    baseline = render(case, dpi=72)
    copy = case["root"] / "renamed.bin"
    copy.write_bytes(case["pdf"].read_bytes())
    case["pdf"] = copy
    assert render(case, dpi=72) == baseline
    assert baseline["document"] == case["candidates"]["region"]["document"]


def test_exact_selection_rejects_absent_or_duplicate_candidates(case, monkeypatch) -> None:
    candidate_id = case["candidates"]["region"]["candidate_id"]
    for entity, identifier, message in [("absent", candidate_id, "semantic entity not found"),
                                         ("selected-region", "absent", "candidate not found")]:
        with pytest.raises(PipelineError, match=message):
            render_vision_context_candidate(case["root"], case["database"], entity, identifier,
                                            case["pdf"], output=Path("artifacts/vision-context"))
    candidate = case["candidates"]["region"]
    monkeypatch.setattr(QueryCore, "get_vision_evidence_candidates",
                        lambda *_: {"candidates": [candidate, deepcopy(candidate)]})
    with pytest.raises(PipelineError, match="duplicate candidate ID"):
        render(case)


def test_small_fractional_evidence_preserved(case, monkeypatch) -> None:
    bbox = [20, 30, 20.125, 30.125]
    override(case, monkeypatch)["bbox"] = bbox
    assert_context(render(case, dpi=72), bbox, [0, 0, 164.125, 174.125], ["left", "top"])


def test_evidence_collapsed_by_renderer_is_rejected_before_expansion(case, monkeypatch) -> None:
    override(case, monkeypatch)["bbox"] = [20, 30, 20 + 1e-12, 30 + 1e-12]
    with pytest.raises(PipelineError, match="bbox in renderer coordinates"):
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


@pytest.mark.parametrize("kind", ["missing", "corrupt", "password"])
def test_unavailable_source_rejected(case, monkeypatch, kind) -> None:
    if kind == "missing":
        case["pdf"].unlink()
        message = "source PDF not found"
    else:
        if kind == "corrupt":
            data, message = b"not a PDF", "cannot open PDF"
        else:
            with fitz.open(case["pdf"]) as document:
                data = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                                        owner_pw="owner", user_pw="secret")
            message = "password-protected PDF"
        case["pdf"].write_bytes(data)
        override(case, monkeypatch)["document"]["source_sha256"] = hashlib.sha256(data).hexdigest()
    with pytest.raises(PipelineError, match=message):
        render(case)


def test_deterministic_metadata_input_immutability_and_exact_render_regression(case, monkeypatch) -> None:
    candidate = override(case, monkeypatch)
    candidate["document"]["source_filename"] = "危険 / drawing:?*.pdf"
    snapshot = deepcopy(candidate)
    before = {path: path.read_bytes() for path in (case["pdf"], case["database"])}
    exact = render_exact(case, dpi=72)
    exact_bytes = output_file(case, exact).read_bytes()
    first = render(case)
    data = output_file(case, first).read_bytes()
    assert (first["pixel_width"], first["pixel_height"]) == (1536, 1370)
    assert render(case) == first
    assert output_file(case, first).read_bytes() == data
    assert first["output_sha256"] == hashlib.sha256(data).hexdigest()
    assert first["candidate_id"] == candidate["candidate_id"]
    assert first["semantic_entity_id"] == "selected-region"
    assert first["pdf_page"] == 1
    assert first["renderer"] == {"name": "tools.pdf_pipeline.vision_render", "library": "PyMuPDF",
                                 "library_version": "1.28.2", "page_rotation": 0, "alpha": False}
    assert json.loads(json.dumps(first, allow_nan=False)) == first
    assert candidate == snapshot
    assert before == {path: path.read_bytes() for path in before}
    assert SCHEMA_VERSION == 2
    with sqlite3.connect(case["database"]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert render_exact(case, dpi=72) == exact
    assert output_file(case, exact).read_bytes() == exact_bytes
    assert output_file(case, exact) != output_file(case, first)
    assert "fixed_margin_v1" in output_file(case, first).name


def test_context_does_not_overwrite_exact_render_in_same_directory(case) -> None:
    directory = Path("artifacts/shared-vision")
    exact = render_exact(case, dpi=72, output=directory)
    exact_bytes = output_file(case, exact).read_bytes()
    context = render(case, dpi=72, output=directory)
    assert output_file(case, exact) != output_file(case, context)
    assert output_file(case, exact).read_bytes() == exact_bytes
    assert render_exact(case, dpi=72, output=directory) == exact
    assert render(case, dpi=72, output=directory) == context


def test_output_path_does_not_change_content_or_identity(case, tmp_path) -> None:
    first = render(case, dpi=72)
    other = render(case, dpi=72, output=tmp_path.parent / f"{tmp_path.name}-external")
    assert Path(other["output_path"]).is_absolute()
    assert first["output_path"] != other["output_path"]
    assert {k: v for k, v in first.items() if k != "output_path"} == {
        k: v for k, v in other.items() if k != "output_path"}
    assert output_file(case, first).read_bytes() == Path(other["output_path"]).read_bytes()


@pytest.mark.parametrize("failure", ["save", "replace", "render"])
def test_atomic_failure_preserves_existing_png(case, monkeypatch, failure) -> None:
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


@pytest.mark.parametrize("output", ["projects/example/source", "projects/example/knowledge",
                                    "tests/fixtures", "schema", ".", "../escape"])
def test_unsafe_output_rejected(case, output) -> None:
    with pytest.raises(PipelineError, match="artifact root"):
        render(case, output=Path(output))


@pytest.mark.parametrize("input_name", ["pdf", "database"])
def test_output_symlink_cannot_replace_inputs(case, input_name) -> None:
    result = render(case, dpi=72)
    target = output_file(case, result)
    target.unlink()
    before = case[input_name].read_bytes()
    target.symlink_to(case[input_name])
    with pytest.raises(PipelineError, match="would replace a read-only input"):
        render(case, dpi=72)
    assert case[input_name].read_bytes() == before


def test_fixed_policy_has_no_caller_margin(case) -> None:
    with pytest.raises(TypeError, match="margin"):
        render(case, margin=1.0)


def test_v1_result_cannot_be_used_as_v3_exact_evidence(case) -> None:
    result = render(case, dpi=72)
    with pytest.raises(VisionContractError, match="incomplete render result"):
        build_vision_inspection_request(result, output_file(case, result), "inspect")


def test_cli_explicit_selection_json_and_errors(case) -> None:
    command = [sys.executable, "-m", "tools.pdf_pipeline", "--repo-root", str(case["root"]),
               "render-context-candidate", "artifacts/project.sqlite", "selected-region",
               case["candidates"]["region"]["candidate_id"], "--pdf",
               "projects/example/source/synthetic.pdf", "--dpi", "72",
               "--output", "artifacts/vision-context"]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout) == render(case, dpi=72)
    for extra in (["--margin", "1"], []):
        invalid = command + extra
        if not extra:
            invalid[7:9] = ["selected-page", case["candidates"]["page"]["candidate_id"]]
        failed = subprocess.run(invalid, capture_output=True, text=True)
        assert failed.returncode == (2 if extra else 1)
        assert failed.stdout == ""
        assert ("unrecognized arguments" if extra else "page candidate rejected") in failed.stderr
    missing = command.copy()
    missing[8] = "absent"
    failed = subprocess.run(missing, capture_output=True, text=True)
    assert failed.returncode == 1
    assert failed.stdout == ""
    assert "candidate not found" in failed.stderr
