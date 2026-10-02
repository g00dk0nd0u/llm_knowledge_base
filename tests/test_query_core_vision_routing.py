"""Phase 5A routes existing source surfaces; it never executes Vision."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from test_query_core_architectural_evidence import pdf_semantic_database
from test_query_core_architectural_retrieval_acceptance import (
    _selected_id, architectural_database, pdf_database,
)
from test_query_core_semantic import semantic_contract_records
from test_query_core_semantic_drawing import semantic_drawing_database
from tools.query_core.query import QueryCore


def _refs(candidate: dict) -> set[tuple[str, str]]:
    return {(ref['kind'], ref['id']) for ref in candidate['source_refs']}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def test_door_routes_existing_model_schedule_and_shared_direct_facts(
    architectural_database: Path,
) -> None:
    with QueryCore(architectural_database) as core:
        selected = _selected_id(core.search_semantic_entities('D-101', entity_class='Door'))
        context = core.get_architectural_evidence_context(selected)
        result = core.get_vision_evidence_candidates(selected)
    assert result['status'] == 'ok'
    assert result['semantic_entity_id'] == selected
    assert result['capability'] == context['capability']
    assert result['coverage'] == {'candidate_count': 2}
    page, region = result['candidates']
    model = next(row for row in context['drawing_occurrences']
                 if row.get('appearance_kind') == 'model')
    schedule = next(row for row in context['drawing_occurrences']
                    if row.get('appearance_kind') == 'schedule')
    assert region['input_scope'] == 'region'
    assert region['bbox'] == model['bbox'] == [10.0, 20.0, 200.0, 80.0]
    assert region['bbox_quality'] == 'exact'
    assert page['input_scope'] == 'page'
    assert page['bbox'] is schedule['bbox'] is None
    assert page['bbox_quality'] == schedule['bbox_quality'] == 'page_only'
    assert page['navigation'] == schedule['navigation']
    for candidate in result['candidates']:
        assert candidate['document'] == context['source_documents'][0]
        assert candidate['document']['source_sha256']
        assert candidate['pdf_page'] == 1
        assert set(candidate) == {
            'candidate_id', 'input_scope', 'document', 'pdf_page', 'bbox',
            'bbox_quality', 'coordinate_space', 'navigation', 'source_refs', 'routing_reasons',
        }
    # Model and direct evidence occupy one identical physical region.
    refs = _refs(region)
    assert ('drawing_occurrence', 'appearance-door-model') in refs
    assert ('evidence', 'evidence-door-101-schedule') in refs
    assert ('semantic_entity', selected) in refs
    assert ('semantic_binding', 'binding-door-101') in refs
    assert ('semantic_property', 'property-door-101-lock') in refs
    assert ('query_core_parameter', 'parameter-door-101-lock') in refs
    assert ('query_core_relationship', 'relationship-door-space') in refs
    assert {'drawing_occurrence:model', 'direct_evidence',
            'semantic_property:evidence_ref', 'semantic_entity:evidence_ref'} <= set(
        region['routing_reasons']
    )
    for surface in (model, context['evidence'][0]):
        assert any(ref.get('navigation') == surface['navigation']
                   for ref in region['source_refs'])
    assert ('drawing_occurrence', 'appearance-door-schedule') in _refs(page)


def test_pdf_native_cells_route_exactly_without_host_or_spatial_inference(
    pdf_database: Path,
) -> None:
    with QueryCore(pdf_database) as core:
        selected = _selected_id(core.search_semantic_entities('D-105', entity_class='Door'))
        context = core.get_architectural_evidence_context(selected)
        result = core.get_vision_evidence_candidates(selected)
        assert core.connection.execute('SELECT count(*) FROM source_models').fetchone()[0] == 0
        assert context['spatial_contexts'] == []
        assert len(result['candidates']) == len(context['drawing_occurrences']) == 3
        for occurrence in context['drawing_occurrences']:
            native = core.get_pdf_table_cell(occurrence['source_id'])
            candidate = next(row for row in result['candidates']
                             if row['bbox'] == native['bbox'])
            assert candidate['input_scope'] == 'region'
            assert candidate['bbox_quality'] is None
            assert candidate['document'] == native['document']
            assert candidate['pdf_page'] == native['pdf_page'] == 1
            assert candidate['navigation'] == occurrence['navigation']
            assert ('drawing_occurrence', native['cell_id']) in _refs(candidate)
            prop = next((row for row in context['properties']
                         if row['source_binding_ref'] == occurrence['binding']['id']), None)
            if prop:
                assert ('semantic_property', prop['property_id']) in _refs(candidate)
                assert 'semantic_property:source_binding_ref' in candidate['routing_reasons']
    # Equal values/semantic properties must not collapse distinct cells.
    assert len({row['candidate_id'] for row in result['candidates']}) == 3


def test_shared_pdf_region_aggregates_fact_kinds_without_endpoint_expansion(
    pdf_semantic_database: Path,
) -> None:
    with QueryCore(pdf_semantic_database) as core:
        result = core.get_vision_evidence_candidates('pdf-door-a')
    assert result['coverage'] == {'candidate_count': 1}
    candidate = result['candidates'][0]
    assert candidate['bbox'] == [10.0, 20.0, 40.0, 30.0]
    assert candidate['pdf_page'] == 9
    assert {
        ('drawing_occurrence', 'cell-door'), ('evidence', 'ev-pdf-shared'),
        ('semantic_property', 'property-a-rating'),
        ('query_core_relationship', 'relationship-source'),
        ('semantic_relationship', 'relationship-semantic'),
    } <= _refs(candidate)
    assert 'semantic_relationship:evidence_ref' in candidate['routing_reasons']
    assert all('property-b-only' != ref['id'] for ref in candidate['source_refs'])
    assert candidate['document']['id'] == 'doc-drawings'


@pytest.mark.parametrize('fixture_name', ['architectural_database', 'pdf_database',
                                         'pdf_semantic_database'])
def test_byte_stable_order_reverse_scans_and_no_database_mutation(
    request: pytest.FixtureRequest, fixture_name: str,
) -> None:
    database = request.getfixturevalue(fixture_name)
    selected = {'architectural_database': 'semantic-door-101',
                'pdf_semantic_database': 'pdf-door-a'}.get(fixture_name)
    before = database.read_bytes()
    with QueryCore(database) as core:
        selected = selected or _selected_id(core.search_semantic_entities('D-105'))
        statements = []
        core.connection.set_trace_callback(statements.append)
        baseline = _json(core.get_vision_evidence_candidates(selected))
        assert _json(core.get_vision_evidence_candidates(selected)) == baseline
        assert core.connection.total_changes == 0
        assert all(sql.lstrip().upper().startswith(('SELECT', 'PRAGMA')) for sql in statements)
    with QueryCore(database) as core:
        core.connection.execute('PRAGMA reverse_unordered_selects=ON')
        assert _json(core.get_vision_evidence_candidates(selected)) == baseline
    assert database.read_bytes() == before


def test_direct_page_only_evidence_and_empty_or_missing_entity(
    architectural_database: Path,
) -> None:
    with sqlite3.connect(architectural_database) as connection:
        connection.execute('UPDATE evidence SET x_min=NULL,y_min=NULL,x_max=NULL,y_max=NULL')
        connection.execute(
            "INSERT INTO semantic_entities (id,entity_class,label,instance_or_type,"
            "resolution_state,provenance) VALUES ('empty','Door','Empty','instance','exact','test')"
        )
    with QueryCore(architectural_database) as core:
        result = core.get_vision_evidence_candidates('semantic-door-101')
        page = next(row for row in result['candidates'] if row['input_scope'] == 'page')
        assert page['bbox'] is None
        assert ('evidence', 'evidence-door-101-schedule') in _refs(page)
        assert ('drawing_occurrence', 'appearance-door-schedule') in _refs(page)
        assert core.get_vision_evidence_candidates('absent') is None
        empty = core.get_vision_evidence_candidates('empty')
        assert empty['status'] == 'ok'
        assert empty['candidates'] == []
        assert empty['coverage'] == {'candidate_count': 0}


@pytest.mark.parametrize('missing_table', ['semantic_properties', 'semantic_relationships',
                                        'semantic_projection'])
def test_legacy_optional_capabilities_need_no_migration(
    pdf_semantic_database: Path, missing_table: str,
) -> None:
    with sqlite3.connect(pdf_semantic_database) as connection:
        if missing_table == 'semantic_projection':
            for table in ('semantic_properties', 'semantic_relationships',
                          'semantic_bindings', 'semantic_entities'):
                connection.execute(f'DROP TABLE {table}')
        else:
            connection.execute(f'DROP TABLE {missing_table}')
    before = pdf_semantic_database.read_bytes()
    with QueryCore(pdf_semantic_database) as core:
        result = core.get_vision_evidence_candidates('pdf-door-a')
        if missing_table == 'semantic_projection':
            assert result is None  # Existing missing-semantic-capability behavior.
        else:
            assert result['coverage'] == {'candidate_count': 1}
        assert core.connection.execute('PRAGMA user_version').fetchone()[0] == 2
    assert pdf_semantic_database.read_bytes() == before


def test_sqlite_cli_stdlib_emits_only_deterministic_json(architectural_database: Path) -> None:
    def cli(entity_id: str) -> str:
        completed = subprocess.run(
            [sys.executable, '-S', '-m', 'tools.query_core', 'vision-candidates',
             str(architectural_database), entity_id],
            check=True, capture_output=True, text=True, encoding='utf-8',
        )
        assert completed.stderr == ''
        return completed.stdout
    first = cli('semantic-door-101')
    assert cli('semantic-door-101') == first
    with QueryCore(architectural_database) as core:
        assert json.loads(first) == core.get_vision_evidence_candidates('semantic-door-101')
    assert json.loads(cli('absent')) is None
    # A source number is not treated as a free-text discovery query.
    assert json.loads(cli('D-101')) is None


def test_span_and_duplicate_bindings_preserve_distinct_locations(
    semantic_drawing_database: Path,
) -> None:
    with QueryCore(semantic_drawing_database) as core:
        span = core.get_pdf_text_span('span-9')
        result = core.get_vision_evidence_candidates('sem-span')
        candidate, = result['candidates']
        assert candidate['bbox'] == span['bbox'] == [11.0, 21.0, 39.0, 29.0]
        assert candidate['document'] == span['document']
        assert candidate['input_scope'] == 'region'
        result = core.get_vision_evidence_candidates('sem-element')
        context = core.get_architectural_evidence_context('sem-element')
        assert len(context['drawing_occurrences']) == 8
        assert len(result['candidates']) == 4
        for candidate in result['candidates']:
            assert {('semantic_binding', 'binding-element-a'),
                    ('semantic_binding', 'binding-element-b')} <= _refs(candidate)
            assert ('semantic_binding', 'binding-ambiguous') not in _refs(candidate)
        boxes = [row['bbox'] for row in result['candidates']]
        assert [120.0, 150.0, 330.0, 240.0] in boxes
        assert [121.0, 150.0, 331.0, 240.0] in boxes


def test_occurrence_only_uses_canonical_sha(architectural_database: Path) -> None:
    with sqlite3.connect(architectural_database) as connection:
        connection.execute('UPDATE semantic_entities SET evidence_id=NULL')
        connection.execute('UPDATE semantic_bindings SET evidence_id=NULL')
        connection.execute("DELETE FROM semantic_bindings WHERE source_kind='evidence'")
        connection.execute('DELETE FROM semantic_properties')
        connection.execute('UPDATE parameters SET evidence_id=NULL')
        connection.execute('UPDATE relationships SET evidence_id=NULL')
    with QueryCore(architectural_database) as core:
        context = core.get_architectural_evidence_context('semantic-door-101')
        assert context['evidence'] == []
        assert all(row['document'].get('source_sha256') is None
                   for row in context['drawing_occurrences'])
        result = core.get_vision_evidence_candidates('semantic-door-101')
        assert result['coverage'] == {'candidate_count': 2}
        assert all(row['document'] == context['source_documents'][0]
                   for row in result['candidates'])


def test_routing_uses_only_context_and_is_independent_of_collection_order(
    architectural_database: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with QueryCore(architectural_database) as core:
        context = core.get_architectural_evidence_context('semantic-door-101')
        baseline = _json(core.get_vision_evidence_candidates('semantic-door-101'))
        for value in context.values():
            if isinstance(value, list):
                value.reverse()
        monkeypatch.setattr(core, 'get_architectural_evidence_context', lambda _: context)
        monkeypatch.setattr(core, '_rows', lambda *args: pytest.fail('Routing must reuse context'))
        assert _json(core.get_vision_evidence_candidates('semantic-door-101')) == baseline


@pytest.mark.parametrize('bbox', [[0, 0, 0, 1], [1, 0, 0, 1], [0, None, 1, 1],
                                  [0, 0, float('inf'), 1]])
def test_invalid_geometry_does_not_invent_page_fallback(
    architectural_database: Path, monkeypatch: pytest.MonkeyPatch, bbox: list,
) -> None:
    with QueryCore(architectural_database) as core:
        context = core.get_architectural_evidence_context('semantic-door-101')
        context['drawing_occurrences'] = []
        context['evidence'][0]['bbox'] = bbox
        monkeypatch.setattr(core, 'get_architectural_evidence_context', lambda _: context)
        result = core.get_vision_evidence_candidates('semantic-door-101')
        assert result['status'] == 'ok'
        assert result['candidates'] == []
