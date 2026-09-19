from pathlib import Path

import pytest

from revit_exporter.clarity.assembly_selection import (
    SERVICE_TYPE,
    configured_fallback,
    find_loaded_exporter,
)


class _Name:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Assembly:
    def __init__(self, name: str, exposes_service: bool) -> None:
        self.name = name
        self.exposes_service = exposes_service

    def GetName(self) -> _Name:
        return _Name(self.name)

    def GetType(self, name: str, throw: bool) -> object | None:
        assert (name, throw) == (SERVICE_TYPE, False)
        return object() if self.exposes_service else None


def test_loaded_exporter_must_expose_service() -> None:
    wrong = _Assembly("LlmKnowledgeBase.Revit2025", False)
    expected = _Assembly("LlmKnowledgeBase.Revit2027", True)
    assert find_loaded_exporter([wrong, expected]) is expected


def test_exporter_fallback_requires_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not configured"):
        configured_fallback({})
    with pytest.raises(RuntimeError, match="must be absolute"):
        configured_fallback({"exporter_assembly": "relative/exporter.dll"})
    absolute = str(tmp_path / "exporter.dll")
    assert configured_fallback({"exporter_assembly": absolute}) == absolute
