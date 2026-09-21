from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from tools.pdf_pipeline.pipeline import process_all
from tools.query_core.cli import main as query_core_main
from tools.query_core.errors import QueryCoreError
from tools.query_core.project_pdf_adapter import build_pdf_project_database
from tools.query_core.project_pdf_update import (
    compare_pdf_project_database,
    update_pdf_project_database,
)


def _pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def _manifest(root: Path) -> dict:
    return json.loads((root / "projects/example/manifest.json").read_text())


def _entry(root: Path, identity: str) -> dict:
    return next(
        item for item in _manifest(root)["documents"] if item["source_file"] == identity
    )


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    _pdf(tmp_path / "projects/example/source/a.pdf", "alpha")
    _pdf(tmp_path / "projects/example/source/b.pdf", "beta")
    process_all(tmp_path)
    database = build_pdf_project_database(
        tmp_path, Path("projects/example"), tmp_path / "project.sqlite"
    )
    return tmp_path, database


def test_noop_comparison_is_read_only(project: tuple[Path, Path]) -> None:
    root, database = project
    database_bytes = database.read_bytes()
    manifest = root / "projects/example/manifest.json"
    manifest_bytes = manifest.read_bytes()

    report = compare_pdf_project_database(root, Path("projects/example"), database)

    assert report.added == report.changed == report.removed == ()
    assert tuple(item.identity for item in report.unchanged) == (
        "projects/example/source/a.pdf",
        "projects/example/source/b.pdf",
    )
    assert report.duplicate_byte_groups == ()
    assert database.read_bytes() == database_bytes
    assert manifest.read_bytes() == manifest_bytes


def test_report_exposes_exact_revision_sha_transition_and_matches_update(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    old_a_sha = _entry(root, "projects/example/source/a.pdf")["source_sha256"]
    source_a = root / "projects/example/source/a.pdf"
    source_a.unlink()
    _pdf(source_a, "alpha revised")
    (root / "projects/example/source/b.pdf").unlink()
    _pdf(root / "projects/example/source/c.pdf", "gamma")
    process_all(root)
    new_a_sha = _entry(root, "projects/example/source/a.pdf")["source_sha256"]

    database_bytes = database.read_bytes()
    report = compare_pdf_project_database(root, Path("projects/example"), database)

    assert [item.identity for item in report.added] == [
        "projects/example/source/c.pdf"
    ]
    assert [item.identity for item in report.removed] == [
        "projects/example/source/b.pdf"
    ]
    assert [item.identity for item in report.changed] == [
        "projects/example/source/a.pdf"
    ]
    assert report.changed[0].previous_sha256 == old_a_sha
    assert report.changed[0].current_sha256 == new_a_sha
    assert old_a_sha != new_a_sha
    assert report.unchanged == ()
    assert database.read_bytes() == database_bytes

    result = update_pdf_project_database(root, Path("projects/example"), database)
    assert result.added == tuple(item.identity for item in report.added)
    assert result.changed == tuple(item.identity for item in report.changed)
    assert result.removed == tuple(item.identity for item in report.removed)
    assert result.unchanged == tuple(item.identity for item in report.unchanged)


def test_path_rename_is_added_plus_removed_not_changed(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    old = root / "projects/example/source/b.pdf"
    payload = old.read_bytes()
    old.unlink()
    renamed = root / "projects/example/source/archive/b-renamed.pdf"
    renamed.parent.mkdir(parents=True)
    renamed.write_bytes(payload)
    process_all(root)

    report = compare_pdf_project_database(root, Path("projects/example"), database)

    assert [item.identity for item in report.added] == [
        "projects/example/source/archive/b-renamed.pdf"
    ]
    assert [item.identity for item in report.removed] == [
        "projects/example/source/b.pdf"
    ]
    assert report.changed == ()
    assert report.added[0].current_sha256 == report.removed[0].previous_sha256


def test_duplicate_byte_groups_are_deterministic_and_do_not_collapse_identity(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    original = root / "projects/example/source/a.pdf"
    first_copy = root / "projects/example/source/archive/a-copy-1.pdf"
    second_copy = root / "projects/example/source/archive/a-copy-2.pdf"
    first_copy.parent.mkdir(parents=True)
    first_copy.write_bytes(original.read_bytes())
    second_copy.write_bytes(original.read_bytes())
    process_all(root)

    report = compare_pdf_project_database(root, Path("projects/example"), database)

    assert len(report.duplicate_byte_groups) == 1
    group = report.duplicate_byte_groups[0]
    assert group.source_sha256 == _entry(root, "projects/example/source/a.pdf")[
        "source_sha256"
    ]
    assert group.identities == (
        "projects/example/source/a.pdf",
        "projects/example/source/archive/a-copy-1.pdf",
        "projects/example/source/archive/a-copy-2.pdf",
    )
    assert tuple(item.identity for item in report.added) == (
        "projects/example/source/archive/a-copy-1.pdf",
        "projects/example/source/archive/a-copy-2.pdf",
    )


def test_comparison_rejects_stale_authoritative_source_without_mutation(
    project: tuple[Path, Path],
) -> None:
    root, database = project
    database_bytes = database.read_bytes()
    source = root / "projects/example/source/a.pdf"
    source.write_bytes(source.read_bytes() + b"stale")

    with pytest.raises(QueryCoreError, match="source PDF byte SHA-256 mismatch"):
        compare_pdf_project_database(root, Path("projects/example"), database)
    assert database.read_bytes() == database_bytes


def test_compare_cli_outputs_deterministic_json(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root, database = project
    copy = root / "projects/example/source/a-copy.pdf"
    copy.write_bytes((root / "projects/example/source/a.pdf").read_bytes())
    process_all(root)

    exit_code = query_core_main(
        [
            "compare-pdf-project",
            "--repo-root",
            str(root),
            "projects/example",
            str(database),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["project_id"] == "example"
    assert payload["counts"]["added"] == 1
    assert payload["counts"]["changed"] == 0
    assert payload["counts"]["removed"] == 0
    assert payload["counts"]["duplicate_byte_groups"] == 1
    assert payload["added"][0]["identity"] == "projects/example/source/a-copy.pdf"
