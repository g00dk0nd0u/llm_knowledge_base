"""Provider-neutral contracts against real Phase 5B rendering and synthetic PNGs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import jsonschema
import pytest

from test_pdf_pipeline_vision_render import make_case, render, output_file
from tools.vision import (VisionContractError, build_vision_inspection_request as build,
                          build_vision_observation as observe, validate_vision_inspection_request)


@pytest.fixture
def case(tmp_path):
    source = make_case(tmp_path)
    result = render(source)
    return source, result, output_file(source, result)


def schema(name, value):
    document = json.loads(Path(f'schema/{name}_v1.schema.json').read_text())
    jsonschema.Draft202012Validator.check_schema(document)
    jsonschema.validate(value, document, format_checker=jsonschema.FormatChecker())


@pytest.mark.parametrize('scope', ['page', 'region'])
def test_real_render_identity_and_read_only(tmp_path, scope):
    source = make_case(tmp_path)
    before = {name: source[name].read_bytes() for name in ('pdf', 'database')}
    result = render(source, scope)
    image = output_file(source, result)
    request = build(result, image, '  Inspect 電気錠\n')
    schema('vision_inspection_request', request)
    assert request['instruction'] == '  Inspect 電気錠\n'
    assert request['bbox'] == result['bbox']
    copy = tmp_path / 'renamed.png'
    copy.write_bytes(image.read_bytes())
    changed = deepcopy(result)
    changed['output_path'] = 'arbitrary/other.png'
    assert build(changed, copy, request['instruction']) == request
    assert all(source[name].read_bytes() == data for name, data in before.items())
    assert validate_vision_inspection_request(request) == request


def test_request_id_changes(case):
    _, result, image = case
    baseline = build(result, image, 'inspect')['request_id']
    assert build(result, image, 'other')['request_id'] != baseline
    changed = deepcopy(result)
    changed['candidate_id'] = 'vision-candidate-' + '0' * 64
    assert build(changed, image, 'inspect')['request_id'] != baseline
    # A harmless ancillary text chunk changes actual PNG bytes and SHA.
    import struct
    import zlib
    payload = b'Note\0changed'
    chunk = struct.pack('>I', len(payload)) + b'tEXt' + payload
    chunk += struct.pack('>I', zlib.crc32(b'tEXt' + payload))
    image.write_bytes(image.read_bytes()[:-12] + chunk + image.read_bytes()[-12:])
    changed = deepcopy(result)
    changed['output_sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
    assert build(changed, image, 'inspect')['request_id'] != baseline


@pytest.mark.parametrize('stage', ['V0', 'V1', 'V2', None])
def test_stage(case, stage):
    _, result, image = case
    with pytest.raises(VisionContractError):
        build(result, image, 'inspect', stage)


@pytest.mark.parametrize('field,value', [
    ('bbox', None), ('bbox', [0, 0, 0, 1]), ('bbox', [0, 0, float('inf'), 1]),
    ('bbox', [True, 0, 2, 2]), ('coordinate_space', None), ('coordinate_space', 'model_mm'),
    ('pixel_width', 1), ('output_sha256', '0'*64), ('output_sha256', 'A'*64),
    ('dpi', True), ('status', 'error'), ('renderer', {}), ('candidate_id', 'bad'),
])
def test_reject_render(case, field, value):
    _, result, image = case
    result[field] = value
    with pytest.raises(VisionContractError):
        build(result, image, 'inspect')


@pytest.mark.parametrize('data', [b'bad', b'\x89PNG\r\n\x1a\n', b'\x89PNG\r\n\x1a\n'+b'\0'*30])
def test_invalid_png(case, data):
    _, result, image = case
    image.write_bytes(data)
    result['output_sha256'] = hashlib.sha256(data).hexdigest()
    with pytest.raises(VisionContractError):
        build(result, image, 'inspect')


@pytest.mark.parametrize('status', ['supported','conflict','ambiguous','insufficient_evidence','unresolved'])
def test_observation(case, status):
    _, result, image = case
    request = build(result, image, 'inspect')
    observation = observe(request, 'provider', 'model', '  observation\n', status, provider_run_id='run-1')
    assert observe(request, 'provider', 'model', '  observation\n', status, provider_run_id='run-1') == observation
    assert observation['evidence_class'] == 'vision_observation'
    assert observation['request'] == request
    schema('vision_observation', observation)
    request['document']['source_sha256'] = '0'*64
    assert observation['request']['document']['source_sha256'] != '0'*64
    with pytest.raises(VisionContractError):
        observe(request, 'provider', 'model', 'text', status, provider_run_id='run-1')


@pytest.mark.parametrize('kwargs', [dict(provider=''),dict(model=None),dict(observation={}),
    dict(status='ok'),dict(provider_run_id=None),dict(created_at='2026-10-03T00:00:00'),dict(provider_run_id=' '),
    dict(created_at='bad')])
def test_invalid_observation(case, kwargs):
    _, result, image = case
    params=dict(provider='p',model='m',observation='text',status='supported',provider_run_id='run')
    params.update(kwargs)
    with pytest.raises(VisionContractError):
        observe(build(result,image,'inspect'), **params)


def test_timestamp_and_no_override(case):
    _, result, image = case
    request = build(result,image,'inspect')
    observation = observe(request,'p','m','text','supported',created_at='2026-10-03T00:00:00+09:00')
    schema('vision_observation', observation)
    with pytest.raises(TypeError):
        observe(request,'p','m','text','supported',provider_run_id='r',candidate_id='override')
    with pytest.raises(VisionContractError):
        build(result,image,' ')
    with pytest.raises(jsonschema.ValidationError):
        schema('vision_observation',dict(observation,evidence_class='source_fact'))


def test_stdlib_runtime():
    subprocess.run([sys.executable,'-S','-c','import tools.vision'],check=True)


@pytest.mark.parametrize('document', [None, {}, {'id':'d','identity':'p','source_sha256':'A'*64},
                                     {'id':'','identity':'p','source_sha256':'0'*64}])
def test_document_validation(case, document):
    _, result, image = case
    result['document'] = document
    with pytest.raises(VisionContractError):
        build(result,image,'inspect')


def test_page_bbox_and_closed_schema(tmp_path):
    source = make_case(tmp_path)
    result = render(source,'page')
    image = output_file(source,result)
    request = build(result,image,'inspect')
    with pytest.raises(jsonschema.ValidationError):
        schema('vision_inspection_request',dict(request,image_bytes='forbidden'))
    result['bbox'] = [0,0,1,1]
    with pytest.raises(VisionContractError):
        build(result,image,'inspect')


def test_crc_and_dimensions(case):
    _, result, image = case
    data = bytearray(image.read_bytes())
    data[20] ^= 1
    image.write_bytes(data)
    result['output_sha256'] = hashlib.sha256(data).hexdigest()
    with pytest.raises(VisionContractError,match='CRC'):
        build(result,image,'inspect')


@pytest.mark.parametrize('created_at', [
    '2026-10-03T00:00:00Z',
    '2026-10-03T00:00:00+09:00',
    '2026-10-03T00:00:00-09:00',
    '2026-10-03T00:00:00.123456789Z',
    '2024-02-29T23:59:59+00:00',
    '2000-02-29T00:00:00Z',
    '0001-01-01T00:00:00Z',
    '9999-12-31T23:59:59-23:59',
])
@pytest.mark.parametrize('status', ['supported', 'conflict', 'ambiguous',
                                   'insufficient_evidence', 'unresolved'])
def test_timestamp_runtime_schema_acceptance(case, created_at, status):
    _, result, image = case
    request = build(result, image, 'inspect')
    observation = observe(request, 'p', 'm', 'text', status, created_at=created_at)
    assert observation['created_at'] == created_at
    assert observation['provider_run_id'] is None
    schema('vision_observation', observation)
    assert observe(request, 'p', 'm', 'text', status, created_at=created_at) == observation


@pytest.mark.parametrize('created_at', [
    '2026-10-03T00:00:00',
    '2026-10-03T00:00:00+0900',
    '2026-10-03T00:00:00+09:00:00',
    '2026-10-03T00:00:00+09:00:30',
    '2026-10-03T00:00:00+09:00:00.5',
    '2026-10-03T00:00:00+09',
    '2026-10-03 00:00:00Z',
    '20261003T000000Z',
    '2026-10-03T00:00:00,5Z',
    '2026-10-03t00:00:00z',
    '2026-02-29T00:00:00Z',
    '1900-02-29T00:00:00Z',
    '0000-01-01T00:00:00Z',
    '2026-04-31T00:00:00Z',
    '2026-10-03T24:00:00Z',
    '2026-10-03T00:00:60Z',
    '2026-10-03T00:00:00+24:00',
    '2026-10-03T00:00:00+09:60',
    '2026-10-03T00:00:00Z\n',
])
def test_timestamp_runtime_schema_rejection(case, created_at):
    _, result, image = case
    request = build(result, image, 'inspect')
    # Even a valid provider run identity must not mask an invalid timestamp.
    with pytest.raises(VisionContractError):
        observe(request, 'p', 'm', 'text', 'supported',
                provider_run_id='provider-run', created_at=created_at)
    observation = observe(request, 'p', 'm', 'text', 'supported', provider_run_id='provider-run')
    observation['created_at'] = created_at
    with pytest.raises(jsonschema.ValidationError):
        schema('vision_observation', observation)


def test_provider_run_identity_has_no_legacy_alias(case):
    _, result, image = case
    request = build(result, image, 'inspect')
    observation = observe(request, 'p', 'm', 'text', 'supported', provider_run_id='provider-run')
    assert observation['provider_run_id'] == 'provider-run'
    assert 'run_id' not in observation
    schema('vision_observation', observation)
    with pytest.raises(TypeError):
        observe(request, 'p', 'm', 'text', 'supported', run_id='legacy')
    observation['run_id'] = observation.pop('provider_run_id')
    with pytest.raises(jsonschema.ValidationError):
        schema('vision_observation', observation)
