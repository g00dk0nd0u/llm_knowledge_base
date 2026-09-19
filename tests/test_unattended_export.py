from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tools.query_core.fixtures import create_synthetic_pdf, write_synthetic_revit_snapshot
from tools.query_core.revit_snapshot import finalize_revit_export
from tools.unattended_export import publish


def config_file(tmp_path: Path, **updates: object) -> Path:
    data = {
        "project_key": "project-a", "export_root": str(tmp_path / "exports"),
        "publish_root": str(tmp_path / "published"), "archive_timezone": "Asia/Tokyo",
        "python_executable": "/usr/bin/python3", "repository_root": str(tmp_path),
        "archive_retention_days": None, "include_links": True,
    }
    data.update(updates)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def completed_run(tmp_path: Path, name: str = "run-1", timestamp: str = "2026-01-01T16:00:00Z") -> Path:
    run = tmp_path / "exports" / name
    run.mkdir(parents=True)
    drawing = create_synthetic_pdf(run / "drawing.pdf")
    write_synthetic_revit_snapshot(drawing, run / "revit_snapshot.json")
    manifest = {
        "drawing_pdf": "drawing.pdf", "snapshot_file": "revit_snapshot.json",
        "drawing_pdf_sha256": publish.sha256(drawing), "export_timestamp": timestamp,
    }
    (run / "export_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run


def complete_files(run: Path) -> Path:
    finalize_revit_export(run)
    return run


def test_valid_and_invalid_config(tmp_path: Path) -> None:
    config = publish.load_config(config_file(tmp_path))
    assert config.project_key == "project-a"
    assert config.archive_timezone.key == "Asia/Tokyo"
    with pytest.raises(ValueError, match="publish_root"):
        publish.load_config(config_file(tmp_path, publish_root=None))
    with pytest.raises(ValueError, match="filesystem-safe"):
        publish.load_config(config_file(tmp_path, project_key="../bad"))
    with pytest.raises(ValueError, match="absolute"):
        publish.load_config(config_file(tmp_path, export_root="relative"))


def test_finalize_publish_uses_export_date_and_repeat_is_idempotent(tmp_path: Path) -> None:
    config = publish.load_config(config_file(tmp_path))
    run = completed_run(tmp_path)
    latest = publish.finalize_and_publish(config, run)
    assert sorted(item.name for item in latest.iterdir()) == sorted(publish.REQUIRED_FILES)
    archive = config.publish_root / "project-a/archive/2026-01-02/run-1"
    assert archive.is_dir()
    # Publication date is irrelevant; the manifest timestamp fixes the archive location.
    assert publish.finalize_and_publish(config, run) == latest
    assert [item.name for item in (config.publish_root / "project-a/archive").iterdir()] == ["2026-01-02"]


def test_failed_finalization_preserves_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = publish.load_config(config_file(tmp_path))
    latest = publish.finalize_and_publish(config, completed_run(tmp_path, "old"))
    before = publish._digest(latest)
    monkeypatch.setattr(publish, "finalize_revit_export", lambda run: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(RuntimeError, match="bad"):
        publish.finalize_and_publish(config, completed_run(tmp_path, "new"))
    assert publish._digest(latest) == before


def test_failed_promotion_rolls_back_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = publish.load_config(config_file(tmp_path))
    latest = publish.finalize_and_publish(config, completed_run(tmp_path, "old"))
    before = publish._digest(latest)
    real_replace = os.replace
    def fail_candidate(source: str | Path, target: str | Path) -> None:
        if Path(source).name.startswith(".latest-candidate") and Path(target).name == "latest":
            raise OSError("promotion failure")
        real_replace(source, target)
    monkeypatch.setattr(publish.os, "replace", fail_candidate)
    with pytest.raises(OSError, match="promotion failure"):
        publish.finalize_and_publish(config, completed_run(tmp_path, "new"))
    assert publish._digest(latest) == before


def test_simultaneous_second_publisher_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "published/project-a"
    with publish._project_lock(project):
        with pytest.raises(RuntimeError, match="already in progress"):
            with publish._project_lock(project):
                pass


def test_stale_lock_file_does_not_block_publisher(tmp_path: Path) -> None:
    project = tmp_path / "published/project-a"
    project.mkdir(parents=True)
    (project / ".publish.lock").write_text("terminated process metadata")
    with publish._project_lock(project):
        assert json.loads((project / ".publish.lock").read_text())["pid"] == os.getpid()


def test_normal_lock_release_allows_next_publication(tmp_path: Path) -> None:
    project = tmp_path / "published/project-a"
    with publish._project_lock(project):
        pass
    with publish._project_lock(project):
        pass


@pytest.mark.parametrize("corruption", ["manifest", "database", "enhanced"])
def test_corruption_preserves_previous_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str) -> None:
    config = publish.load_config(config_file(tmp_path))
    latest = publish.finalize_and_publish(config, completed_run(tmp_path, "old"))
    before = publish._digest(latest)
    run = complete_files(completed_run(tmp_path, "new"))
    target = {"manifest": "export_manifest.json", "database": "project.sqlite", "enhanced": "enhanced.pdf"}[corruption]
    (run / target).write_bytes(b"corrupt")
    monkeypatch.setattr(publish, "finalize_revit_export", lambda unused: None)
    with pytest.raises(Exception):
        publish.finalize_and_publish(config, run)
    assert publish._digest(latest) == before


def test_interrupted_promotion_restores_only_backup(tmp_path: Path) -> None:
    config = publish.load_config(config_file(tmp_path))
    latest = publish.finalize_and_publish(config, completed_run(tmp_path, "old"))
    before = publish._digest(latest)
    backup = latest.parent / ".latest-backup"
    os.replace(latest, backup)
    publish.finalize_and_publish(config, completed_run(tmp_path, "new", "2026-01-02T16:00:00Z"))
    assert latest.is_dir() and not backup.exists()
    assert before != publish._digest(latest)


def test_stale_backup_is_removed_only_after_latest_validation(tmp_path: Path) -> None:
    config = publish.load_config(config_file(tmp_path))
    latest = publish.finalize_and_publish(config, completed_run(tmp_path, "old"))
    backup = latest.parent / ".latest-backup"
    shutil.copytree(latest, backup)
    (latest / "project.sqlite").write_bytes(b"corrupt")
    publish.finalize_and_publish(config, completed_run(tmp_path, "new"))
    assert latest.is_dir() and not backup.exists()


def test_null_retention_does_not_remove_old_archive(tmp_path: Path) -> None:
    config = publish.load_config(config_file(tmp_path))
    old = config.publish_root / "project-a/archive/2000-01-01/keep"
    old.mkdir(parents=True)
    publish.finalize_and_publish(config, completed_run(tmp_path))
    assert old.is_dir()


def test_retention_failure_does_not_fail_published_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = publish.load_config(config_file(tmp_path, archive_retention_days=1))
    monkeypatch.setattr(publish, "_apply_retention", lambda *args: (_ for _ in ()).throw(OSError("busy")))
    assert publish.finalize_and_publish(config, completed_run(tmp_path)).is_dir()
