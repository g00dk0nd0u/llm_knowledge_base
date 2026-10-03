"""Common runner and OpenAI shape/errors with a mock transport, never live."""
import json
from copy import deepcopy
import httpx
import pytest
from test_pdf_pipeline_vision_render import make_case, render, output_file
from tools.vision import (build_vision_inspection_request, run_vision, VisionContractError)
from tools.vision.runner import VisionExecutionError
from tools.vision.providers.openai import OpenAIProvider, ENDPOINT
from tools.vision.providers.fake import FakeProvider
from tools.vision.stages import build_stage_request, validate_request


@pytest.fixture
def request_case(tmp_path):
    case = make_case(tmp_path)
    result = render(case, dpi=72)
    path = output_file(case, result)
    return build_vision_inspection_request(result, path, 'Inspect'), path


def response_body(**updates):
    return dict(id='resp_mock', status='completed', usage={'total_tokens': 123},
                output=[{'type': 'message', 'status': 'completed', 'content': [
                    {'type': 'output_text', 'text': json.dumps({'observation': 'Visible conflict', 'status': 'conflict'})}]}],
                **updates)


def provider(handler):
    return OpenAIProvider(transport=httpx.MockTransport(handler))


def test_openai_response_shape_and_neutral_output(request_case, monkeypatch):
    request, path = request_case
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret')
    seen = []
    def handler(req):
        seen.append(req)
        assert str(req.url) == ENDPOINT
        body = json.loads(req.content)
        assert body['model'] == 'explicit-model'
        assert body['store'] is False
        assert 'assistants' not in str(req.url)
        assert req.headers['authorization'] == 'Bearer test-secret'
        content = body['input'][0]['content']
        assert content[1]['type'] == 'input_image'
        assert content[1]['image_url'].startswith('data:image/png;base64,')
        assert body['text']['format']['strict'] is True
        assert body['text']['format']['schema']['additionalProperties'] is False
        return httpx.Response(200, json=response_body())
    observation = run_vision(request, [path], provider(handler), model='explicit-model', live=True)
    assert len(seen) == 1
    assert observation['status'] == 'conflict'
    assert observation['request'] == request
    assert observation['provider_run_id'] == 'resp_mock'
    assert 'usage' not in observation and 'confidence' not in observation
    assert 'test-secret' not in json.dumps(observation)


@pytest.mark.parametrize('code,body,status', [
    ('auth_failed', {}, 401), ('auth_failed', {}, 403), ('http_error', {}, 429),
    ('http_error', {}, 500), ('malformed_response', [], 200),
    ('partial_response', {'id': 'r', 'status': 'incomplete'}, 200),
    ('refusal', {'id': 'r', 'status': 'completed', 'output': [
        {'type': 'message', 'status': 'completed', 'content': [{'type': 'refusal', 'refusal': 'secret'}]}]}, 200),
    ('malformed_response', {'id': 'r', 'status': 'completed', 'output': []}, 200),
    ('partial_response', {'id': 'r', 'status': 'completed', 'output': [
        {'type': 'message', 'status': 'in_progress', 'content': []}]}, 200),
])
def test_explicit_failures(request_case, monkeypatch, code, body, status):
    request, path = request_case
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret')
    with pytest.raises(VisionExecutionError) as error:
        run_vision(request, [path], provider(lambda req: httpx.Response(status, json=body)),
                   model='test', live=True)
    assert error.value.code == code
    assert 'test-secret' not in str(error.value)


def test_timeout_and_malformed_json(request_case, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret')
    request, path = request_case
    def handler(req):
        raise httpx.ReadTimeout('raw secret')
    with pytest.raises(VisionExecutionError, match='timeout'):
        run_vision(request, [path], provider(handler), model='test', live=True)
    with pytest.raises(VisionExecutionError, match='malformed_response'):
        run_vision(request, [path], provider(lambda req: httpx.Response(200, text='secret invalid')),
                   model='test', live=True)


def test_image_swap_request_mutation_and_consent_are_pre_network(request_case, monkeypatch):
    request, path = request_case
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret')
    def forbidden(req):
        pytest.fail('network must not run')
    adapter = provider(forbidden)
    with pytest.raises(VisionExecutionError, match='live_opt_in_required'):
        run_vision(request, [path], adapter, model='test')
    changed = deepcopy(request)
    changed['instruction'] = 'swapped'
    with pytest.raises(VisionContractError):
        run_vision(changed, [path], adapter, model='test', live=True)
    path.write_bytes(path.read_bytes() + b'swapped')
    with pytest.raises(VisionContractError):
        run_vision(request, [path], adapter, model='test', live=True)


def test_extra_provider_fields_never_become_observation(request_case):
    class Extra(FakeProvider):
        def inspect(self, *args, **kwargs):
            return dict(observation='x', status='supported', provider_run_id='r', confidence=0.99)
    request, path = request_case
    with pytest.raises(VisionExecutionError, match='malformed_response'):
        run_vision(request, [path], Extra(), model='test')


def test_v0_v2_stage_separation(request_case):
    request, path = request_case
    with pytest.raises(VisionContractError):
        build_stage_request('V0', [request], 'triage')
    with pytest.raises(VisionContractError):
        build_stage_request('V2', [request, request], 'compare', relation=None)
    bad = deepcopy(request)
    bad['stage'] = 'V2'
    with pytest.raises(VisionContractError):
        validate_request(bad)


def test_invalid_compressed_png_rejected_before_transport(request_case):
    import hashlib
    import struct
    import zlib
    from tools.vision.contract import _id
    request, path = request_case
    data = path.read_bytes()
    position = 8
    chunks = [data[:8]]
    while position < len(data):
        size, kind = struct.unpack('>I4s', data[position:position + 8])
        payload = data[position + 8:position + 8 + size]
        if kind == b'IDAT':
            payload = b'not valid compressed pixels'
        chunks.append(struct.pack('>I', len(payload)) + kind + payload +
                      struct.pack('>I', zlib.crc32(kind + payload)))
        position += size + 12
    path.write_bytes(b''.join(chunks))
    content = {k: v for k, v in request.items() if k != 'request_id'}
    content['output_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    changed = dict(content, request_id=_id('vision-request-', content))
    class Forbidden(FakeProvider):
        def inspect(self, *args, **kwargs):
            pytest.fail('corrupt PNG must fail before provider')
    with pytest.raises(VisionContractError, match='compressed data'):
        run_vision(changed, [path], Forbidden(), model='test')


@pytest.mark.parametrize('text', [
    '{"observation":"a","status":"supported","status":"conflict"}',
    '{"observation":"a","status":"supported","confidence":0.9}',
    '{"observation":"a","status":"approved"}',
    '{"observation":"","status":"supported"}',
    '{"observation":NaN,"status":"supported"}',
])
def test_malformed_structured_output_is_failure(request_case, monkeypatch, text):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret')
    request, path = request_case
    body = response_body()
    body['output'][0]['content'][0]['text'] = text
    with pytest.raises(VisionExecutionError, match='malformed_response'):
        run_vision(request, [path], provider(lambda req: httpx.Response(200, json=body)),
                   model='test', live=True)


def test_missing_auth_and_invalid_timeout_are_pre_network(request_case, monkeypatch):
    request, path = request_case
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    adapter = provider(lambda req: pytest.fail('network forbidden'))
    with pytest.raises(VisionExecutionError, match='auth_missing'):
        run_vision(request, [path], adapter, model='test', live=True)
    with pytest.raises(VisionContractError):
        run_vision(request, [path], adapter, model='test', live=True, timeout=float('nan'))


def test_provider_cannot_rewrite_evidence_request(request_case):
    from tools.vision.contract import _id
    request, path = request_case
    class Mutating(FakeProvider):
        def inspect(self, supplied, images, **kwargs):
            supplied['document']['source_sha256'] = 'f' * 64
            content = {k: v for k, v in supplied.items() if k != 'request_id'}
            supplied['request_id'] = _id('vision-request-', content)
            return dict(observation='Test only', status='unresolved', provider_run_id='r')
    observation = run_vision(request, [path], Mutating(), model='test')
    assert observation['request'] == request
    assert request['document']['source_sha256'] != 'f' * 64


@pytest.mark.parametrize('stage', ['V1', 'V3'])
def test_valid_png_replacement_with_same_dimensions_is_rejected(tmp_path, stage):
    import fitz
    from test_pdf_pipeline_vision_context_render import make_context_case, render as context_render
    from tools.vision import build_vision_context_inspection_request
    if stage == 'V1':
        case = make_context_case(tmp_path)
        result = context_render(case, dpi=72)
        path = output_file(case, result)
        request = build_vision_context_inspection_request(result, path, 'Inspect context')
    else:
        case = make_case(tmp_path)
        result = render(case, dpi=72)
        path = output_file(case, result)
        request = build_vision_inspection_request(result, path, 'Inspect exact evidence')
    pixmap = fitz.Pixmap(str(path))
    pixmap.clear_with(123)
    pixmap.save(path)
    class Forbidden(FakeProvider):
        def inspect(self, *args, **kwargs):
            pytest.fail('valid swapped image must fail before provider')
    with pytest.raises(VisionContractError, match='PNG SHA mismatch'):
        run_vision(request, [path], Forbidden(), model='test')
