"""Portable SQLite-backed architectural query core."""

from .build import build_database
from .errors import QueryCoreError
from .project_pdf_adapter import build_pdf_project_database
from .project_pdf_update import ProjectUpdateResult, update_pdf_project_database
from .query import QueryCore

SCHEMA_VERSION = 2
GENERATOR_VERSION = "query-core/2.0"

__all__ = [
    "GENERATOR_VERSION",
    "SCHEMA_VERSION",
    "ProjectUpdateResult",
    "QueryCore",
    "QueryCoreError",
    "build_database",
    "build_pdf_project_database",
    "update_pdf_project_database",
]
