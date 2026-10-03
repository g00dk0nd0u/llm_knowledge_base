"""Synthetic Phase 6A: source -> retrieval -> all stages -> human review packet."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import fitz
import pytest

from test_query_core_semantic import semantic_contract_records
from tools.query_core.build import build_database
from tools.query_core.query import QueryCore, SCHEMA_VERSION
from tools.vision import plan_review, execute_review, VisionContractError, run_vision
from tools.vision.providers.fake import FakeProvider
from tools.vision.runner import VisionExecutionError


@pytest.fixture
def proof(tmp_path, semantic_contract_records):
    records = semantic_contract_records
    pdf = tmp_path / 'drawings.pdf'
    with fitz.open() as doc:
        for title in ('Door D-101 Plan 2/A-301', 'Door D-101 Schedule EL160',
                      'Door D-101 Detail EL560', 'Section 2/A-301'):
            page = doc.new_page(width=600, height=500)
            page.insert_text((30, 45), title)
            page.draw_rect((20, 20, 250, 100))
        doc.save(pdf)
    doc_id = records['documents'][0]['id']
    records['documents'][0]['source_sha256'] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    records['evidence'][0]['pdf_page'] = 2
    for kind, page in [('plan', 1), ('detail', 3), ('section', 4)]:
        records['evidence'].append(dict(records['evidence'][0], id='ev-' + kind, pdf_page=page))
    records['semantic_entities'][0]['evidence_id'] = 'ev-plan'
    records['views'] = [dict(id='view-' + kind, document_id=doc_id, name=kind,
                             view_type=kind, source_model_id='model-host', source_unique_id=kind)
                        for kind in ('plan', 'schedule', 'detail', 'section')]
    records['sheets'] = [dict(id='sheet-' + kind, document_id=doc_id, name=kind,
                              number='A-' + str(i), pdf_page=i, export_order=i,
                              source_model_id='model-host', source_unique_id='sheet-' + kind)
                         for i, kind in enumerate(('plan', 'schedule', 'detail', 'section'), 1)]
    for kind, page in [('plan', 1), ('detail', 3), ('section', 4)]:
        row = next(e for e in records['evidence'] if e['id'] == 'ev-' + kind)
        row.update(view_id='view-' + kind, sheet_id='sheet-' + kind)
    records['entity_appearances'] = [dict(
        id='appearance-' + kind, entity_kind='element', entity_id='door-101',
        sheet_id='sheet-' + kind, view_id='view-' + kind, pdf_page=page,
        x_min=20.0, y_min=20.0, x_max=250.0, y_max=100.0,
        coordinate_space='pdf_points_top_left', bbox_quality='exact', appearance_kind='model',
        provenance='synthetic_explicit_occurrence') for kind, page in [('plan', 1), ('detail', 3)]]
    records['drawing_references'] = [dict(
        id='reference-' + kind, source_evidence_id='ev-plan', relation_type=relation,
        printed_reference=printed, target_view_id='view-' + kind,
        target_sheet_id='sheet-' + kind, target_evidence_id='ev-' + kind,
        resolution_state='exact', provenance='synthetic_explicit_printed_reference')
        for kind, relation, printed in [('detail', 'callout_to', '1/A-3'),
                                         ('section', 'section_cut_to', '2/A-4')]]
    records['drawing_references'].append(dict(
        id='reference-ambiguous', source_evidence_id='ev-plan', relation_type='references',
        printed_reference='?/A-?', resolution_state='ambiguous', provenance='synthetic_ambiguity'))
    database = build_database(records, tmp_path / 'artifacts/proof.sqlite')
    return dict(root=tmp_path, pdf=pdf, database=database, sources={doc_id: pdf})


class ProofProvider(FakeProvider):
    def inspect(self, request, images, **kwargs):
        status = {'V0': 'unresolved', 'V1': 'ambiguous', 'V2': 'conflict', 'V3': 'insufficient_evidence'}[request['stage']]
        assert len(images) == (2 if request['stage'] == 'V2' else 1)
        return dict(observation='Synthetic discrepancy EL160 / EL560; review originals.',
                    status=status, provider_run_id='proof-' + request['request_id'])


def test_door_and_plan_section_complete_review_flow(proof):
    before = {p: p.read_bytes() for p in (proof['pdf'], proof['database'])}
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='explicit-test-model', provider='fake',
                           stages=['V0', 'V1', 'V2', 'V3'], dpi=72)
        assert plan == plan_review(core, 'semantic-door-101', model='explicit-test-model', provider='fake',
                                   stages=['V0', 'V1', 'V2', 'V3'], dpi=72)
        packet = execute_review(core, plan, proof['sources'], root=proof['root'],
                                database=proof['database'], provider=ProofProvider())
    assert packet['status'] == 'ok'
    assert packet['failures'] == []
    assert {r['stage'] for r in packet['requests']} == {'V0', 'V1', 'V2', 'V3'}
    assert {o['status'] for o in packet['observations']} == {
        'unresolved', 'ambiguous', 'conflict', 'insufficient_evidence'}
    properties = packet['plan']['context']['properties']
    assert {p['source_value'] for p in properties if p['source_name'] == '電気錠'} == {'EL160', 'EL560'}
    relations = [j['relation'] for j in packet['plan']['jobs'] if j['stage'] == 'V2']
    assert {'callout_to', 'section_cut_to', 'semantic_entity_cooccurrence'} <= {r['relation_type'] for r in relations}
    assert any(s.get('reference_id') == 'reference-ambiguous' for s in plan['skipped'])
    assert any(r['resolution_state'] == 'ambiguous' for r in packet['plan']['context']['drawing_references'])
    for observation in packet['observations']:
        request = observation['request']
        assert request in packet['requests']
        for item in request.get('inputs', [request]):
            assert item['document']['source_sha256'] == hashlib.sha256(proof['pdf'].read_bytes()).hexdigest()
            assert item['pdf_page'] in (1, 2, 3, 4)
            assert item['output_sha256']
            if request['stage'] == 'V1':
                assert item['evidence_bbox'] != item['context_bbox']
            elif request['stage'] == 'V0':
                assert item['bbox'] is None
            else:
                assert item['bbox']
    assert all(e['source_refs'] for e in packet['executions'])
    assert packet['human_review_required']
    assert json.loads(json.dumps(packet, ensure_ascii=False)) == packet
    assert before == {p: p.read_bytes() for p in before}
    assert SCHEMA_VERSION == 2


def test_plan_is_stdlib_network_zero_and_write_free(proof):
    files = sorted(proof['root'].rglob('*'))
    command = [sys.executable, '-S', '-m', 'tools.vision', 'plan', '--database', str(proof['database']),
               '--entity', 'semantic-door-101', '--model', 'test', '--provider', 'openai']
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    plan = json.loads(result.stdout)
    assert plan['requires_live_opt_in']
    assert plan['network_calls_planned'] == len(plan['jobs'])
    assert files == sorted(proof['root'].rglob('*'))


def test_changed_plan_and_source_rejected_before_provider(proof):
    provider = ProofProvider()
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='test', provider='fake', dpi=72)
        changed = deepcopy(plan)
        changed['context']['properties'][0]['source_value'] = 'forged'
        with pytest.raises(VisionContractError, match='replan'):
            execute_review(core, changed, proof['sources'], root=proof['root'],
                           database=proof['database'], provider=provider)
        proof['pdf'].write_bytes(b'changed')
        with pytest.raises(VisionContractError, match='SHA'):
            execute_review(core, plan, proof['sources'], root=proof['root'],
                           database=proof['database'], provider=provider)


@pytest.mark.parametrize('error_type', [RuntimeError, VisionExecutionError])
def test_failures_stay_explicit_and_no_provider_fields_leak(proof, error_type):
    class Failing(FakeProvider):
        def inspect(self, *args, **kwargs):
            raise error_type('secret raw response')
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='test', provider='fake', stages=['V3'], dpi=72)
        packet = execute_review(core, plan, proof['sources'], root=proof['root'],
                                database=proof['database'], provider=Failing())
    assert packet['status'] == 'failed' and packet['observations'] == []
    assert {f['code'] for f in packet['failures']} == {'provider_failure'}
    assert 'secret raw response' not in json.dumps(packet)


def test_cli_fake_and_live_skip(proof, monkeypatch):
    from tools.vision.__main__ import main
    args = ['execute', '--database', str(proof['database']), '--entity', 'semantic-door-101',
            '--model', 'test', '--root', str(proof['root']), '--dpi', '72',
            '--source', next(iter(proof['sources'])) + '=' + str(proof['pdf'])]
    assert main(args + ['--provider', 'fake']) == 0
    assert list((proof['root'] / 'artifacts/vision-review').glob('*.json'))
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    assert main(args + ['--provider', 'openai']) == 1
    assert main(args + ['--provider', 'openai', '--live']) == 0


def test_all_stages_with_openai_mock_transport(proof, monkeypatch):
    import httpx
    from tools.vision.providers.openai import OpenAIProvider
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-key')
    calls = []
    def handler(request):
        body = json.loads(request.content)
        text = body['input'][0]['content'][0]['text']
        envelope = json.loads(text.split('\n', 1)[1])
        images = [c for c in body['input'][0]['content'] if c['type'] == 'input_image']
        assert len(images) == (2 if envelope['stage'] == 'V2' else 1)
        calls.append(envelope['stage'])
        return httpx.Response(200, json=dict(id='resp-' + str(len(calls)), status='completed',
            output=[dict(type='message', status='completed', content=[dict(
                type='output_text', text=json.dumps(dict(observation='Mock only', status='ambiguous')))])]))
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='explicit-mock', provider='openai',
                           stages=['V0', 'V1', 'V2', 'V3'], dpi=72)
        packet = execute_review(core, plan, proof['sources'], root=proof['root'],
                                database=proof['database'], live=True,
                                provider=OpenAIProvider(transport=httpx.MockTransport(handler)))
    assert packet['status'] == 'ok'
    assert set(calls) == {'V0', 'V1', 'V2', 'V3'}
    assert len(calls) == plan['network_calls_planned']
    assert 'dummy-key' not in json.dumps(packet)


def test_additive_stage_schemas_and_nested_ids(proof):
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    from tools.vision import validate_stage_observation
    schema_dir = Path('schema')
    registry = Registry()
    for name in ('vision_inspection_request_v1.schema.json', 'vision_stage_request_v1.schema.json',
                 'vision_stage_observation_v1.schema.json'):
        registry = registry.with_resource(name, Resource.from_contents(
            json.loads((schema_dir / name).read_text()), default_specification=DRAFT202012))
    validator = jsonschema.Draft202012Validator(
        json.loads((schema_dir / 'vision_stage_observation_v1.schema.json').read_text()), registry=registry)
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='test', provider='fake', stages=['V0', 'V2'], dpi=72)
        packet = execute_review(core, plan, proof['sources'], root=proof['root'], database=proof['database'], provider=ProofProvider())
    assert packet['observations']
    for observation in packet['observations']:
        validator.validate(observation)
        assert validate_stage_observation(observation) == observation
        bad = deepcopy(observation)
        bad['request']['inputs'][0]['pdf_page'] += 1
        with pytest.raises(VisionContractError):
            validate_stage_observation(bad)


def test_resolved_sheet_only_reference_renders_page_without_guessing(proof):
    import sqlite3
    with sqlite3.connect(proof['database']) as connection:
        connection.execute("UPDATE drawing_references SET target_evidence_id=NULL WHERE id='reference-section'")
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='test', provider='fake', stages=['V2'], dpi=72)
        job = next(j for j in plan['jobs'] if j['relation']['id'] == 'reference-section')
        assert job['candidates'][1]['input_scope'] == 'page'
        assert job['candidates'][1]['pdf_page'] == 4
        packet = execute_review(core, plan, proof['sources'], root=proof['root'], database=proof['database'], provider=ProofProvider())
    assert packet['status'] == 'ok'
    assert any(e.get('evidence_class') == 'deterministic_derived' for e in plan['context']['evidence'])
    assert any(r['kind'] == 'drawing_reference' for r in job['candidates'][1]['source_refs'])
