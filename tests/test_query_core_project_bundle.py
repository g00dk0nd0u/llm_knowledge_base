from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from tools.pdf_pipeline.pipeline import process_all
from tools.query_core.cli import main as query_core_main
from tools.query_core.errors import QueryCoreError
from tools.query_core.project_bundle import (
    BUNDLE_FORMAT,
    inspect_pdf_project_bundle,
    package_pdf_project_bundle,
    project_bundle_database,
    project_bundle_source,
)
from tools.query_core.project_pdf_adapter import build_pdf_project_database


def _pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text, fontname="japan" if "日本" in text else "helv")
    document.save(path)
    document.close()


@pytest.fixture
def bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "repository"
    _pdf(root / "projects/example/source/z.pdf", "English fire resistance")
    _pdf(root / "projects/example/source/nested/a.pdf", "日本語検索")
    process_all(root)
    database = build_pdf_project_database(
        root, Path("projects/example"), tmp_path / "project.sqlite"
    )
    output = tmp_path / "bundle"
    package_pdf_project_bundle(root, Path("projects/example"), database, output)
    return root, database, output


def test_packages_deterministic_nested_byte_exact_bundle(
    bundle: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, database, output = bundle
    manifest_bytes = (output / "bundle.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["bundle_format"] == BUNDLE_FORMAT
    assert [item["identity"] for item in manifest["documents"]] == sorted(
        item["identity"] for item in manifest["documents"]
    )
    assert (output / "project.sqlite").read_bytes() == database.read_bytes()
    assert (output / "sources/nested/a.pdf").read_bytes() == (
        root / "projects/example/source/nested/a.pdf"
    ).read_bytes()
    second = tmp_path / "second"
    package_pdf_project_bundle(root, Path("projects/example"), database, second)
    assert (second / "bundle.json").read_bytes() == manifest_bytes
    for relative in ("project.sqlite", "sources/z.pdf", "sources/nested/a.pdf"):
        assert (second / relative).read_bytes() == (output / relative).read_bytes()
    assert inspect_pdf_project_bundle(output) == manifest


def test_bundle_cli_searches_english_and_japanese_after_move(
    bundle: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root, _database, output = bundle
    moved = tmp_path / "portable"
    output.rename(moved)
    assert query_core_main(["search", str(moved), "fire resistance"]) == 0
    assert "English fire resistance" in capsys.readouterr().out
    assert query_core_main(["search", str(moved), "日本語検索"]) == 0
    assert "日本語検索" in capsys.readouterr().out


def test_source_resolver_verifies_only_selected_source(
    bundle: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, _database, output = bundle
    unrelated = output / "sources/z.pdf"
    unrelated.write_bytes(unrelated.read_bytes() + b"tampered")
    calls: list[Path] = []
    from tools.query_core import project_bundle as module

    original = module._sha256
    monkeypatch.setattr(module, "_sha256", lambda path: calls.append(path) or original(path))
    selected = project_bundle_source(
        output, "projects/example/source/nested/a.pdf"
    )
    assert selected == output / "sources/nested/a.pdf"
    assert calls == [selected]
    with pytest.raises(QueryCoreError, match="SHA-256 mismatch"):
        inspect_pdf_project_bundle(output)


def test_search_resolution_does_not_hash_sources(
    bundle: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, _database, output = bundle
    from tools.query_core import project_bundle as module

    original = module._sha256
    seen: list[Path] = []
    monkeypatch.setattr(module, "_sha256", lambda path: seen.append(path) or original(path))
    assert project_bundle_database(output) == output / "project.sqlite"
    assert seen == [output / "project.sqlite"]


@pytest.mark.parametrize("target", ["database", "database_manifest", "source"])
def test_tampering_is_rejected(
    bundle: tuple[Path, Path, Path], target: str
) -> None:
    _root, _database, output = bundle
    if target == "database":
        path = output / "project.sqlite"
        path.write_bytes(path.read_bytes() + b"bad")
    elif target == "database_manifest":
        path = output / "bundle.json"
        value = json.loads(path.read_text())
        value["database"]["sha256"] = "0" * 64
        path.write_text(json.dumps(value))
    else:
        path = output / "sources/nested/a.pdf"
        path.write_bytes(path.read_bytes() + b"bad")
    with pytest.raises(QueryCoreError, match="SHA-256 mismatch"):
        inspect_pdf_project_bundle(output)


def test_traversal_identity_collision_and_symlinks_are_rejected(
    bundle: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _root, _database, output = bundle
    manifest_path = output / "bundle.json"
    original = json.loads(manifest_path.read_text())
    value = json.loads(json.dumps(original))
    value["documents"][0]["bundle_path"] = "sources/../outside.pdf"
    manifest_path.write_text(json.dumps(value))
    with pytest.raises(QueryCoreError, match="canonical"):
        inspect_pdf_project_bundle(output)

    manifest_path.write_text(json.dumps(original))
    real = output / "sources/nested/a.pdf"
    elsewhere = tmp_path / "elsewhere.pdf"
    elsewhere.write_bytes(real.read_bytes())
    real.unlink()
    real.symlink_to(elsewhere)
    with pytest.raises(QueryCoreError, match="symlink"):
        inspect_pdf_project_bundle(output)


def test_existing_and_stale_outputs_are_rejected(
    bundle: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, database, output = bundle
    with pytest.raises(QueryCoreError, match="already exists"):
        package_pdf_project_bundle(root, Path("projects/example"), database, output)
    _pdf(root / "projects/example/source/new.pdf", "new")
    process_all(root)
    with pytest.raises(QueryCoreError, match="stale"):
        package_pdf_project_bundle(
            root, Path("projects/example"), database, tmp_path / "stale"
        )
    assert not (tmp_path / "stale").exists()


def test_output_protection_and_empty_project(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "projects/empty/source").mkdir(parents=True)
    process_all(root)
    database = build_pdf_project_database(
        root, Path("projects/empty"), tmp_path / "empty.sqlite"
    )
    with pytest.raises(QueryCoreError, match="protected"):
        package_pdf_project_bundle(
            root, Path("projects/empty"), database,
            root / "projects/empty/source/bundle",
        )
    output = tmp_path / "empty-bundle"
    package_pdf_project_bundle(root, Path("projects/empty"), database, output)
    assert inspect_pdf_project_bundle(output)["documents"] == []
    assert (output / "sources").is_dir()


def test_duplicate_bytes_remain_distinct(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _pdf(root / "projects/example/source/a.pdf", "same")
    second = root / "projects/example/source/nested/b.pdf"
    second.parent.mkdir(parents=True)
    second.write_bytes((root / "projects/example/source/a.pdf").read_bytes())
    process_all(root)
    database = build_pdf_project_database(
        root, Path("projects/example"), tmp_path / "same.sqlite"
    )
    output = tmp_path / "same-bundle"
    package_pdf_project_bundle(root, Path("projects/example"), database, output)
    manifest = inspect_pdf_project_bundle(output)
    assert len(manifest["documents"]) == 2
    assert len({item["source_sha256"] for item in manifest["documents"]}) == 1
    assert (output / "sources/a.pdf").is_file()
    assert (output / "sources/nested/b.pdf").is_file()
