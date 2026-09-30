from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.query_core.fixtures import build_synthetic_fixture
from tools.query_core.query import QueryCore


def _semantic_database(tmp_path: Path) -> Path:
    _, database = build_synthetic_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO semantic_entities "
            "(id,entity_class,label,instance_or_type,resolution_state,provenance,evidence_id) "
            "VALUES ('door-1','Door','D-101','instance','exact','synthetic','ev-shutter')"
        )
        connection.executemany(
            "INSERT INTO semantic_bindings "
            "(id,semantic_entity_id,source_kind,source_id,resolution_state,provenance,evidence_id) "
            "VALUES (?,?,?,?,?,'synthetic','ev-shutter')",
            [
                ('b-exact', 'door-1', 'element', 'element-sd03', 'exact'),
                ('b-ambiguous', 'door-1', 'element', 'element-dl03', 'ambiguous'),
            ],
        )
    return database


@pytest.fixture
def pdf_semantic_database(tmp_path: Path) -> Path:
    """Build source-neutral semantic facts over exact PDF-native records."""
    _, database = build_synthetic_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO pdf_pages (id,document_id,page_number,width_points,"
            "height_points,rotation,coordinate_space,provenance) VALUES "
            "('pdf-page-9','doc-drawings',9,612,792,0,'pdf_points_top_left','test')"
        )
        connection.execute(
            "INSERT INTO evidence VALUES "
            "('ev-pdf-shared','doc-drawings',NULL,NULL,9,10,20,40,30,"
            "'pdf_points_top_left')"
        )
        connection.execute(
            "INSERT INTO pdf_text_blocks VALUES "
            "('block-9','pdf-page-9',0,'D-101',10,20,40,30,"
            "'pdf_points_top_left','embedded_pdf_text','ev-pdf-shared')"
        )
        connection.execute(
            "INSERT INTO pdf_text_lines VALUES "
            "('line-9','block-9',0,'D-101',10,20,40,30,"
            "'pdf_points_top_left','embedded_pdf_text')"
        )
        connection.execute(
            "INSERT INTO pdf_text_spans VALUES "
            "('span-9','line-9',0,'D-101',11,21,39,29,"
            "'pdf_points_top_left','embedded_pdf_text',NULL,NULL,NULL)"
        )
        connection.execute(
            "INSERT INTO pdf_tables VALUES "
            "('table-9','pdf-page-9',0,2,2,5,15,100,100,'pdf_points_top_left',"
            "'derived_pdf_table','pymupdf_lines_strict','1','PyMuPDF','1')"
        )
        connection.executemany(
            "INSERT INTO pdf_table_cells VALUES "
            "(?,'table-9',?,?,1,1,?,?,?,?,?,'pdf_points_top_left',"
            "'derived_pdf_table')",
            [
                ('cell-header-a', 0, 0, '', 5, 15, 49, 19),
                ('cell-header-b', 0, 1, '', 50, 15, 100, 19),
                ('cell-door', 1, 0, 'D-101', 10, 20, 40, 30),
                ('cell-rating', 1, 1, '', 50, 20, 90, 30),
            ],
        )
        connection.execute(
            "INSERT INTO pdf_table_cell_spans VALUES ('cell-door','span-9',0)"
        )
        connection.executemany(
            "INSERT INTO semantic_entities "
            "(id,entity_class,label,instance_or_type,resolution_state,provenance) "
            "VALUES (?,?,?,'instance','exact','test')",
            [
                ('pdf-door-a', 'DoorScheduleRow', 'D-101'),
                ('pdf-door-b', 'Door', 'Other endpoint'),
            ],
        )
        connection.executemany(
            "INSERT INTO semantic_bindings "
            "(id,semantic_entity_id,source_kind,source_id,resolution_state,"
            "provenance,evidence_id) VALUES (?,?,?,?,?,'test',?)",
            [
                ('binding-a-cell', 'pdf-door-a', 'pdf_table_cell', 'cell-door',
                 'exact', 'ev-pdf-shared'),
                ('binding-b-element', 'pdf-door-b', 'element', 'element-dl03',
                 'exact', 'ev-roof'),
            ],
        )
        connection.executemany(
            "INSERT INTO semantic_properties "
            "(id,semantic_entity_id,source_name,source_value,value_type,scope,"
            "provenance,source_binding_id,evidence_id) "
            "VALUES (?,?,?,?,?,'schedule','test',?,?)",
            [
                ('property-a-rating', 'pdf-door-a', 'Fire Rating', '60 min',
                 'text', 'binding-a-cell', 'ev-pdf-shared'),
                ('property-b-only', 'pdf-door-b', 'Endpoint Only', 'must not leak',
                 'text', 'binding-b-element', 'ev-roof'),
            ],
        )
        connection.execute(
            "INSERT INTO relationships "
            "(id,source_kind,source_id,relation_type,target_kind,target_id,"
            "provenance,evidence_id) VALUES "
            "('relationship-source','pdf_table_cell','cell-door','references',"
            "'element','element-dl03','test','ev-pdf-shared')"
        )
        connection.execute(
            "INSERT INTO semantic_relationships "
            "(id,subject_semantic_entity_id,relation_type,"
            "object_semantic_entity_id,provenance,evidence_id,source_binding_id) "
            "VALUES ('relationship-semantic','pdf-door-a','related_to',"
            "'pdf-door-b','test','ev-pdf-shared','binding-a-cell')"
        )
        connection.execute(
            "INSERT INTO documents VALUES "
            "('doc-unrelated','unrelated-v1','Unrelated','unrelated.pdf',?)",
            ('f' * 64,),
        )
        connection.execute(
            "INSERT INTO evidence VALUES "
            "('ev-unrelated','doc-unrelated',NULL,NULL,1,NULL,NULL,NULL,NULL,NULL)"
        )
    return database


def test_architectural_context_keeps_ambiguity_and_deduplicates_evidence(
    tmp_path: Path,
) -> None:
    database = _semantic_database(tmp_path)
    with QueryCore(database) as core:
        result = core.get_architectural_evidence_context('door-1')
        repeated = core.get_architectural_evidence_context('door-1')

    assert result is not None
    assert result == repeated
    assert result['entity']['label'] == 'D-101'
    assert [row['id'] for row in result['bindings']] == ['b-ambiguous', 'b-exact']
    assert result['resolution']['ambiguous_binding_ids'] == ['b-ambiguous']
    assert result['resolution']['binding_counts'] == {
        'exact': 1, 'resolved_deterministically': 0, 'ambiguous': 1, 'unresolved': 0,
    }
    assert result['coverage']['drawing_occurrence_count'] > 0
    assert len(result['evidence']) == 1
    assert result['evidence'][0]['evidence_id'] == 'ev-shutter'
    assert result['coverage']['source_document_count'] == 1


def test_pdf_only_context_is_source_neutral_one_hop_and_traceable(
    pdf_semantic_database: Path,
) -> None:
    source_sha256 = hashlib.sha256(
        (pdf_semantic_database.parent / 'drawing.pdf').read_bytes()
    ).hexdigest()
    with QueryCore(pdf_semantic_database) as core:
        result = core.get_architectural_evidence_context('pdf-door-a')
        repeated = core.get_architectural_evidence_context('pdf-door-a')

    assert result == repeated
    assert result is not None
    assert result['entity']['id'] == 'pdf-door-a'
    assert [row['id'] for row in result['bindings']] == ['binding-a-cell']
    assert result['bindings'][0]['source_kind'] == 'pdf_table_cell'
    assert result['bindings'][0]['source_id'] == 'cell-door'
    assert result['properties'] == [{
        'fact_kind': 'semantic_property',
        'property_id': 'property-a-rating',
        'source_name': 'Fire Rating',
        'source_value': '60 min',
        'value_type': 'text',
        'raw_numeric_value': None,
        'numeric_value': None,
        'unit': None,
        'scope': 'schedule',
        'canonical_name': None,
        'provenance': 'test',
        'source_binding_ref': 'binding-a-cell',
        'evidence_refs': ['ev-pdf-shared'],
    }]

    occurrence = result['drawing_occurrences'][0]
    assert occurrence['source_kind'] == 'pdf_table_cell'
    assert occurrence['cell_id'] == 'cell-door'
    assert occurrence['pdf_page'] == 9
    assert occurrence['bbox'] == [10.0, 20.0, 40.0, 30.0]
    assert occurrence['document'] == {
        'id': 'doc-drawings',
        'identity': 'synthetic-drawings-v1',
        'source_filename': 'drawing.pdf',
        'source_sha256': source_sha256,
    }
    assert occurrence['navigation']['pdf_page'] == 9
    assert result['spatial_contexts'] == []

    relationships = result['relationships']
    assert [(row['fact_kind'], row['direction'], row['relation_type'])
            for row in relationships] == [
        ('query_core_relationship', 'outgoing', 'references'),
        ('semantic_relationship', 'outgoing', 'related_to'),
    ]
    assert all(row['evidence_refs'] == ['ev-pdf-shared'] for row in relationships)

    # The endpoint remains a relationship reference, but none of its facts expand.
    assert relationships[1]['object_semantic_entity_id'] == 'pdf-door-b'
    assert all(row['semantic_entity_id'] != 'pdf-door-b'
               for row in result['bindings'])
    assert all(row['source_value'] != 'must not leak'
               for row in result['properties'])
    assert all(row.get('binding', {}).get('semantic_entity_id') != 'pdf-door-b'
               for row in result['drawing_occurrences'])
    assert result['spatial_contexts'] == []

    # One shared source fact stays local to every fact and global evidence is unique.
    assert result['bindings'][0]['evidence_id'] == 'ev-pdf-shared'
    assert result['properties'][0]['evidence_refs'] == ['ev-pdf-shared']
    assert [row['evidence_id'] for row in result['evidence']] == ['ev-pdf-shared']
    evidence = result['evidence'][0]
    assert evidence['pdf_page'] == 9
    assert evidence['bbox'] == [10.0, 20.0, 40.0, 30.0]
    assert evidence['navigation']['pdf_page'] == 9

    # Only direct evidence/occurrences contribute documents; unrelated rows do not.
    assert result['source_documents'] == [{
        'id': 'doc-drawings',
        'identity': 'synthetic-drawings-v1',
        'source_filename': 'drawing.pdf',
        'source_sha256': source_sha256,
    }]


@pytest.mark.parametrize(
    ('missing_table', 'property_count', 'relationship_fact_kinds'),
    [
        ('semantic_properties', 0,
         ['query_core_relationship', 'semantic_relationship']),
        ('semantic_relationships', 1, ['query_core_relationship']),
    ],
)
def test_architectural_context_degrades_without_optional_semantic_tables(
    pdf_semantic_database: Path,
    tmp_path: Path,
    missing_table: str,
    property_count: int,
    relationship_fact_kinds: list[str],
) -> None:
    database = tmp_path / f'without-{missing_table}.sqlite'
    database.write_bytes(pdf_semantic_database.read_bytes())
    with sqlite3.connect(database) as connection:
        connection.execute(f'DROP TABLE {missing_table}')

    with QueryCore(database) as core:
        result = core.get_architectural_evidence_context('pdf-door-a')
        tables = {
            row['name'] for row in core.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert result is not None
    assert result['entity']['id'] == 'pdf-door-a'
    assert [row['id'] for row in result['bindings']] == ['binding-a-cell']
    assert len(result['properties']) == property_count
    assert [row['fact_kind'] for row in result['relationships']] == (
        relationship_fact_kinds
    )
    assert missing_table not in tables


def test_architectural_context_cli_prints_api_json_and_missing_entity_is_null(
    tmp_path: Path,
) -> None:
    database = _semantic_database(tmp_path)
    completed = subprocess.run(
        [sys.executable, '-S', '-m', 'tools.query_core', 'architectural-context',
         str(database), 'door-1'],
        check=True, capture_output=True, text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload['entity']['id'] == 'door-1'
    assert payload['status'] == 'ok'
    missing = subprocess.run(
        [sys.executable, '-S', '-m', 'tools.query_core', 'architectural-context',
         str(database), 'absent'],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(missing.stdout) is None
