from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.query_core.package import inspect_pdf, sha256
from tools.query_core.query import validate_database
from tools.query_core.revit_snapshot import finalize_revit_export

REQUIRED_FILES = (
    "drawing.pdf", "revit_snapshot.json", "export_manifest.json",
    "project.sqlite", "enhanced.pdf",
)
EXPORT_FILES = ("drawing.pdf", "revit_snapshot.json", "export_manifest.json")


@dataclass(frozen=True)
class UnattendedConfig:
    project_key: str
    export_root: Path
    publish_root: Path
    archive_timezone: ZoneInfo
    python_executable: Path
    repository_root: Path
    archive_retention_days: int | None = None
    include_links: bool = True


def load_config(path: str | Path) -> UnattendedConfig:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid unattended config: {error}") from error
    required = {"project_key", "export_root", "publish_root", "archive_timezone", "python_executable", "repository_root"}
    if not isinstance(raw, dict):
        raise ValueError("unattended config must be a JSON object")
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError(f"missing config fields: {', '.join(missing)}")
    key = raw["project_key"]
    if not isinstance(key, str) or not key or key in {".", ".."} or Path(key).name != key:
        raise ValueError("project_key must be one filesystem-safe path component")
    try:
        timezone = ZoneInfo(raw["archive_timezone"])
    except (TypeError, ZoneInfoNotFoundError) as error:
        raise ValueError("archive_timezone must be a valid IANA timezone") from error
    retention = raw.get("archive_retention_days")
    if retention is not None and (not isinstance(retention, int) or isinstance(retention, bool) or retention < 0):
        raise ValueError("archive_retention_days must be null or a non-negative integer")
    for flag in ("include_links",):
        if flag in raw and not isinstance(raw[flag], bool):
            raise ValueError(f"{flag} must be a boolean")
    def configured_path(name: str) -> Path:
        value = raw[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty path")
        configured = Path(value).expanduser()
        if not configured.is_absolute():
            raise ValueError(f"{name} must be an absolute path")
        return configured.resolve()
    return UnattendedConfig(key, configured_path("export_root"), configured_path("publish_root"), timezone,
                              configured_path("python_executable"), configured_path("repository_root"), retention,
                              raw.get("include_links", True))


def _validate(directory: Path) -> datetime:
    missing = [name for name in REQUIRED_FILES if not (directory / name).is_file() or (directory / name).stat().st_size == 0]
    if missing:
        raise ValueError(f"run is not publishable; missing or empty: {', '.join(missing)}")
    try:
        manifest = json.loads((directory / "export_manifest.json").read_text(encoding="utf-8"))
        if manifest["drawing_pdf"] != "drawing.pdf" or manifest["snapshot_file"] != "revit_snapshot.json":
            raise ValueError("manifest file names do not match the publication contract")
        if manifest["drawing_pdf_sha256"] != sha256(directory / "drawing.pdf"):
            raise ValueError("manifest drawing_pdf_sha256 does not match drawing.pdf")
        exported = datetime.fromisoformat(manifest["export_timestamp"])
        if exported.tzinfo is None:
            raise ValueError("manifest export_timestamp must include a timezone")
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"invalid export_manifest.json: {error}") from error
    validate_database(directory / "project.sqlite")
    details = inspect_pdf(directory / "enhanced.pdf")
    if details["payload_sha256"] != sha256(directory / "project.sqlite"):
        raise ValueError("enhanced.pdf embedded SQLite does not match project.sqlite")
    return exported


def _digest(directory: Path) -> dict[str, str]:
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in REQUIRED_FILES}


def _export_digest(directory: Path) -> dict[str, str]:
    return {name: sha256(directory / name) for name in EXPORT_FILES}


@contextmanager
def _project_lock(project: Path) -> Iterator[None]:
    lock = project / ".publish.lock"
    project.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise RuntimeError(f"publication already in progress for {project.name}") from error
    try:
        yield
    finally:
        lock.rmdir()


def _recover_latest(project: Path) -> None:
    latest, backup = project / "latest", project / ".latest-backup"
    if not backup.exists():
        return
    if not latest.exists():
        _validate(backup)
        os.replace(backup, latest)
        return
    try:
        _validate(latest)
    except Exception:
        _validate(backup)
        shutil.rmtree(latest)
        os.replace(backup, latest)
    else:
        shutil.rmtree(backup)


def finalize_and_publish(config: UnattendedConfig, run: str | Path) -> Path:
    directory = Path(run).resolve()
    if not directory.is_dir():
        raise ValueError(f"completed run does not exist: {directory}")
    project = config.publish_root / config.project_key
    with _project_lock(project):
        _recover_latest(project)
        finalize_revit_export(directory)
        exported = _validate(directory)
        date = exported.astimezone(config.archive_timezone).date().isoformat()
        archive = project / "archive" / date / directory.name
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists():
            _validate(archive)
            if _export_digest(archive) != _export_digest(directory):
                raise ValueError(f"archive already exists with different contents: {archive}")
        else:
            candidate_archive = Path(tempfile.mkdtemp(prefix=f".{directory.name}.", dir=archive.parent))
            try:
                for name in REQUIRED_FILES:
                    shutil.copy2(directory / name, candidate_archive / name)
                _validate(candidate_archive)
                os.replace(candidate_archive, archive)
            finally:
                shutil.rmtree(candidate_archive, ignore_errors=True)

        latest = project / "latest"
        candidate = Path(tempfile.mkdtemp(prefix=".latest-candidate.", dir=project))
        backup = project / ".latest-backup"
        try:
            for name in REQUIRED_FILES:
                shutil.copy2(archive / name, candidate / name)
            _validate(candidate)
            if backup.exists():
                raise RuntimeError("latest backup recovery did not complete")
            if latest.exists():
                os.replace(latest, backup)
            try:
                os.replace(candidate, latest)
            except Exception:
                if backup.exists() and not latest.exists():
                    os.replace(backup, latest)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        finally:
            shutil.rmtree(candidate, ignore_errors=True)
        try:
            _apply_retention(project / "archive", config.archive_retention_days, date)
        except OSError:
            pass
    return latest

def _apply_retention(archive_root: Path, days: int | None, today: str) -> None:
    if days is None:
        return
    current = datetime.fromisoformat(today).date()
    for child in archive_root.iterdir():
        try:
            age = (current - datetime.strptime(child.name, "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if child.is_dir() and age > days:
            shutil.rmtree(child)
