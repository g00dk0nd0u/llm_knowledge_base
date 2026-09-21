"""Shared PDF Pipeline consumer compatibility contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import QueryCoreError

SUPPORTED_PIPELINE_VERSIONS = ("1", "2")
_MARKERS = {version: f".pdf-pipeline-v{version}" for version in SUPPORTED_PIPELINE_VERSIONS}
_CREATED_FROM = {version: f"pdf-pipeline/{version}" for version in SUPPORTED_PIPELINE_VERSIONS}


def require_pipeline_version(value: Any, label: str = "pipeline_version") -> str:
    if value not in SUPPORTED_PIPELINE_VERSIONS:
        raise QueryCoreError(f"{label} must be one of: 1, 2")
    return value


def created_from(version: str) -> str:
    return _CREATED_FROM[require_pipeline_version(version)]


def require_created_from(value: Any, version: str, label: str = "created_from") -> None:
    expected = created_from(version)
    if value != expected:
        raise QueryCoreError(
            f"{label} mismatch: expected {expected}, got {value}; FULL REBUILD REQUIRED"
        )


def require_matching_marker(directory: Path, version: str) -> None:
    version = require_pipeline_version(version)
    present = sorted(
        path.name for path in directory.glob(".pdf-pipeline-v*") if path.is_file()
    )
    expected = _MARKERS[version]
    if present != [expected]:
        raise QueryCoreError(
            f"knowledge directory is unowned or ambiguous: must contain exactly "
            f"{expected} and no other "
            "recognized PDF Pipeline ownership marker"
        )
