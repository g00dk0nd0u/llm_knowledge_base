"""Portable SQLite-backed architectural query core."""

from .build import build_database
from .errors import QueryCoreError
from .query import QueryCore

SCHEMA_VERSION = 2
GENERATOR_VERSION = "query-core/2.0"

__all__ = [
    "GENERATOR_VERSION",
    "SCHEMA_VERSION",
    "QueryCore",
    "QueryCoreError",
    "build_database",
]
