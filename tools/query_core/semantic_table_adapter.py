"""Explicit, deterministic projection of one PDF table into semantic records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import QueryCoreError


@dataclass(frozen=True)
class SemanticTableMapping:
    """Caller-supplied interpretation of exactly one already extracted table."""

    mapping_id: str
    source_namespace: str
    table_id: str
    header_rows: tuple[int, ...]
    entity_class: str
    key_columns: tuple[str, ...]
    instance_or_type: Literal["instance", "type", "unknown"]
    property_scope: Literal["instance", "type", "sheet", "schedule", "project", "unknown"]
    property_columns: tuple[str, ...] | Literal["all_non_key"]
    label_column: str | None = None
    number_column: str | None = None
    canonical_names: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticTableRowIssue:
    row_index: int
    code: str
    message: str


@dataclass(frozen=True)
class SemanticTableAdapterReport:
    imported: int
    skipped: int
    ambiguous: tuple[SemanticTableRowIssue, ...]
    errors: tuple[SemanticTableRowIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "ambiguous": [issue.__dict__ for issue in self.ambiguous],
            "errors": [issue.__dict__ for issue in self.errors],
        }


def _stable_id(kind: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return f"{kind}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def apply_semantic_table_mapping(
    records: dict[str, Any], mapping: SemanticTableMapping
) -> SemanticTableAdapterReport:
    """Append semantic records for ``mapping`` and return deterministic row outcomes.

    Entity IDs hash the exact tuple ``(mapping_id, source_namespace,
    entity_class, key values)``.  Row indexes and non-key values are deliberately
    excluded, so reordering a logical table does not change entity identity.
    """
    _validate_mapping(mapping)
    table = next(
        (row for row in records.get("pdf_tables", []) if row.get("id") == mapping.table_id),
        None,
    )
    if table is None:
        raise QueryCoreError(f"semantic table mapping target does not exist: {mapping.table_id}")
    cells = {
        (row["row_index"], row["column_index"]): row
        for row in records.get("pdf_table_cells", [])
        if row.get("table_id") == mapping.table_id
    }
    if len(cells) != table["row_count"] * table["column_count"]:
        raise QueryCoreError("semantic table mapping target is not a complete cell grid")
    if any(row < 0 or row >= table["row_count"] for row in mapping.header_rows):
        raise QueryCoreError("semantic table mapping header row is outside the target table")

    headers: dict[str, list[int]] = {}
    for column in range(table["column_count"]):
        values = [cells[(row, column)]["text"] for row in mapping.header_rows]
        nonempty = [value for value in values if value.strip()]
        if len(nonempty) > 1:
            raise QueryCoreError(
                f"column {column} has multiple non-empty configured header cells"
            )
        if nonempty:
            headers.setdefault(nonempty[0], []).append(column)

    requested = set(mapping.key_columns) | set(mapping.canonical_names)
    requested.update(mapping.property_columns if mapping.property_columns != "all_non_key" else ())
    requested.update(value for value in (mapping.label_column, mapping.number_column) if value)
    resolved: dict[str, int] = {}
    for name in sorted(requested):
        columns = headers.get(name, [])
        if not columns:
            raise QueryCoreError(f"requested PDF table header is missing: {name!r}")
        if len(columns) != 1:
            raise QueryCoreError(
                f"requested PDF table header is ambiguous: {name!r} occurs in columns {columns}"
            )
        resolved[name] = columns[0]

    if mapping.property_columns == "all_non_key":
        key_indexes = {resolved[name] for name in mapping.key_columns}
        property_headers = []
        for column in range(table["column_count"]):
            if column in key_indexes:
                continue
            names = [name for name, indexes in headers.items() if column in indexes]
            if len(names) != 1:
                raise QueryCoreError(f"property column {column} does not have one exact header")
            if len(headers[names[0]]) != 1:
                raise QueryCoreError(
                    f"PDF table header is ambiguous for all_non_key: {names[0]!r}"
                )
            property_headers.append(names[0])
    else:
        property_headers = list(mapping.property_columns)
    if not set(mapping.canonical_names).issubset(property_headers):
        raise QueryCoreError("canonical names may only map configured property columns")

    data_rows = [row for row in range(table["row_count"]) if row not in mapping.header_rows]
    keys_by_row: dict[int, tuple[str, ...]] = {}
    errors: list[SemanticTableRowIssue] = []
    for row in data_rows:
        values = tuple(cells[(row, resolved[name])]["text"] for name in mapping.key_columns)
        if any(not value.strip() for value in values):
            errors.append(SemanticTableRowIssue(row, "missing_key", "configured key cell is blank"))
        else:
            keys_by_row[row] = values
    rows_by_key: dict[tuple[str, ...], list[int]] = {}
    for row, key in keys_by_row.items():
        rows_by_key.setdefault(key, []).append(row)
    ambiguous: list[SemanticTableRowIssue] = []
    duplicate_rows = {row for rows in rows_by_key.values() if len(rows) > 1 for row in rows}
    for row in sorted(duplicate_rows):
        ambiguous.append(SemanticTableRowIssue(
            row, "duplicate_key", "configured key values occur in more than one data row"
        ))

    existing_collections = (
        records.get("semantic_entities", []),
        records.get("semantic_bindings", []),
        records.get("semantic_properties", []),
    )
    existing_ids = {
        item["id"] for collection in existing_collections for item in collection
    }
    staged_entities: list[dict[str, Any]] = []
    staged_bindings: list[dict[str, Any]] = []
    staged_properties: list[dict[str, Any]] = []
    staged_ids: set[str] = set()
    imported = 0
    for row in data_rows:
        if row not in keys_by_row or row in duplicate_rows:
            continue
        key = keys_by_row[row]
        entity_id = _stable_id(
            "semantic-pdf", mapping.mapping_id, mapping.source_namespace,
            mapping.entity_class, key,
        )
        entity = {
            "id": entity_id,
            "entity_class": mapping.entity_class,
            "label": _optional_cell_text(cells, row, resolved, mapping.label_column),
            "number": _optional_cell_text(cells, row, resolved, mapping.number_column),
            "instance_or_type": mapping.instance_or_type,
            "resolution_state": "exact",
            "provenance": f"pdf_table_mapping:{mapping.mapping_id}",
        }
        generated: list[dict[str, Any]] = [entity]
        row_bindings = []
        for name in mapping.key_columns:
            cell = cells[(row, resolved[name])]
            binding = _binding(mapping, entity_id, cell)
            row_bindings.append(binding)
            generated.append(binding)
        row_properties = []
        for name in property_headers:
            cell = cells[(row, resolved.get(name, headers[name][0]))]
            if not cell["text"].strip():
                continue
            binding = _binding(mapping, entity_id, cell)
            prop = {
                "id": _stable_id("semantic-property", entity_id, cell["id"]),
                "semantic_entity_id": entity_id,
                "source_name": name,
                "source_value": cell["text"],
                "scope": mapping.property_scope,
                "canonical_name": mapping.canonical_names.get(name),
                "provenance": f"pdf_table_mapping:{mapping.mapping_id}",
                "source_binding_id": binding["id"],
            }
            row_bindings.append(binding)
            row_properties.append(prop)
            generated.extend((binding, prop))
        ids = [item["id"] for item in generated]
        if (
            len(ids) != len(set(ids))
            or existing_ids.intersection(ids)
            or staged_ids.intersection(ids)
        ):
            raise QueryCoreError("semantic table mapping would create a duplicate record ID")
        staged_ids.update(ids)
        staged_entities.append(entity)
        staged_bindings.extend(row_bindings)
        staged_properties.extend(row_properties)
        imported += 1
    if staged_entities:
        records.setdefault("semantic_entities", []).extend(staged_entities)
        records.setdefault("semantic_bindings", []).extend(staged_bindings)
        records.setdefault("semantic_properties", []).extend(staged_properties)
    return SemanticTableAdapterReport(
        imported=imported,
        skipped=len(data_rows) - imported,
        ambiguous=tuple(ambiguous),
        errors=tuple(errors),
    )


def _binding(mapping: SemanticTableMapping, entity_id: str, cell: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _stable_id("semantic-binding", entity_id, cell["id"]),
        "semantic_entity_id": entity_id,
        "source_kind": "pdf_table_cell",
        "source_id": cell["id"],
        "resolution_state": "exact",
        "provenance": f"pdf_table_mapping:{mapping.mapping_id}",
    }


def _optional_cell_text(cells, row, resolved, name):
    if name is None:
        return None
    value = cells[(row, resolved[name])]["text"]
    return value if value.strip() else None


def _validate_mapping(mapping: SemanticTableMapping) -> None:
    for label, value in (
        ("mapping_id", mapping.mapping_id), ("source_namespace", mapping.source_namespace),
        ("table_id", mapping.table_id), ("entity_class", mapping.entity_class),
    ):
        if not isinstance(value, str) or not value:
            raise QueryCoreError(f"semantic table mapping {label} must be non-empty")
    if not mapping.header_rows or len(set(mapping.header_rows)) != len(mapping.header_rows):
        raise QueryCoreError("semantic table mapping requires distinct header rows")
    if not mapping.key_columns or len(set(mapping.key_columns)) != len(mapping.key_columns):
        raise QueryCoreError("semantic table mapping requires distinct key columns")
    if mapping.instance_or_type not in {"instance", "type", "unknown"}:
        raise QueryCoreError("semantic table mapping has invalid instance_or_type")
    if mapping.property_scope not in {"instance", "type", "sheet", "schedule", "project", "unknown"}:
        raise QueryCoreError("semantic table mapping has invalid property_scope")
    if mapping.property_columns != "all_non_key" and len(set(mapping.property_columns)) != len(mapping.property_columns):
        raise QueryCoreError("semantic table mapping property columns must be distinct")
    if mapping.property_columns != "all_non_key" and set(mapping.property_columns) & set(mapping.key_columns):
        raise QueryCoreError("semantic table mapping key columns cannot also be properties")
