"""Portable SQLite-backed architectural query core."""

from importlib import import_module
from typing import Any

from .errors import QueryCoreError
from .query import QueryCore

SCHEMA_VERSION = 2
GENERATOR_VERSION = "query-core/2.0"

_LAZY_EXPORTS = {
    "build_database": (".build", "build_database"),
    "build_pdf_project_database": (".project_pdf_adapter", "build_pdf_project_database"),
    "BUNDLE_FORMAT": (".project_bundle", "BUNDLE_FORMAT"),
    "inspect_pdf_project_bundle": (".project_bundle", "inspect_pdf_project_bundle"),
    "package_pdf_project_bundle": (".project_bundle", "package_pdf_project_bundle"),
    "project_bundle_database": (".project_bundle", "project_bundle_database"),
    "project_bundle_source": (".project_bundle", "project_bundle_source"),
    "DuplicateByteGroup": (".project_pdf_update", "DuplicateByteGroup"),
    "ProjectComparisonReport": (".project_pdf_update", "ProjectComparisonReport"),
    "ProjectDocumentChange": (".project_pdf_update", "ProjectDocumentChange"),
    "ProjectUpdateResult": (".project_pdf_update", "ProjectUpdateResult"),
    "compare_pdf_project_database": (".project_pdf_update", "compare_pdf_project_database"),
    "update_pdf_project_database": (".project_pdf_update", "update_pdf_project_database"),
    "SemanticTableAdapterReport": (".semantic_table_adapter", "SemanticTableAdapterReport"),
    "SemanticTableMapping": (".semantic_table_adapter", "SemanticTableMapping"),
    "apply_semantic_table_mapping": (".semantic_table_adapter", "apply_semantic_table_mapping"),
}


def __getattr__(name: str) -> Any:
    """Load build and packaging helpers only when explicitly requested."""
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value

__all__ = [
    "DuplicateByteGroup",
    "BUNDLE_FORMAT",
    "GENERATOR_VERSION",
    "ProjectComparisonReport",
    "ProjectDocumentChange",
    "ProjectUpdateResult",
    "SCHEMA_VERSION",
    "SemanticTableAdapterReport",
    "SemanticTableMapping",
    "QueryCore",
    "QueryCoreError",
    "build_database",
    "build_pdf_project_database",
    "apply_semantic_table_mapping",
    "inspect_pdf_project_bundle",
    "package_pdf_project_bundle",
    "project_bundle_database",
    "project_bundle_source",
    "compare_pdf_project_database",
    "update_pdf_project_database",
]
