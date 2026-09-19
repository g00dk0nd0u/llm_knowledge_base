from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tools.unattended_export import publish


def config_file(tmp_path: Path, **updates: object) -> Path:
    data = {
        "project_key": "project-a", "export_root": str(tmp_path / "exports"),
        "publish_root": str(tmp_path / "published"), "archive_timezone": "Asia/Tokyo",
        "python_executable": "/usr/bin/python3", "repository_root": str(tmp_path),
        "archive_retention_days": None, "include_links": True, "finalize": True,
    }
    data.update(updates)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def completed_run(tmp_path: Path, name: str = "run-1") -> Path:
    run = tmp_path / "exports" / name
    run.mkdir(parents=True)
    for filename in ("drawing.pdf", "revit_snapshot.json", "export_manifest.json"):
        (run / filename).write_bytes((filename + name).encode())
    return run


def fake_finalize(run: Path) -> Path:
    (run / "project.sqlite").write_bytes(b"sqlite")
    (run / "enhanced.pdf").write_bytes(b"enhanced")
    return run / "enhanced.pdf"


def test_valid_and_invalid_config(tmp_path: Path) -> None:
    config = publish.load_config(config_file(tmp_path))
    assert config.project_key == "project-a"
    assert config.archive_timezone.key == "Asia/Tokyo"
    with pytest.raises(ValueError, match="publish_root"):
        publish.load_config(config_file(tmp_path, publish_root=None))
    with pytest.raises(ValueError, match="filesystem-safe"):
        publish.load_config(config_file(tmp_path, project_key="../bad"))


def test_finalize_publish_latest_archive_and_repeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "finalize_revit_export", fake_finalize)
    config = publish.load_config(config_file(tmp_path))
    run = completed_run(tmp_path)
    now = datetime(2026, 1, 2, 1, tzinfo=ZoneInfo("UTC"))
    latest = publish.finalize_and_publish(config, run, now=now)
    assert sorted(item.name for item in latest.iterdir()) == sorted(publish.REQUIRED_FILES)
    archive = config.publish_root / "project-a/archive/2026-01-02/run-1"
    assert archive.is_dir()
    assert publish.finalize_and_publish(config, run, now=now) == latest


def test_failed_finalization_preserves_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = publish.load_config(config_file(tmp_path))
    latest = config.publish_root / "project-a/latest"
    latest.mkdir(parents=True)
    (latest / "sentinel").write_text("old")
    monkeypatch.setattr(publish, "finalize_revit_export", lambda run: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(RuntimeError, match="bad"):
        publish.finalize_and_publish(config, completed_run(tmp_path))
    assert (latest / "sentinel").read_text() == "old"


def test_failed_promotion_rolls_back_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "finalize_revit_export", fake_finalize)
    config = publish.load_config(config_file(tmp_path))
    latest = config.publish_root / "project-a/latest"
    latest.mkdir(parents=True)
    (latest / "sentinel").write_text("old")
    real_replace = os.replace
    def fail_candidate(source: str | Path, target: str | Path) -> None:
        if Path(source).name.startswith(".latest-candidate") and Path(target).name == "latest":
            raise OSError("promotion failure")
        real_replace(source, target)
    monkeypatch.setattr(publish.os, "replace", fail_candidate)
    with pytest.raises(OSError, match="promotion failure"):
        publish.finalize_and_publish(config, completed_run(tmp_path))
    assert (latest / "sentinel").read_text() == "old"


def test_concurrency_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "finalize_revit_export", fake_finalize)
    config = publish.load_config(config_file(tmp_path))
    (config.publish_root / "project-a/.publish.lock").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="already in progress"):
        publish.finalize_and_publish(config, completed_run(tmp_path))


def test_timezone_archive_path_and_null_retention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "finalize_revit_export", fake_finalize)
    config = publish.load_config(config_file(tmp_path, archive_timezone="America/Los_Angeles"))
    old = config.publish_root / "project-a/archive/2000-01-01/keep"
    old.mkdir(parents=True)
    publish.finalize_and_publish(config, completed_run(tmp_path), now=datetime(2026, 1, 2, 2, tzinfo=ZoneInfo("UTC")))
    assert (config.publish_root / "project-a/archive/2026-01-01/run-1").is_dir()
    assert old.is_dir()
