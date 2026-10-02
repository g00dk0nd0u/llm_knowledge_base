"""Phase 4C: discovery, explicit selection, and structured evidence retrieval.

Reuse the existing Door contract and PDF table fixtures. Add occurrences, a
duplicate identifier, and evidence references; stored topology stays unchanged.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_query_core_semantic import semantic_contract_records
from test_query_core_semantic_table_adapter import _mapping, _records
from tools.query_core.build import build_database
from tools.query_core.fixtures import create_synthetic_pdf
from tools.query_core.query import QueryCore, validate_database
from tools.query_core.semantic_table_adapter import apply_semantic_table_mapping


CONTEXT_COLLECTIONS = (
    'bindings', 'properties', 'relationships', 'spatial_contexts',
    'drawing_occurrences', 'evidence', 'source_documents',
)


@pytest.fixture
def architectural_database(tmp_path: Path, semantic_contract_records: dict) -> Path:
    records = semantic_contract_records
    source = create_synthetic_pdf(tmp_path / 'door-schedule.pdf')
    records['documents'][0]['source_sha256'] = hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    shared_evidence = 'evidence-door-101-schedule'
    records['semantic_entities'][0]['evidence_id'] = shared_evidence
    records['semantic_bindings'][0]['evidence_id'] = shared_evidence
    for parameter in records['parameters']:
        if parameter['entity_id'] == 'door-101':
            parameter['evidence_id'] = shared_evidence
    for relationship in records['relationships']:
        if relationship['source_id'] == 'door-101':
            relationship['evidence_id'] = shared_evidence

    # Two distinct identities share a number. Neither is selected by discovery.
    records['semantic_entities'].append({
        **records['semantic_entities'][1], 'id': 'semantic-door-102-second',
    })
    document_id = records['documents'][0]['id']
    records['sheets'] = [{
        'id': 'sheet-door', 'document_id': document_id,
        'number': 'A-601', 'name': 'Synthetic Door Sheet',
        'pdf_page': 1, 'export_order': 1, 'source_model_id': 'model-host',
        'source_unique_id': 'sheet-door-uid',
    }]
    records['views'] = [{
        'id': f'view-door-{kind}', 'document_id': document_id,
        'name': f'Synthetic Door {kind}', 'view_type': view_type,
        'source_model_id': 'model-host', 'source_unique_id': f'view-{kind}-uid',
    } for kind, view_type in [('model', 'Plan'), ('schedule', 'Schedule')]]
    records['entity_appearances'] = [{
        'id': f'appearance-door-{kind}', 'entity_kind': 'element',
        'entity_id': 'door-101', 'sheet_id': 'sheet-door',
        'view_id': f'view-door-{kind}', 'pdf_page': 1,
        'x_min': bbox[0], 'y_min': bbox[1], 'x_max': bbox[2], 'y_max': bbox[3],
        'coordinate_space': 'pdf_points_top_left', 'appearance_kind': kind,
        'bbox_quality': quality, 'provenance': 'acceptance_fixture',
    } for kind, bbox, quality in [
        ('schedule', [None] * 4, 'page_only'),
        ('model', [10.0, 20.0, 200.0, 80.0], 'exact'),
    ]]
    return build_database(records, tmp_path / 'project.sqlite')


@pytest.fixture
def pdf_database(tmp_path: Path) -> Path:
    # Existing PDF-native fixture and explicit mapping, with no Revit records.
    records = _records(
        ['建具番号', '電気錠', '防火', 'ガラス厚', '靴ずり'],
        [['D-105', 'EL560', '', '8mm', '']],
    )
    report = apply_semantic_table_mapping(records, _mapping())
    assert report.imported == 1
    assert report.errors == report.ambiguous == ()
    return build_database(records, tmp_path / 'pdf.sqlite')


def _selected_id(discovery: dict) -> str:
    assert discovery['status'] == 'exact_unique'
    assert len(discovery['candidates']) == 1
    return discovery['candidates'][0]['semantic_entity']['id']


def _assert_coverage(context: dict) -> None:
    assert context['status'] == 'ok'
    assert isinstance(context['entity'], dict)
    counts = (
        'binding_count', 'property_count', 'relationship_count',
        'spatial_context_count', 'drawing_occurrence_count', 'evidence_count',
        'source_document_count',
    )
    assert context['coverage'] == {
        count: len(context[collection])
        for count, collection in zip(counts, CONTEXT_COLLECTIONS, strict=True)
    }
    assert all(isinstance(context[key], list) for key in CONTEXT_COLLECTIONS)


def test_discovered_door_composes_existing_architectural_evidence(
    architectural_database: Path,
) -> None:
    with QueryCore(architectural_database) as core:
        discovery = core.search_semantic_entities('D-101', entity_class='Door')
        selected_id = _selected_id(discovery)
        context = core.get_architectural_evidence_context(selected_id)
        assert context is not None
        assert context['entity'] == discovery['candidates'][0]['semantic_entity']
        assert [match['match_kind'] for match in
                discovery['candidates'][0]['matches']] == [
            'number_exact', 'label_contains',
        ]
        _assert_coverage(context)

        # Also follow a fixture-native sparse source value through discovery.
        source_value = core.search_semantic_entities('EL160', entity_class='Door')
        assert _selected_id(source_value) == selected_id
        assert source_value['candidates'][0]['matches'][0] == {
            'match_kind': 'property_value_exact',
            'property_id': 'property-door-101-lock', 'source_name': '電気錠',
            'source_value': 'EL160', 'scope': 'schedule',
            'evidence_refs': ['evidence-door-101-schedule'],
        }
        assert core.get_architectural_evidence_context(
            _selected_id(source_value)
        ) == context

        assert context['spatial_contexts'] == core.get_semantic_spatial_context(
            selected_id
        )['spatial_contexts']
        assert context['drawing_occurrences'] == core.get_semantic_drawing_context(
            selected_id
        )['occurrences']

        # The fixture's to_space fact is a Room reference, not containment.
        door_binding = next(binding for binding in context['bindings']
                            if binding['source_kind'] == 'element')
        rooms = core.get_related_spaces(door_binding['source_id'])
        assert [(room['id'], room['kind']) for room in rooms] == [
            ('space-101', 'Room'),
        ]
        assert rooms[0]['contexts'][0]['relation_type'] == 'to_space'
        room_context = core.get_spatial_context('space', rooms[0]['id'])
        assert room_context['level']['id'] == 'level-01'
        assert room_context['level']['name'] == 'Level 01'
        assert room_context['contained_elements'] == []
        door_spatial = context['spatial_contexts'][0]['spatial_context']
        assert door_spatial['containment'] == []
        assert door_spatial['level'] is None
        assert core.connection.execute(
            'SELECT count(*) FROM semantic_relationships'
        ).fetchone()[0] == 0

    assert context['entity']['number'] == 'D-101'
    assert [row['id'] for row in context['bindings']] == [
        'binding-door-101', 'binding-door-101-schedule', 'binding-door-type-a',
    ]
    assert [(row['id'], row['relation_type']) for row in
            context['relationships']] == [
        ('relationship-door-space', 'to_space'),
        ('relationship-instance-of', 'instance_of'),
    ]
    lock = next(row for row in context['properties']
                if row.get('parameter_id') == 'parameter-door-101-lock')
    assert (lock['source_name'], lock['source_value'], lock['scope'],
            lock['provenance'], lock['evidence_refs']) == (
        '電気錠', 'EL560', 'instance', 'revit_snapshot',
        ['evidence-door-101-schedule'],
    )
    schedule_lock = next(row for row in context['properties']
                         if row.get('property_id') == 'property-door-101-lock')
    assert schedule_lock['source_name'] == '電気錠'
    assert schedule_lock['source_value'] == 'EL160'
    assert schedule_lock['provenance'] == 'door_schedule'
    assert schedule_lock['source_binding_ref'] == 'binding-door-101-schedule'
    assert schedule_lock['evidence_refs'] == ['evidence-door-101-schedule']

    occurrences = {row['appearance_kind']: row
                   for row in context['drawing_occurrences']
                   if 'appearance_kind' in row}
    assert set(occurrences) == {'model', 'schedule'}
    model = occurrences['model']
    assert model['bbox'] == [10.0, 20.0, 200.0, 80.0]
    assert model['navigation']['bbox'] == model['bbox']
    assert model['navigation']['can_zoom'] is True
    schedule = occurrences['schedule']
    assert schedule['bbox'] is None
    assert schedule['bbox_quality'] == 'page_only'
    assert schedule['navigation']['bbox'] is None
    assert schedule['navigation']['can_zoom'] is False
    for occurrence in occurrences.values():
        assert occurrence['pdf_page'] == occurrence['navigation']['pdf_page'] == 1
        assert occurrence['document']['identity'] == 'source/door-schedule.pdf'

    # Shared references stay local, while global evidence/documents are unique.
    assert len(context['evidence']) == 1
    evidence = context['evidence'][0]
    assert evidence['evidence_id'] == 'evidence-door-101-schedule'
    assert evidence['pdf_page'] == evidence['navigation']['pdf_page'] == 1
    assert evidence['bbox'] == evidence['navigation']['bbox'] == model['bbox']
    assert context['bindings'][0]['evidence_id'] == evidence['evidence_id']
    assert all(row['evidence_refs'] == [evidence['evidence_id']]
               for row in context['relationships'])
    expected_document = {
        'id': 'document-door-schedule', 'identity': 'source/door-schedule.pdf',
        'source_filename': 'door-schedule.pdf',
        'source_sha256': hashlib.sha256(
            (architectural_database.parent / 'door-schedule.pdf').read_bytes()
        ).hexdigest(),
    }
    # Protect Phase 4A: canonical documents retain SHA even when model/schedule
    # occurrence descriptors carry only document identity and filename.
    assert context['source_documents'] == [expected_document]
    assert evidence['document'] == expected_document
    assert evidence['navigation']['document']['identity'] == expected_document['identity']
    assert evidence['navigation']['document']['source_filename'] == 'door-schedule.pdf'
    assert validate_database(architectural_database)['schema_version'] == '2'


def test_pdf_native_discovery_to_context_needs_no_revit_host(pdf_database: Path) -> None:
    with QueryCore(pdf_database) as core:
        discovery = core.search_semantic_entities('D-105', entity_class='Door')
        selected_id = _selected_id(discovery)
        context = core.get_architectural_evidence_context(selected_id)
        assert context is not None
        assert context['entity'] == discovery['candidates'][0]['semantic_entity']
        _assert_coverage(context)
        assert core.connection.execute(
            'SELECT count(*) FROM source_models'
        ).fetchone()[0] == 0
        assert context['spatial_contexts'] == []
        assert context['relationships'] == []
        assert {row['source_kind'] for row in context['bindings']} == {'pdf_table_cell'}
        assert _selected_id(core.search_semantic_entities('EL560')) == selected_id

        # Empty cells create no sparse properties; original source strings survive.
        assert [(row['source_name'], row['source_value'])
                for row in context['properties']] == [('ガラス厚', '8mm'), ('電気錠', 'EL560')]
        for prop in context['properties']:
            assert prop['provenance'] == 'pdf_table_mapping:proof-v1'
            assert prop['scope'] == 'schedule'
            assert prop['canonical_name'] is None
            binding = next(row for row in context['bindings']
                           if row['id'] == prop['source_binding_ref'])
            native = core.get_pdf_table_cell(binding['source_id'])
            assert native['text'] == prop['source_value']
            assert native['source_spans']
            # The existing adapter records a cell binding, not an evidence_id.
            # Traceability goes through that binding and its native source spans.
            assert prop['evidence_refs'] == []

        assert context['drawing_occurrences']
        for occurrence in context['drawing_occurrences']:
            native = core.get_pdf_table_cell(occurrence['source_id'])
            assert occurrence['bbox'] == native['bbox']
            navigation = occurrence['navigation']
            assert navigation['pdf_page'] == occurrence['pdf_page'] == 1
            assert navigation['bbox'] == occurrence['bbox']
            assert navigation['can_zoom'] is True
            assert navigation['document']['identity'] == 'source/test.pdf'
            assert navigation['document']['source_filename'] == 'test.pdf'
            assert occurrence['document'] == context['source_documents'][0]
        assert context['evidence'] == []
        assert context['source_documents'][0]['source_sha256'] == 'a' * 64


@pytest.mark.parametrize(('query', 'status', 'ids'), [
    ('D-102', 'multiple_exact', ['semantic-door-102', 'semantic-door-102-second']),
    ('D-10', 'candidates', ['semantic-door-101', 'semantic-door-102',
                          'semantic-door-102-second']),
])
def test_discovery_safety_gates_never_expand_or_select(
    architectural_database: Path, monkeypatch: pytest.MonkeyPatch,
    query: str, status: str, ids: list[str],
) -> None:
    def unexpected_expansion(*args, **kwargs):
        pytest.fail('Discovery must leave context retrieval to explicit selection')

    for method in ('get_architectural_evidence_context', 'get_semantic_entity',
                   'get_semantic_spatial_context', 'get_semantic_drawing_context'):
        monkeypatch.setattr(QueryCore, method, unexpected_expansion)
    with QueryCore(architectural_database) as core:
        result = core.search_semantic_entities(query, entity_class='Door')
        assert core.search_semantic_entities(query, entity_class='Door') == result
    assert result['status'] == status
    assert set(result) == {'capability', 'query', 'entity_class', 'status', 'candidates'}
    assert [row['semantic_entity']['id'] for row in result['candidates']] == ids
    for candidate in result['candidates']:
        assert set(candidate) == {'semantic_entity', 'matches'}
        assert candidate['matches']
        assert not set(CONTEXT_COLLECTIONS) & candidate['semantic_entity'].keys()
        assert all(match['match_kind'].endswith(
            '_exact' if status == 'multiple_exact' else '_contains'
        ) for match in candidate['matches'])


@pytest.mark.parametrize('fixture_name', ['architectural_database', 'pdf_database'])
def test_discovery_and_context_repeat_with_stable_order_and_no_writes(
    request: pytest.FixtureRequest, fixture_name: str,
) -> None:
    database = request.getfixturevalue(fixture_name)
    query = 'D-101' if fixture_name == 'architectural_database' else 'D-105'
    before = database.read_bytes()
    with QueryCore(database) as core:
        discovery = core.search_semantic_entities(query, entity_class='Door')
        selected_id = _selected_id(discovery)
        context = core.get_architectural_evidence_context(selected_id)
        assert core.search_semantic_entities(query, entity_class='Door') == discovery
        assert core.get_architectural_evidence_context(selected_id) == context
    # Reopening and reversing unordered SQLite scans exposes incidental SQL order.
    with QueryCore(database) as core:
        core.connection.execute('PRAGMA reverse_unordered_selects=ON')
        assert core.search_semantic_entities(query, entity_class='Door') == discovery
        assert core.get_architectural_evidence_context(selected_id) == context
    assert database.read_bytes() == before


def test_cli_stdlib_discovery_then_explicit_context(architectural_database: Path) -> None:
    def cli(command: str, *arguments: str) -> dict:
        completed = subprocess.run(
            [sys.executable, '-S', '-m', 'tools.query_core', command,
             str(architectural_database), *arguments],
            cwd=Path(__file__).resolve().parents[1],
            check=True, capture_output=True, text=True, encoding='utf-8',
        )
        return json.loads(completed.stdout)

    discovery = cli('semantic-find', 'D-101', '--entity-class', 'Door')
    selected_id = _selected_id(discovery)
    context = cli('architectural-context', selected_id)
    assert context['entity'] == discovery['candidates'][0]['semantic_entity']
    _assert_coverage(context)
    with QueryCore(architectural_database) as core:
        assert discovery == core.search_semantic_entities('D-101', entity_class='Door')
        assert context == core.get_architectural_evidence_context(selected_id)
