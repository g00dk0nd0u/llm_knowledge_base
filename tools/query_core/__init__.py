"""Portable SQLite-backed architectural query core."""

from .build import build_database
from .errors import QueryCoreError
from .project_pdf_adapter import build_pdf_project_database
from .project_bundle import (
    BUNDLE_FORMAT,
    inspect_pdf_project_bundle,
    package_pdf_project_bundle,
    project_bundle_database,
    project_bundle_source,
)
from .project_pdf_update import (
    DuplicateByteGroup,
    ProjectComparisonReport,
    ProjectDocumentChange,
    ProjectUpdateResult,
    compare_pdf_project_database,
    update_pdf_project_database,
)
from .query import QueryCore

SCHEMA_VERSION = 2
GENERATOR_VERSION = "query-core/2.0"

__all__ = [
    "DuplicateByteGroup",
    "BUNDLE_FORMAT",
    "GENERATOR_VERSION",
    "ProjectComparisonReport",
    "ProjectDocumentChange",
    "ProjectUpdateResult",
    "SCHEMA_VERSION",
    "QueryCore",
    "QueryCoreError",
    "build_database",
    "build_pdf_project_database",
    "inspect_pdf_project_bundle",
    "package_pdf_project_bundle",
    "project_bundle_database",
    "project_bundle_source",
    "compare_pdf_project_database",
    "update_pdf_project_database",
]
