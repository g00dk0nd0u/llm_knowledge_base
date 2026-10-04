"""Source-backed planning policy, closed fallback and exact row member trace."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import fitz
import pytest

from test_query_core_semantic import semantic_contract_records
from test_query_core_semantic_table_adapter import _records
from test_vision_review_loop import proof, ProofProvider
from test_vision_hardening import large_case
from tools.query_core.build import build_database
from tools.query_core.query import QueryCore
from tools.query_core.semantic_table_adapter import SemanticTableMapping, apply_semantic_table_mapping
from tools.vision import plan_review, execute_review, VisionContractError
from tools.vision.review import _v3_surfaces


def test_focused_suppresses_without_enumerating_generic_pairs(large_case, monkeypatch):
    import tools.vision.review as review
    monkeypatch.setattr(review, 'combinations', lambda *a: pytest.fail('suppressed pairs enumerated'))
    with QueryCore(large_case['database']) as core:
        plan = plan_review(core, 'selected-region', model='test', stages=['V2'], provider='openai')
        assert plan == plan_review(core, 'selected-region', model='test', stages=['V2'], provider='openai')
    assert plan['policy'] == 'focused'
    assert len(plan['jobs']) == plan['provider_calls_planned'] == plan['network_calls_planned'] == 1
    summary = plan['v2_cooccurrence']
    assert summary['eligible_count'] == summary['suppressed_count'] == 120 * 119 // 2 - 1
    assert summary['scheduled_count'] == summary['omitted_count'] == 0
    assert summary['reason'] == 'explicit_relation_first'
    assert plan['jobs'][0]['selection_reason'] == 'resolved_explicit_relation'
    assert any(s.get('reason') == 'explicit_pair_already_scheduled' for s in plan['skipped'])


@pytest.mark.parametrize('state', [None, 'ambiguous', 'unresolved'])
def test_no_resolved_reference_preserves_bounded_fallback(large_case, state):
    with sqlite3.connect(large_case['database']) as connection:
        if state is None:
            connection.execute('DELETE FROM drawing_references')
        else:
            connection.execute('UPDATE drawing_references SET resolution_state=?,target_evidence_id=NULL', (state,))
    with QueryCore(large_case['database']) as core:
        focused = plan_review(core, 'selected-region', model='test', stages=['V2'])
        exhaustive = plan_review(core, 'selected-region', model='test', stages=['V2'], policy='exhaustive')
    assert focused['jobs'] == exhaustive['jobs']
    assert len(focused['jobs']) == 32
    assert all(j['relation']['relation_type'] == 'semantic_entity_cooccurrence' for j in focused['jobs'])
    summary = focused['v2_cooccurrence']
    assert summary['eligible_count'] == 120 * 119 // 2
    assert summary['omitted_count'] == 120 * 119 // 2 - 32
    assert summary['suppressed_count'] == 0
    if state:
        assert len([s for s in focused['skipped'] if s.get('reference_id')]) == 2


def test_multiple_explicit_relations_policy_ids_and_execute(proof):
    with QueryCore(proof['database']) as core:
        focused = plan_review(core, 'semantic-door-101', model='test', provider='fake', stages=['V2'], dpi=72)
        exhaustive = plan_review(core, 'semantic-door-101', model='test', provider='fake', stages=['V2'], dpi=72, policy='exhaustive')
        assert focused['plan_id'] != exhaustive['plan_id']
        assert focused['jobs'] == [j for j in exhaustive['jobs'] if j['relation']['relation_type'] != 'semantic_entity_cooccurrence']
        assert len(focused['jobs']) == 2
        packet = execute_review(core, exhaustive, proof['sources'], root=proof['root'], database=proof['database'], provider=ProofProvider())
        assert packet['status'] == 'ok'
        with pytest.raises(VisionContractError, match='policy'):
            plan_review(core, 'semantic-door-101', model='test', policy='hidden')
    pairs = [tuple(sorted(c['candidate_id'] for c in j['candidates'])) for j in focused['jobs']]
    assert len(pairs) == len(set(pairs))
    assert any(s.get('reference_id') == 'reference-ambiguous' for s in focused['skipped'])


@pytest.fixture
def row_case(tmp_path):
    r = _records(['Number', 'Width', 'Height'], [['D-1', '1000', '2100']])
    pdf = tmp_path / 'source.pdf'
    with fitz.open() as doc:
        doc.new_page(width=100, height=100)
        doc.save(pdf)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    r['documents'][0]['source_sha256'] = r['source_document_sha256'] = sha
    mapping = SemanticTableMapping(mapping_id='test', source_namespace='test', table_id='table-1',
        header_rows=(0,), entity_class='Door', key_columns=('Number',), instance_or_type='type',
        property_scope='schedule', property_columns=('Width', 'Height'))
    apply_semantic_table_mapping(r, mapping)
    entity = r['semantic_entities'][0]['id']
    r['semantic_entities'][0]['evidence_id'] = 'evidence-0'
    r['drawing_references'] = [dict(id='row-reference', source_evidence_id='evidence-0',
        target_evidence_id='evidence-2', relation_type='references', resolution_state='exact', provenance='synthetic_explicit')]
    db = build_database(r, tmp_path / 'artifacts/row.sqlite')
    return dict(entity=entity, database=db, root=tmp_path, pdf=pdf, sources={r['documents'][0]['id']: pdf})


def test_row_union_geometry_members_trace_id_and_fake_execution(row_case):
    case = row_case
    before = {p: p.read_bytes() for p in (case['pdf'], case['database'])}
    with QueryCore(case['database']) as core:
        focused = plan_review(core, case['entity'], model='test', stages=['V2','V3'], provider='fake', dpi=72)
        exhaustive = plan_review(core, case['entity'], model='test', stages=['V2','V3'], provider='fake', dpi=72, policy='exhaustive')
        assert focused['context'] == exhaustive['context']
        assert focused['candidates'] == exhaustive['candidates']
        aggregate = next(j['candidates'][0] for j in focused['jobs'] if j['stage']=='V3' and j['candidates'][0].get('members'))
        assert aggregate['bbox'] == [0.0,1.0,3.0,2.0]
        assert aggregate['provenance'] == 'exact_table_row_union_v1'
        assert aggregate['navigation'] is None  # Navigation belongs to exact members.
        assert len(aggregate['members']) == 3
        assert all(m in exhaustive['candidates'] for m in aggregate['members'])
        assert {json.dumps(r, sort_keys=True) for r in aggregate['source_refs']} == {
            json.dumps(r, sort_keys=True) for m in aggregate['members'] for r in m['source_refs']}
        assert all(m['navigation']['bbox'] == m['bbox'] for m in aggregate['members'])
        v3 = [j for j in focused['jobs'] if j['stage']=='V3']
        assert len(v3) == 3
        assert all(j['selection_reason']=='explicit_reference_endpoint' for j in v3[:2])
        summary = focused['v3_selection']
        assert summary == dict(eligible_count=5, scheduled_count=3, aggregated_member_count=3, reason='exact_table_row_union_v1')
        replacements = [s for s in focused['skipped'] if s.get('replacement_candidate_id')]
        assert {s['candidate_id'] for s in replacements} == {m['candidate_id'] for m in aggregate['members']}
        assert {s['replacement_candidate_id'] for s in replacements} == {aggregate['candidate_id']}
        reordered, _ = _v3_surfaces(core, list(reversed(focused['candidates'])), set())
        assert next(c for c in reordered if c.get('members')) == aggregate
        changed = deepcopy(focused['candidates'])
        member_id = aggregate['members'][0]['candidate_id']
        changed_member = next(c for c in changed if c['candidate_id'] == member_id)
        changed_member['source_refs'].append(dict(kind='extra_trace', id='new-provenance'))
        changed_surfaces, _ = _v3_surfaces(core, changed, set())
        assert next(c for c in changed_surfaces if c.get('members'))['candidate_id'] != aggregate['candidate_id']
        packet = execute_review(core, focused, case['sources'], root=case['root'], database=case['database'], provider=ProofProvider())
    assert packet['status'] == 'ok' and packet['human_review_required']
    assert len(packet['requests']) == len(focused['jobs']) == focused['provider_calls_planned']
    assert focused['network_calls_planned'] == 0
    assert any(r['candidate_id']==aggregate['candidate_id'] and r['bbox']==aggregate['bbox'] for r in packet['requests'] if r['stage']=='V3')
    assert before == {p:p.read_bytes() for p in before}


def test_mixed_row_and_protected_cell_never_join(row_case):
    with QueryCore(row_case['database']) as core:
        p = plan_review(core, row_case['entity'], model='test', policy='exhaustive')
        cells = [c for c in p['candidates'] if any(r.get('source_kind')=='pdf_table_cell' for r in c['source_refs'])]
        protected = cells[0]['candidate_id']
        surfaces, replaced = _v3_surfaces(core, cells, {protected})
        assert next(c for c in surfaces if c['candidate_id']==protected) == cells[0]
        assert len(replaced)==2
        # Wrong geometry or page cannot borrow stored row membership.
        changed = deepcopy(cells)
        changed[0]['pdf_page'] += 1
        changed[1]['bbox'][0] += 0.1
        surfaces, replaced = _v3_surfaces(core, changed, set())
        assert replaced == [] and not any(c.get('members') for c in surfaces)


def test_cli_policy_is_network_free_and_counts_real_jobs(row_case, capsys):
    from tools.vision.__main__ import main
    args = ['plan','--database',str(row_case['database']),'--entity',row_case['entity'],
            '--model','test','--provider','openai','--stages','V2','V3']
    assert main(args)==0
    focused=json.loads(capsys.readouterr().out)
    assert main(args+['--policy','exhaustive'])==0
    exhaustive=json.loads(capsys.readouterr().out)
    assert focused['policy']=='focused' and exhaustive['policy']=='exhaustive'
    assert focused['network_calls_planned']==len(focused['jobs']) < exhaustive['network_calls_planned']


def test_same_page_other_row_retains_own_surface(row_case):
    with QueryCore(row_case['database']) as core:
        p = plan_review(core, row_case['entity'], model='test', policy='exhaustive')
        # One known cell in header row 0 and the three data cells in row 1.
        header = deepcopy(next(c for c in p['candidates'] if c['bbox'] == [0.0,0.0,1.0,1.0]))
        header['source_refs'].append(dict(kind='drawing_occurrence', id='header-cell',
                                         source_kind='pdf_table_cell', source_id='cell-0-0'))
        data = [c for c in p['candidates'] if any(r.get('source_kind')=='pdf_table_cell' for r in c['source_refs'])]
        surfaces, replacements = _v3_surfaces(core, [header, *data], set())
        assert header in surfaces and len(replacements) == 3
        aggregate = next(c for c in surfaces if c.get('members'))
        assert aggregate['aggregation_key']['row_index'] == 1
        assert header not in aggregate['members']


def test_aggregate_live_budget_exactly_matches_scheduled_jobs(row_case, monkeypatch):
    import httpx
    from test_vision_runner_openai import response_body
    from tools.vision.providers.openai import OpenAIProvider
    from tools.vision.runner import VisionExecutionError
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_body())
    with QueryCore(row_case['database']) as core:
        p = plan_review(core, row_case['entity'], model='test', provider='openai', stages=['V2','V3'], dpi=72)
        monkeypatch.setenv('OPENAI_API_KEY', 'dummy-key')
        provider = OpenAIProvider(transport=httpx.MockTransport(handler))
        with pytest.raises(VisionExecutionError, match='live_call_budget_exceeded'):
            execute_review(core, p, {}, root=row_case['root'], database=row_case['database'],
                           provider=provider, live=True, max_live_calls=len(p['jobs'])-1)
        assert not calls
        packet = execute_review(core, p, row_case['sources'], root=row_case['root'], database=row_case['database'],
                                provider=provider, live=True, max_live_calls=len(p['jobs']))
        assert packet['status'] == 'ok'
        assert len(calls) == p['network_calls_planned'] == p['provider_calls_planned'] == len(p['jobs'])


def test_resolved_reference_without_distinct_surfaces_stays_closed(large_case):
    with sqlite3.connect(large_case['database']) as connection:
        connection.execute('UPDATE drawing_references SET target_evidence_id=source_evidence_id')
    with QueryCore(large_case['database']) as core:
        p = plan_review(core, 'selected-region', model='test', stages=['V2'])
    assert p['jobs'] == [] and p['status'] == 'insufficient_evidence'
    assert p['v2_cooccurrence']['suppressed_count'] == 120 * 119 // 2
    assert len([s for s in p['skipped'] if s.get('reference_id')]) == 2
