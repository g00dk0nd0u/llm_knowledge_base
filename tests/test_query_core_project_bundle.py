from __future__ import annotations

import hashlib
import json
import sqlite3
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
from tools.query_core.pdf_adapter import build_pdf_database


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _refresh_database_sha(bundle_directory: Path) -> None:
    manifest_path = bundle_directory / "bundle.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["database"]["sha256"] = _sha256(bundle_directory / "project.sqlite")
    _write_manifest(manifest_path, manifest)


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
    assert calls == [output / "project.sqlite", selected]
    with pytest.raises(QueryCoreError, match="SHA-256 mismatch"):
        inspect_pdf_project_bundle(output)


def test_search_resolution_does_not_hash_sources(
    bundle: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _root, _database, output = bundle
    from tools.query_core import project_bundle as module

    original = module._sha256
    seen: list[Path] = []
    monkeypatch.setattr(module, "_sha256", lambda path: seen.append(path) or original(path))
    assert query_core_main(["search", str(output), "fire resistance"]) == 0
    assert "English fire resistance" in capsys.readouterr().out
    assert seen == [output / "project.sqlite"]


def test_source_resolver_rejects_selected_source_tamper(
    bundle: tuple[Path, Path, Path]
) -> None:
    _root, _database, output = bundle
    selected = output / "sources/nested/a.pdf"
    selected.write_bytes(selected.read_bytes() + b"tampered")
    with pytest.raises(QueryCoreError, match="source PDF SHA-256 mismatch"):
        project_bundle_source(output, "projects/example/source/nested/a.pdf")


def test_source_resolver_binds_coordinated_tamper_to_database(
    bundle: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _root, _database, output = bundle
    selected = output / "sources/nested/a.pdf"
    replacement = tmp_path / "replacement.pdf"
    _pdf(replacement, "different valid one-page PDF")
    selected.write_bytes(replacement.read_bytes())
    manifest_path = output / "bundle.json"
    manifest = json.loads(manifest_path.read_text())
    entry = next(
        item for item in manifest["documents"]
        if item["identity"] == "projects/example/source/nested/a.pdf"
    )
    entry["source_sha256"] = _sha256(selected)
    _write_manifest(manifest_path, manifest)

    with pytest.raises(QueryCoreError, match="does not match Query Core"):
        project_bundle_source(output, entry["identity"])


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


@pytest.mark.parametrize(
    "identity",
    [
        "/projects/example/source/a.pdf",
        "projects/example/source/../a.pdf",
        "projects/example/source/./a.pdf",
        "projects/other/source/a.pdf",
        "projects/example/knowledge/a.pdf",
        "projects/example/source/a.txt",
    ],
)
def test_malformed_logical_identities_are_rejected(
    bundle: tuple[Path, Path, Path], identity: str
) -> None:
    _root, _database, output = bundle
    manifest_path = output / "bundle.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["documents"][0]["identity"] = identity
    _write_manifest(manifest_path, manifest)
    with pytest.raises(QueryCoreError, match="identity|project"):
        inspect_pdf_project_bundle(output)


def test_database_symlink_is_rejected_by_runtime_and_inspector(
    bundle: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _root, _database, output = bundle
    database = output / "project.sqlite"
    external = tmp_path / "external.sqlite"
    external.write_bytes(database.read_bytes())
    database.unlink()
    database.symlink_to(external)
    with pytest.raises(QueryCoreError, match="symlink"):
        project_bundle_database(output)
    with pytest.raises(QueryCoreError, match="symlink"):
        inspect_pdf_project_bundle(output)


def test_manifest_symlink_is_rejected_by_all_bundle_consumers(
    bundle: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _root, _database, output = bundle
    manifest = output / "bundle.json"
    external = tmp_path / "external-bundle.json"
    manifest.replace(external)
    manifest.symlink_to(external)

    with pytest.raises(QueryCoreError, match="manifest may not be a symlink"):
        inspect_pdf_project_bundle(output)
    with pytest.raises(QueryCoreError, match="manifest may not be a symlink"):
        project_bundle_database(output)
    with pytest.raises(QueryCoreError, match="manifest may not be a symlink"):
        project_bundle_source(output, "projects/example/source/nested/a.pdf")


def test_case_insensitive_source_collision_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _pdf(root / "projects/example/source/Foo/A.pdf", "first")
    _pdf(root / "projects/example/source/foo/a.pdf", "second")
    process_all(root)
    database = build_pdf_project_database(
        root, Path("projects/example"), tmp_path / "collision.sqlite"
    )
    with pytest.raises(QueryCoreError, match="case-insensitive"):
        package_pdf_project_bundle(
            root, Path("projects/example"), database, tmp_path / "collision"
        )


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


def test_packaging_rejects_other_project_and_single_document_databases(
    bundle: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, _database, _output = bundle
    _pdf(root / "projects/other/source/other.pdf", "other")
    process_all(root)
    other_database = build_pdf_project_database(
        root, Path("projects/other"), tmp_path / "other.sqlite"
    )
    with pytest.raises(QueryCoreError, match="project_id mismatch"):
        package_pdf_project_bundle(
            root, Path("projects/example"), other_database, tmp_path / "wrong-project"
        )
    manifest = json.loads((root / "projects/example/manifest.json").read_text())
    single_database = build_pdf_database(
        root, root / manifest["documents"][0]["knowledge_path"],
        tmp_path / "single.sqlite",
    )
    with pytest.raises(QueryCoreError, match="binding_mode mismatch"):
        package_pdf_project_bundle(
            root, Path("projects/example"), single_database, tmp_path / "single"
        )


@pytest.mark.parametrize("mutation", ["missing", "extra", "sha", "page_count"])
def test_packaging_rejects_database_document_mismatches(
    bundle: tuple[Path, Path, Path], tmp_path: Path, mutation: str
) -> None:
    root, database, _output = bundle
    mutated = tmp_path / f"{mutation}.sqlite"
    mutated.write_bytes(database.read_bytes())
    with sqlite3.connect(mutated) as connection:
        if mutation == "missing":
            identity = "projects/example/source/z.pdf"
            document_id = connection.execute(
                "SELECT id FROM documents WHERE identity=?", (identity,)
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM search_content WHERE record_id IN ("
                "SELECT b.id FROM pdf_text_blocks b JOIN pdf_pages p ON p.id=b.page_id "
                "WHERE p.document_id=?)", (document_id,)
            )
            connection.execute("DELETE FROM pdf_pages WHERE document_id=?", (document_id,))
            connection.execute("DELETE FROM evidence WHERE document_id=?", (document_id,))
            connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
        elif mutation == "extra":
            connection.execute(
                "INSERT INTO documents(id,identity,title,source_filename,source_sha256) "
                "VALUES('doc-extra','projects/example/source/extra.pdf','extra','extra.pdf',?)",
                ("0" * 64,),
            )
        elif mutation == "sha":
            connection.execute(
                "UPDATE documents SET source_sha256=? WHERE identity LIKE '%/z.pdf'",
                ("0" * 64,),
            )
        else:
            connection.execute(
                "DELETE FROM pdf_pages WHERE document_id=(SELECT id FROM documents "
                "WHERE identity LIKE '%/z.pdf')"
            )
        connection.commit()
    with pytest.raises(QueryCoreError):
        package_pdf_project_bundle(
            root, Path("projects/example"), mutated, tmp_path / f"bundle-{mutation}"
        )


@pytest.mark.parametrize("mutation", ["sha", "page_count", "page_map"])
def test_inspector_rejects_manifest_database_mapping_mismatch(
    bundle: tuple[Path, Path, Path], mutation: str
) -> None:
    _root, _database, output = bundle
    manifest_path = output / "bundle.json"
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["documents"][0]
    if mutation == "sha":
        entry["source_sha256"] = "0" * 64
    elif mutation == "page_count":
        entry["page_count"] += 1
        entry["page_map"]["page_count"] += 1
    else:
        entry["page_map"]["query_core_page_start"] = 2
    _write_manifest(manifest_path, manifest)
    with pytest.raises(QueryCoreError, match="map|page_map"):
        inspect_pdf_project_bundle(output)


def test_inspector_rejects_non_contiguous_database_pages(
    bundle: tuple[Path, Path, Path]
) -> None:
    _root, _database, output = bundle
    with sqlite3.connect(output / "project.sqlite") as connection:
        connection.execute(
            "UPDATE pdf_pages SET page_number=2 WHERE document_id=("
            "SELECT id FROM documents WHERE identity LIKE '%/z.pdf')"
        )
        connection.commit()
    _refresh_database_sha(output)
    with pytest.raises(QueryCoreError, match="non-contiguous"):
        inspect_pdf_project_bundle(output)


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


@pytest.mark.parametrize(
    "relative",
    [
        "projects/example/knowledge/bundle",
        "projects/example/manifest.json",
    ],
)
def test_direct_protected_outputs_are_rejected(
    bundle: tuple[Path, Path, Path], relative: str
) -> None:
    root, database, _output = bundle
    protected = root / relative
    before = protected.read_bytes() if protected.is_file() else None
    with pytest.raises(QueryCoreError, match="protected"):
        package_pdf_project_bundle(
            root, Path("projects/example"), database, protected
        )
    if before is not None:
        assert protected.read_bytes() == before


@pytest.mark.parametrize("protected_name", ["source", "knowledge"])
def test_symlink_alias_into_protected_output_is_rejected(
    bundle: tuple[Path, Path, Path], tmp_path: Path, protected_name: str
) -> None:
    root, database, _output = bundle
    alias = tmp_path / f"{protected_name}-alias"
    alias.symlink_to(root / "projects/example" / protected_name, target_is_directory=True)
    with pytest.raises(QueryCoreError, match="protected"):
        package_pdf_project_bundle(
            root, Path("projects/example"), database, alias / "bundle"
        )


def test_mid_package_failure_is_atomic_and_preserves_inputs(
    bundle: tuple[Path, Path, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, database, _output = bundle
    from tools.query_core import project_bundle as module

    source_paths = sorted((root / "projects/example/source").rglob("*.pdf"))
    source_bytes = {path: path.read_bytes() for path in source_paths}
    database_bytes = database.read_bytes()
    final = tmp_path / "atomic-failure"
    monkeypatch.setattr(
        module, "inspect_pdf_project_bundle",
        lambda _path: (_ for _ in ()).throw(QueryCoreError("forced inspection failure")),
    )
    with pytest.raises(QueryCoreError, match="forced inspection failure"):
        package_pdf_project_bundle(root, Path("projects/example"), database, final)
    assert not final.exists()
    assert not list(tmp_path.glob(".atomic-failure.*"))
    assert database.read_bytes() == database_bytes
    assert {path: path.read_bytes() for path in source_paths} == source_bytes


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
