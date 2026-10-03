"""PR73 operational safety: bounded planning, paid budgets and provider minimization."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import httpx
import jsonschema
import pytest
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from test_query_core_semantic import semantic_contract_records
from test_vision_review_loop import proof, ProofProvider
from test_pdf_pipeline_vision_render import make_case, render, output_file
from test_pdf_pipeline_vision_context_render import make_context_case, render as context_render
from test_vision_runner_openai import response_body
from tools.query_core.query import QueryCore
from tools.vision import (plan_review, execute_review, run_vision, VisionContractError,
                          build_vision_inspection_request, build_vision_context_inspection_request,
                          build_vision_observation, build_vision_context_observation,
                          validate_stage_observation)
from tools.vision.contract import _id
from tools.vision.review import MAX_V2_COOCCURRENCE_JOBS
from tools.vision.runner import VisionExecutionError
from tools.vision.providers.fake import FakeProvider
from tools.vision.providers.openai import OpenAIProvider
from tools.vision.provider_input import STAGE_GUIDANCE, COMMON_GUIDANCE, prepare_provider_input
from tools.vision.stages import validate_request


@pytest.fixture
def large_case(tmp_path):
    case = make_case(tmp_path)
    with sqlite3.connect(case['database']) as connection:
        for i in range(120):
            x, y = i % 100 * 2, 10 + (i // 100 * 30)
            connection.execute('INSERT INTO evidence '
                '(id,document_id,pdf_page,x_min,y_min,x_max,y_max,coordinate_space) VALUES (?,?,?,?,?,?,?,?)',
                (f'large-ev-{i:03}', 'document', 1, x, y, x + 1, y + 10, 'pdf_points_top_left'))
            connection.execute('INSERT INTO semantic_bindings '
                '(id,semantic_entity_id,source_kind,source_id,resolution_state,provenance,evidence_id) '
                'VALUES (?,?,?,?,?,?,?)',
                (f'large-binding-{i:03}', 'selected-region', 'evidence', f'large-ev-{i:03}',
                 'exact', 'synthetic_explicit_binding', f'large-ev-{i:03}'))
        for index, reverse in enumerate((False, True)):
            source, target = ('large-ev-001', 'large-ev-000') if reverse else ('large-ev-000', 'large-ev-001')
            connection.execute('INSERT INTO drawing_references '
                '(id,source_evidence_id,target_evidence_id,relation_type,resolution_state,provenance) '
                'VALUES (?,?,?,?,?,?)',
                (f'large-reference-{index}', source, target, 'references', 'exact', 'synthetic_explicit_reference'))
    return case


def test_120_direct_candidates_are_bounded_lazy_and_explicit_pairs_win(large_case, monkeypatch):
    import tools.vision.review as review
    original = review.combinations
    consumed = []
    def bounded_iterator(values, count):
        for pair in original(values, count):
            consumed.append(1)
            assert len(consumed) <= MAX_V2_COOCCURRENCE_JOBS + 3
            yield pair
    monkeypatch.setattr(review, 'combinations', bounded_iterator)
    with QueryCore(large_case['database']) as core:
        plan = plan_review(core, 'selected-region', model='test', provider='fake', stages=['V2'])
    direct = [c for c in plan['candidates'] if any(r['kind'] == 'drawing_occurrence' for r in c['source_refs'])]
    assert len(direct) == 120
    expected = 120 * 119 // 2 - 1
    summary = plan['v2_cooccurrence']
    assert summary == dict(limit=MAX_V2_COOCCURRENCE_JOBS, eligible_count=expected,
                           scheduled_count=MAX_V2_COOCCURRENCE_JOBS,
                           omitted_count=expected - MAX_V2_COOCCURRENCE_JOBS,
                           reason='deterministic_cooccurrence_limit')
    assert len(plan['jobs']) == MAX_V2_COOCCURRENCE_JOBS + 1
    assert plan['jobs'][0]['relation']['source_refs'] == [{'kind': 'drawing_reference', 'id': 'large-reference-0'}]
    assert any(s.get('reason') == 'explicit_pair_already_scheduled' for s in plan['skipped'])
    assert any(s.get('omitted_count') == expected - MAX_V2_COOCCURRENCE_JOBS for s in plan['skipped'])
    pairs = [tuple(sorted(c['candidate_id'] for c in j['candidates'])) for j in plan['jobs']]
    assert len(pairs) == len(set(pairs))
    assert plan['network_calls_planned'] == 0


@pytest.mark.parametrize('budget,code', [(None, 'live_call_budget_required'),
    (True, 'live_call_budget_required'), (-1, 'live_call_budget_required'),
    (0, 'live_call_budget_exceeded'), (1, 'live_call_budget_exceeded')])
def test_live_budget_rejected_before_render_source_read_or_network(proof, monkeypatch, budget, code):
    import tools.pdf_pipeline.vision_render as rendering
    def forbidden(*args, **kwargs):
        pytest.fail('budget must reject before render/network')
    monkeypatch.setattr(rendering, '_render_selected_candidate', forbidden)
    provider = OpenAIProvider(transport=httpx.MockTransport(forbidden))
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='test', provider='openai', stages=['V3'])
        assert len(plan['jobs']) > 1
        with pytest.raises(VisionExecutionError) as error:
            execute_review(core, plan, {}, root=proof['root'], database=proof['database'],
                           provider=provider, live=True, max_live_calls=budget)
    assert error.value.code == code


@pytest.mark.parametrize('mode,stages,entity', [
    ('no_candidates', ['V3'], 'selected-region'),
    ('page_only', ['V1'], 'selected-page'),
    ('all_skipped', ['V2'], 'selected-page'),
])
@pytest.mark.parametrize('provider_name', ['none', 'fake', 'openai'])
def test_zero_jobs_are_insufficient_and_cli_fails(tmp_path, monkeypatch, capsys, mode, stages, entity, provider_name):
    from tools.vision.__main__ import main
    import tools.pdf_pipeline.vision_render as rendering
    case = make_case(tmp_path)
    if mode == 'no_candidates':
        with sqlite3.connect(case['database']) as connection:
            connection.execute("UPDATE semantic_entities SET evidence_id=NULL WHERE id='selected-region'")
    def forbidden(*args, **kwargs):
        pytest.fail('zero jobs must not start rendering/providers')
    monkeypatch.setattr(rendering, '_render_selected_candidate', forbidden)
    provider = None if provider_name == 'none' else (FakeProvider() if provider_name == 'fake' else OpenAIProvider())
    before = sorted(tmp_path.rglob('*'))
    with QueryCore(case['database']) as core:
        plan = plan_review(core, entity, model='test', provider=provider_name, stages=stages)
        assert plan['status'] == 'insufficient_evidence' and plan['jobs'] == []
        packet = execute_review(core, plan, {}, root=tmp_path, database=case['database'], provider=provider)
    assert packet['status'] == 'insufficient_evidence'
    assert packet['requests'] == packet['observations'] == []
    args = ['--database', str(case['database']), '--entity', entity, '--model', 'test',
            '--provider', provider_name, '--root', str(tmp_path), '--stages', *stages]
    assert main(['plan', *args]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'insufficient_evidence'
    assert main(['execute', *args]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'insufficient_evidence'
    assert before == sorted(tmp_path.rglob('*'))


def test_cli_live_requires_budget_even_when_key_missing(proof, monkeypatch, capsys):
    from tools.vision.__main__ import main
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    args = ['execute', '--database', str(proof['database']), '--entity', 'semantic-door-101',
            '--model', 'test', '--provider', 'openai', '--live']
    assert main(args) == 1
    assert json.loads(capsys.readouterr().err)['code'] == 'live_call_budget_required'
    assert main(args + ['--max-live-calls', '0']) == 1
    assert json.loads(capsys.readouterr().err)['code'] == 'live_call_budget_exceeded'


def test_provider_payload_all_stages_is_minimized_and_guidance_trusted(proof, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-secret-key')
    instruction = '  Original caller instruction\n原文\n'
    seen = []
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='test', provider='openai',
                           stages=['V0', 'V1', 'V2', 'V3'], instruction=instruction, dpi=72)
        forbidden_values = [plan['semantic_entity_id'], str(proof['root']), str(proof['database']), str(proof['pdf'])]
        for c in plan['candidates']:
            forbidden_values += [c['candidate_id'], c['document']['id'], c['document']['identity'], c['document']['source_sha256']]
        def handler(request):
            body = json.loads(request.content)
            serialized = request.content.decode()
            stage = body['input'][0]['content'][0]['text'][:2]
            assert body['input'][0]['role'] == 'developer'
            assert body['input'][0]['content'][0]['text'] == STAGE_GUIDANCE[stage] + ' ' + COMMON_GUIDANCE
            content = body['input'][1]['content']
            assert content[0]['text'] == instruction
            metadata = json.loads(content[1]['text'])
            assert set(metadata) == ({'focus_box_normalized'} if stage == 'V1' else
                                     {'relation_type'} if stage == 'V2' else set())
            for key in ('source_sha256', 'semantic_entity_id', 'candidate_id', 'request_id',
                        'document', 'navigation', 'source_refs', 'source_filename', 'pdf_page', 'output_sha256'):
                assert key not in serialized
            for value in forbidden_values:
                assert value not in serialized
            images = [c for c in content if c['type'] == 'input_image']
            assert len(images) == (2 if stage == 'V2' else 1)
            seen.append(stage)
            return httpx.Response(200, json=response_body())
        packet = execute_review(core, plan, proof['sources'], root=proof['root'], database=proof['database'],
            provider=OpenAIProvider(transport=httpx.MockTransport(handler)), live=True,
            max_live_calls=plan['network_calls_planned'])
    assert packet['status'] == 'ok'
    assert set(seen) == {'V0', 'V1', 'V2', 'V3'}
    assert packet['plan']['context']['drawing_references']
    assert all(o['request'] in packet['requests'] for o in packet['observations'])


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_v1_normalized_focus_tracks_displayed_image_rotation(tmp_path, rotation):
    case = make_context_case(tmp_path, rotation)
    result = context_render(case, dpi=72)
    request = build_vision_context_inspection_request(result, output_file(case, result), 'Focus')
    e, c = request['evidence_bbox'], request['context_bbox']
    x1, y1, x2, y2 = (e[0]-c[0])/(c[2]-c[0]), (e[1]-c[1])/(c[3]-c[1]), (e[2]-c[0])/(c[2]-c[0]), (e[3]-c[1])/(c[3]-c[1])
    expected = {0: [x1,y1,x2,y2], 90: [1-y2,x1,1-y1,x2],
                180: [1-x2,1-y2,1-x1,1-y1], 270: [y1,1-x2,y2,1-x1]}[rotation]
    assert prepare_provider_input(request)['metadata']['focus_box_normalized'] == expected


def _registry():
    registry = Registry()
    for path in Path('schema').glob('vision*.schema.json'):
        registry = registry.with_resource(path.name, Resource.from_contents(json.loads(path.read_text()), default_specification=DRAFT202012))
    return registry


def test_v0_v2_runtime_and_schema_reject_arbitrary_nested_fields(proof):
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='test', stages=['V0', 'V2'], dpi=72)
        packet = execute_review(core, plan, proof['sources'], root=proof['root'], database=proof['database'])
    validator = jsonschema.Draft202012Validator(json.loads(Path('schema/vision_stage_request_v1.schema.json').read_text()), registry=_registry())
    for request in packet['requests']:
        validator.validate(request)
        for location in ('envelope', 'relation', 'ref'):
            if location != 'envelope' and request['stage'] != 'V2':
                continue
            bad = deepcopy(request)
            target = bad if location == 'envelope' else bad['relation'] if location == 'relation' else bad['relation']['source_refs'][0]
            target['provider_raw'] = {'nested': {'secret': 'raw-response'}}
            bad['request_id'] = _id('vision-stage-request-', {k:v for k,v in bad.items() if k != 'request_id'})
            with pytest.raises(VisionContractError):
                validate_request(bad)
            with pytest.raises(jsonschema.ValidationError):
                validator.validate(bad)


@pytest.mark.parametrize('timestamp,expected', [(0, '1970-01-01T00:00:00Z'),
    (1_700_000_000, '2023-11-14T22:13:20Z'), (1_700_000_000.125, '2023-11-14T22:13:20.125000Z')])
def test_provider_timestamp_all_stages_preserved(proof, monkeypatch, timestamp, expected):
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-key')
    body = response_body(created_at=timestamp)
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='test', provider='openai', stages=['V0','V1','V2','V3'], dpi=72)
        packet = execute_review(core, plan, proof['sources'], root=proof['root'], database=proof['database'],
            live=True, max_live_calls=plan['network_calls_planned'],
            provider=OpenAIProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))))
    assert packet['status'] == 'ok'
    for observation in packet['observations']:
        assert observation['created_at'] == expected
        if observation['request']['stage'] in ('V0','V2'):
            validate_stage_observation(observation)
            jsonschema.Draft202012Validator(json.loads(Path('schema/vision_stage_observation_v1.schema.json').read_text()), registry=_registry()).validate(observation)


@pytest.mark.parametrize('timestamp', [None, True, False, '1700000000', 'secret raw timestamp',
    -1, float('nan'), float('inf'), 253402300800, 10**100])
def test_malformed_provider_timestamps_are_safe_failure(tmp_path, monkeypatch, timestamp):
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-key')
    case = make_case(tmp_path)
    result = render(case, dpi=72)
    path = output_file(case, result)
    request = build_vision_inspection_request(result, path, 'Inspect')
    body = response_body(created_at=timestamp)
    adapter = OpenAIProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=json.dumps(body))))
    with pytest.raises(VisionExecutionError) as error:
        run_vision(request, [path], adapter, model='test', live=True, max_live_calls=1)
    assert error.value.code == 'malformed_response'
    assert 'secret' not in str(error.value) and 'dummy-key' not in str(error.value)


@pytest.mark.parametrize('stage', ['V1','V3'])
def test_missing_timestamp_keeps_prior_v1_v3_output_and_id(tmp_path, monkeypatch, stage):
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-key')
    if stage == 'V1':
        case = make_context_case(tmp_path)
        result = context_render(case, dpi=72)
        path = output_file(case, result)
        request = build_vision_context_inspection_request(result, path, 'Inspect')
        builder = build_vision_context_observation
    else:
        case = make_case(tmp_path)
        result = render(case, dpi=72)
        path = output_file(case, result)
        request = build_vision_inspection_request(result, path, 'Inspect')
        builder = build_vision_observation
    expected = builder(request, 'openai', 'test', 'Visible conflict', 'conflict', provider_run_id='resp_mock')
    adapter = OpenAIProvider(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response_body())))
    actual = run_vision(request, [path], adapter, model='test', live=True, max_live_calls=1)
    assert actual == expected
    assert actual['created_at'] is None


def test_timestamp_conversion_is_independent_of_decimal_context():
    from decimal import localcontext
    from tools.vision.provenance import unix_created_at
    with localcontext() as context:
        context.prec = 4
        assert unix_created_at(1_700_000_000.125) == '2023-11-14T22:13:20.125000Z'


def test_single_runner_call_also_requires_explicit_budget(tmp_path):
    case = make_case(tmp_path)
    result = render(case, dpi=72)
    path = output_file(case, result)
    request = build_vision_inspection_request(result, path, 'Inspect')
    adapter = OpenAIProvider(transport=httpx.MockTransport(lambda r: pytest.fail('network forbidden')))
    with pytest.raises(VisionExecutionError, match='live_call_budget_required'):
        run_vision(request, [path], adapter, model='test', live=True)
    with pytest.raises(VisionExecutionError, match='live_call_budget_exceeded'):
        run_vision(request, [path], adapter, model='test', live=True, max_live_calls=0)


def test_malformed_response_never_leaks_to_packet_or_cli(proof, monkeypatch, capsys):
    from tools.vision.__main__ import main
    monkeypatch.setenv('OPENAI_API_KEY', 'dummy-key-secret')
    adapter = OpenAIProvider(transport=httpx.MockTransport(lambda r:
        httpx.Response(200, text='raw-secret-response-source-document-content-base64')))
    with QueryCore(proof['database']) as core:
        plan = plan_review(core, 'semantic-door-101', model='test', provider='openai', stages=['V3'], dpi=72)
        packet = execute_review(core, plan, proof['sources'], root=proof['root'], database=proof['database'],
                               provider=adapter, live=True, max_live_calls=plan['network_calls_planned'])
    assert packet['status'] == 'failed' and packet['observations'] == []
    assert {f['code'] for f in packet['failures']} == {'malformed_response'}
    for secret in ('dummy-key-secret', 'raw-secret-response-source-document-content-base64'):
        assert secret not in json.dumps(packet)
    monkeypatch.setattr('tools.vision.providers.openai.OpenAIProvider', lambda: adapter)
    args = ['execute', '--database', str(proof['database']), '--entity', 'semantic-door-101',
            '--model', 'test', '--provider', 'openai', '--live', '--max-live-calls', '100',
            '--root', str(proof['root']), '--dpi', '72', '--stages', 'V3',
            '--source', next(iter(proof['sources'])) + '=' + str(proof['pdf'])]
    assert main(args) == 1
    summary = capsys.readouterr().out
    assert json.loads(summary)['status'] == 'failed'
    assert 'dummy-key-secret' not in summary and 'raw-secret' not in summary
