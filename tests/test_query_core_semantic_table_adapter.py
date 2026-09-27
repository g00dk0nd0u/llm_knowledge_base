from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tools.query_core import QueryCore
from tools.query_core.build import build_database
from tools.query_core.errors import QueryCoreError
from tools.query_core.semantic_table_adapter import (
    SemanticTableMapping,
    apply_semantic_table_mapping,
)


def _records(headers: list[str], rows: list[list[str]], table_id: str = "table-1") -> dict:
    matrix = [headers, *rows]
    cells = []
    evidence, blocks, lines, spans, links = [], [], [], [], []
    source_order = 0
    for row_index, row in enumerate(matrix):
        for column_index, text in enumerate(row):
            cell_id = f"cell-{row_index}-{column_index}"
            cells.append({
                "id": cell_id, "table_id": table_id,
                "row_index": row_index, "column_index": column_index,
                "row_span": 1, "column_span": 1, "text": text,
                "x_min": float(column_index), "y_min": float(row_index),
                "x_max": float(column_index + 1), "y_max": float(row_index + 1),
                "coordinate_space": "pdf_points_top_left",
                "provenance": "derived_pdf_table",
            })
            if text:
                evidence_id, block_id = f"evidence-{source_order}", f"block-{source_order}"
                line_id, span_id = f"line-{source_order}", f"span-{source_order}"
                bbox = {"x_min": float(column_index), "y_min": float(row_index),
                        "x_max": float(column_index + 1), "y_max": float(row_index + 1)}
                evidence.append({"id": evidence_id, "document_id": "document", "pdf_page": 1,
                                 **bbox, "coordinate_space": "pdf_points_top_left"})
                blocks.append({"id": block_id, "page_id": "page", "order_index": source_order,
                               "text": text, **bbox, "coordinate_space": "pdf_points_top_left",
                               "provenance": "embedded_pdf_text", "evidence_id": evidence_id})
                lines.append({"id": line_id, "block_id": block_id, "order_index": 0, "text": text,
                              **bbox, "coordinate_space": "pdf_points_top_left",
                              "provenance": "embedded_pdf_text"})
                spans.append({"id": span_id, "line_id": line_id, "order_index": 0, "text": text,
                              **bbox, "coordinate_space": "pdf_points_top_left",
                              "provenance": "embedded_pdf_text"})
                links.append({"cell_id": cell_id, "span_id": span_id, "order_index": 0})
                source_order += 1
    return {
        "project_id": "adapter-test", "created_from": "fixture", "binding_mode": "project",
        "documents": [{"id": "document", "identity": "source/test.pdf", "title": "Test",
                       "source_filename": "test.pdf", "source_sha256": "a" * 64}],
        "pdf_pages": [{"id": "page", "document_id": "document", "page_number": 1,
                       "width_points": 100.0, "height_points": 100.0, "rotation": 0,
                       "provenance": "embedded_pdf"}],
        "pdf_tables": [{"id": table_id, "page_id": "page", "order_index": 0,
                        "row_count": len(matrix), "column_count": len(headers),
                        "x_min": 0.0, "y_min": 0.0, "x_max": float(len(headers)),
                        "y_max": float(len(matrix)), "coordinate_space": "pdf_points_top_left",
                        "provenance": "derived_pdf_table", "detection_method": "pymupdf_lines_strict",
                        "algorithm_version": "1", "library": "PyMuPDF", "library_version": "1"}],
        "pdf_table_cells": cells,
        "evidence": evidence, "pdf_text_blocks": blocks, "pdf_text_lines": lines,
        "pdf_text_spans": spans, "pdf_table_cell_spans": links,
    }


def _mapping(**overrides) -> SemanticTableMapping:
    values = dict(
        mapping_id="proof-v1", source_namespace="project:test/source:test.pdf",
        table_id="table-1", header_rows=(0,), entity_class="Door",
        key_columns=("建具番号",), instance_or_type="instance",
        property_scope="schedule", property_columns=("電気錠", "防火", "ガラス厚", "靴ずり"),
        number_column="建具番号",
    )
    values.update(overrides)
    return SemanticTableMapping(**values)


def test_door_fixture_preserves_source_and_is_sparse(tmp_path: Path) -> None:
    records = _records(
        ["建具番号", "電気錠", "防火", "ガラス厚", "靴ずり"],
        [["D-105", "EL560", "", "8mm", ""], ["D-106", "", "60分", "", "SUS"]],
    )
    mapping = _mapping(canonical_names={"電気錠": "access_control.lock_model"})
    report = apply_semantic_table_mapping(records, mapping)
    assert report.as_dict() == {"imported": 2, "skipped": 0, "ambiguous": [], "errors": []}
    assert [len([p for p in records["semantic_properties"] if p["semantic_entity_id"] == entity["id"]])
            for entity in records["semantic_entities"]] == [2, 2]
    assert {(p["source_name"], p["source_value"], p["canonical_name"])
            for p in records["semantic_properties"]} == {
                ("電気錠", "EL560", "access_control.lock_model"),
                ("ガラス厚", "8mm", None), ("防火", "60分", None), ("靴ずり", "SUS", None),
            }
    database = build_database(records, tmp_path / "door.sqlite")
    with QueryCore(database) as query:
        view = query.get_semantic_entity(records["semantic_entities"][0]["id"])
        persisted = [p for p in view["properties"] if p["fact_kind"] == "semantic_property"]
        assert all(p["source_binding_ref"] for p in persisted)
        assert {binding["source_kind"] for binding in view["bindings"]} == {"pdf_table_cell"}


def test_equipment_uses_same_adapter_and_defaults_canonical_name_to_absent() -> None:
    records = _records(
        ["機器番号", "機器名称", "容量", "電源", "備考"],
        [["AHU-1", "外調機", "20kW", "三相200V", ""], ["P-2", "ポンプ", "", "", "予備"]],
    )
    report = apply_semantic_table_mapping(records, _mapping(
        mapping_id="equipment-v1", entity_class="Equipment", key_columns=("機器番号",),
        number_column="機器番号", label_column="機器名称", property_columns="all_non_key",
    ))
    assert report.imported == 2
    assert [entity["label"] for entity in records["semantic_entities"]] == ["外調機", "ポンプ"]
    assert [len([p for p in records["semantic_properties"] if p["semantic_entity_id"] == entity["id"]])
            for entity in records["semantic_entities"]] == [3, 2]
    assert all(prop["canonical_name"] is None for prop in records["semantic_properties"])


def test_entity_ids_do_not_depend_on_row_order() -> None:
    headers = ["建具番号", "電気錠", "防火", "ガラス厚", "靴ずり"]
    first = _records(headers, [["D-105", "EL560", "", "", ""], ["D-106", "", "60分", "", ""]])
    second = _records(headers, [["D-106", "", "60分", "", ""], ["D-105", "EL560", "", "", ""]])
    apply_semantic_table_mapping(first, _mapping())
    apply_semantic_table_mapping(second, _mapping())
    assert {e["number"]: e["id"] for e in first["semantic_entities"]} == {
        e["number"]: e["id"] for e in second["semantic_entities"]
    }


def test_duplicate_and_missing_keys_are_reported_and_not_materialized() -> None:
    records = _records(
        ["建具番号", "電気錠", "防火", "ガラス厚", "靴ずり"],
        [["D-105", "A", "", "", ""], ["D-105", "B", "", "", ""], ["", "C", "", "", ""]],
    )
    report = apply_semantic_table_mapping(records, _mapping())
    assert report.imported == 0 and report.skipped == 3
    assert [(issue.row_index, issue.code) for issue in report.ambiguous] == [(1, "duplicate_key"), (2, "duplicate_key")]
    assert [(issue.row_index, issue.code) for issue in report.errors] == [(3, "missing_key")]
    assert records.get("semantic_entities", []) == []


def test_duplicate_requested_header_is_an_explicit_error() -> None:
    records = _records(["機器番号", "備考", "備考"], [["E-1", "A", "B"]])
    with pytest.raises(QueryCoreError, match="header is ambiguous.*備考"):
        apply_semantic_table_mapping(records, _mapping(
            entity_class="Equipment", key_columns=("機器番号",), number_column="機器番号",
            property_columns=("備考",),
        ))
    assert "semantic_entities" not in records


def test_fatal_later_row_collision_leaves_records_exactly_unchanged() -> None:
    rows = [["D-105", "A", "", "", ""], ["D-106", "B", "", "", ""]]
    generated = _records(["建具番号", "電気錠", "防火", "ガラス厚", "靴ずり"], rows)
    apply_semantic_table_mapping(generated, _mapping())
    later_entity_id = next(
        entity["id"] for entity in generated["semantic_entities"]
        if entity["number"] == "D-106"
    )

    records = _records(["建具番号", "電気錠", "防火", "ガラス厚", "靴ずり"], rows)
    records["semantic_entities"] = [{"id": "existing-entity"}]
    records["semantic_bindings"] = [{"id": "existing-binding"}]
    records["semantic_properties"] = [{"id": later_entity_id}]
    before = deepcopy(records)

    with pytest.raises(QueryCoreError, match="duplicate record ID"):
        apply_semantic_table_mapping(records, _mapping())

    assert records == before


def test_fatal_contract_error_does_not_create_empty_semantic_collections() -> None:
    records = _records(["機器番号", "備考", "備考"], [["E-1", "A", "B"]])
    before = deepcopy(records)

    with pytest.raises(QueryCoreError, match="header is ambiguous"):
        apply_semantic_table_mapping(records, _mapping(
            entity_class="Equipment", key_columns=("機器番号",),
            number_column="機器番号", property_columns=("備考",),
        ))

    assert records == before
