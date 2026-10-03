"""Phase 5E: verified V1 context surfaces with no provider or persistence."""
from copy import deepcopy
import hashlib
from itertools import product
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import jsonschema
import pytest

from test_pdf_pipeline_vision_context_render import make_context_case, render
from test_pdf_pipeline_vision_render import output_file, override, render as render_exact
from tools.vision import (
    VisionContractError,
    build_vision_context_inspection_request as build,
    build_vision_inspection_request as build_v3,
    build_vision_observation,
    validate_vision_context_inspection_request as validate,
    validate_vision_inspection_request,
)


INSTRUCTION = '  Inspect 電気錠\n原文を保持してください。\n'
SCHEMA = json.loads(Path('schema/vision_context_inspection_request_v1.schema.json').read_text())


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload)))


def png():
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x01\x02\x03'))
            + chunk(b'IEND', b''))


@pytest.fixture
def case(tmp_path):
    source = make_context_case(tmp_path)
    result = render(source, dpi=72)
    return source, result, output_file(source, result)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('cropped', [False, True])
def test_valid_request_preserves_surfaces_and_inputs(tmp_path, rotation, cropped):
    source = make_context_case(tmp_path, rotation, cropped)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in (source['pdf'], source['database'])}
    result = render(source, dpi=72)
    image = output_file(source, result)
    before[image] = image.read_bytes(), image.stat().st_mtime_ns
    files = sorted(source['root'].rglob('*'))
    original = deepcopy(result)
    request = build(result, image, INSTRUCTION)
    assert request['contract_version'] == 1
    assert request['kind'] == 'vision_context_inspection_request'
    assert request['stage'] == 'V1'
    assert request['instruction'] == INSTRUCTION
    assert request['document'] == {key: result['document'][key]
                                   for key in ('id', 'identity', 'source_sha256')}
    for name in ('semantic_entity_id', 'candidate_id', 'pdf_page', 'evidence_bbox',
                 'context_bbox', 'coordinate_space', 'input_scope', 'policy', 'margin_pt',
                 'clamped_edges', 'dpi', 'pixel_width', 'pixel_height', 'output_sha256', 'renderer'):
        assert request[name] == result[name]
    assert not {'bbox', 'output_path', 'image_path', 'image_bytes', 'base64', 'created_at'} & request.keys()
    assert validate(request) == request
    jsonschema.validate(request, SCHEMA)
    assert result == original
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}
    assert sorted(source['root'].rglob('*')) == files
    request['evidence_bbox'][0] = 1
    request['document']['id'] = 'mutated'
    request['renderer']['name'] = 'mutated'
    assert result == original


def test_deterministic_content_and_id_across_processes_and_paths(case, tmp_path):
    _, result, image = case
    request = build(result, image, INSTRUCTION)
    content = {key: value for key, value in request.items() if key != 'request_id'}
    assert request['request_id'] == 'vision-context-request-' + hashlib.sha256(
        canonical(content).encode('utf-8')).hexdigest()
    assert canonical(build(result, image, INSTRUCTION)) == canonical(request)
    copy = tmp_path / 'renamed.png'
    copy.write_bytes(image.read_bytes())
    changed = deepcopy(result)
    changed['output_path'] = '/transport-only/other.png'
    changed['document'].update(title='unrelated metadata', source_filename='renamed.pdf')
    assert build(changed, copy, INSTRUCTION) == request
    record = tmp_path / 'render.json'
    record.write_text(json.dumps(result))
    script = ('import json,sys; from tools.vision import build_vision_context_inspection_request; '
              'r=build_vision_context_inspection_request(json.load(open(sys.argv[1])), '
              'sys.argv[2], sys.argv[3]); print(json.dumps(r,ensure_ascii=False))')
    serialized = []
    for seed in ('1', '2'):
        completed = subprocess.run(
            [sys.executable, '-S', '-c', script, str(record), str(copy), INSTRUCTION],
            env=dict(os.environ, PYTHONHASHSEED=seed), capture_output=True, text=True, check=True,
        )
        assert json.loads(completed.stdout) == request
        serialized.append(completed.stdout)
    assert serialized[0] == serialized[1]


@pytest.mark.parametrize('field,value', [
    ('candidate_id', 'vision-candidate-' + '0'*64), ('semantic_entity_id', 'another-entity'),
    ('pdf_page', 2), ('dpi', 144), ('margin_pt', 144),
])
def test_identity_changes_with_valid_metadata(case, field, value):
    _, result, image = case
    baseline = build(result, image, INSTRUCTION)['request_id']
    changed = deepcopy(result)
    changed[field] = value
    assert build(changed, image, INSTRUCTION)['request_id'] != baseline


def test_instruction_image_and_policy_geometry_change_id(case):
    _, result, image = case
    baseline = build(result, image, INSTRUCTION)['request_id']
    assert build(result, image, INSTRUCTION + '追加')['request_id'] != baseline
    changed = deepcopy(result)
    changed['evidence_bbox'][0] += 1
    changed['context_bbox'][0] += 1
    assert build(changed, image, INSTRUCTION)['request_id'] != baseline
    # A different valid clamped surface has different policy geometry/identity.
    changed['evidence_bbox'][0] = 100
    changed['context_bbox'][0] = 0
    changed['clamped_edges'] = ['left']
    assert build(changed, image, INSTRUCTION)['request_id'] != baseline
    data = image.read_bytes()
    image.write_bytes(data[:-12] + chunk(b'tEXt', b'Note\0changed') + data[-12:])
    changed = deepcopy(result)
    changed['output_sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
    assert build(changed, image, INSTRUCTION)['request_id'] != baseline


@pytest.mark.parametrize('clamps', list(product((False, True), repeat=4)))
def test_all_ordered_clamp_subsets_from_real_render(tmp_path, monkeypatch, clamps):
    source = make_context_case(tmp_path)
    bbox = [10 if clamps[0] else 300, 10 if clamps[1] else 220,
            790 if clamps[2] else 380, 590 if clamps[3] else 260]
    override(source, monkeypatch)['bbox'] = bbox
    result = render(source, dpi=36)
    request = build(result, output_file(source, result), INSTRUCTION)
    assert request['clamped_edges'] == [edge for edge, clamped in zip(
        ('left', 'top', 'right', 'bottom'), clamps) if clamped]
    jsonschema.validate(request, SCHEMA)
    assert validate(request) == request


@pytest.mark.parametrize('field,value', [
    ('status', 'error'), ('status', True),
    ('stage', 'V0'), ('stage', 'V2'), ('stage', 'V3'), ('stage', None),
    ('input_scope', 'page'), ('input_scope', None),
    ('coordinate_space', None), ('coordinate_space', 'model_mm'),
    ('policy', 'other'), ('policy', None),
    ('margin_pt', 143.9), ('margin_pt', '144.0'), ('margin_pt', True),
    ('margin_pt', float('nan')), ('margin_pt', float('inf')),
    ('clamped_edges', None), ('clamped_edges', 'left'), ('clamped_edges', ['unknown']),
    ('clamped_edges', ['left', 'left']), ('clamped_edges', ['top', 'left']),
    ('clamped_edges', [1]), ('clamped_edges', [{}]), ('clamped_edges', ['left']),
    ('candidate_id', 'bad'), ('candidate_id', 'vision-candidate-' + '0'*64 + '\n'),
    ('semantic_entity_id', ' '), ('pdf_page', 0), ('pdf_page', True), ('pdf_page', 1.5),
    ('dpi', 35), ('dpi', 1201), ('dpi', True), ('dpi', 72.5),
    ('pixel_width', 0), ('pixel_width', True), ('pixel_height', 1.5),
    ('output_sha256', 'A'*64), ('renderer', {}), ('renderer', None),
    ('bbox', [0, 0, 1, 1]),
])
def test_invalid_result_rejected(case, field, value):
    _, result, image = case
    result[field] = value
    with pytest.raises(VisionContractError):
        build(result, image, INSTRUCTION)


@pytest.mark.parametrize('field', ['evidence_bbox', 'context_bbox'])
@pytest.mark.parametrize('bbox', [None, [], [0, 0, 1], '0,0,1,1', (0, 0, 1, 1),
                                  [True, 0, 1, 1], [-1, 0, 1, 1], [0, 0, 0, 1],
                                  [0, 1, 1, 1], [2, 0, 1, 1], [0, 0, '1', 1],
                                  [0, 0, float('nan'), 1], [0, 0, float('inf'), 1],
                                  [0, 0, 10**1000, 1]])
def test_invalid_rectangles(case, field, bbox):
    _, result, image = case
    result[field] = bbox
    with pytest.raises(VisionContractError):
        build(result, image, INSTRUCTION)


@pytest.mark.parametrize('bbox', [[301, 76, 525, 405], [156, 221, 525, 405],
                                  [156, 76, 380, 405], [156, 76, 525, 260]])
def test_containment_is_required(case, bbox):
    _, result, image = case
    result['context_bbox'] = bbox
    with pytest.raises(VisionContractError, match='contained'):
        build(result, image, INSTRUCTION)


def test_context_must_add_area_and_obey_policy(case):
    _, result, image = case
    changed = deepcopy(result)
    changed['context_bbox'] = changed['evidence_bbox'][:]
    with pytest.raises(VisionContractError, match='add context'):
        build(changed, image, INSTRUCTION)
    for index in range(4):
        changed = deepcopy(result)
        changed['context_bbox'][index] += 1
        with pytest.raises(VisionContractError, match='fixed margin'):
            build(changed, image, INSTRUCTION)
    changed = deepcopy(result)
    changed['clamped_edges'] = ['left']
    changed['context_bbox'][0] += 1
    with pytest.raises(VisionContractError, match='page-local origin'):
        build(changed, image, INSTRUCTION)


@pytest.mark.parametrize('instruction', ['', ' \n', None, 123, {}])
def test_nonempty_original_instruction_required(case, instruction):
    _, result, image = case
    with pytest.raises(VisionContractError):
        build(result, image, instruction)


@pytest.mark.parametrize('field,value', [
    ('source_sha256', None), ('source_sha256', ''), ('source_sha256', 123),
    ('source_sha256', 'bad'), ('source_sha256', 'A'*64), ('source_sha256', '0'*64 + '\n'),
    ('id', ''), ('identity', None), ('identity', ' \n'),
])
def test_canonical_document_identity_required(case, field, value):
    _, result, image = case
    result['document'][field] = value
    with pytest.raises(VisionContractError):
        build(result, image, INSTRUCTION)


@pytest.mark.parametrize('field', [
    'status', 'stage', 'input_scope', 'coordinate_space', 'policy', 'margin_pt',
    'evidence_bbox', 'context_bbox', 'clamped_edges', 'candidate_id', 'semantic_entity_id',
    'document', 'pdf_page', 'dpi', 'pixel_width', 'pixel_height', 'renderer', 'output_sha256',
])
def test_missing_result_metadata_has_no_fallback(case, field):
    _, result, image = case
    del result[field]
    with pytest.raises(VisionContractError):
        build(result, image, INSTRUCTION)


@pytest.mark.parametrize('field,value', [('alpha', True), ('page_rotation', True),
                                       ('page_rotation', 45), ('library', ''),
                                       ('library_version', None), ('name', ' ')])
def test_renderer_parameters_required(case, field, value):
    _, result, image = case
    result['renderer'][field] = value
    with pytest.raises(VisionContractError):
        build(result, image, INSTRUCTION)


def test_missing_png_and_no_output_path_fallback(case):
    _, result, image = case
    for path in (None, image.parent / 'missing.png', image.parent, 'bad\0path'):
        with pytest.raises(VisionContractError, match='cannot read PNG'):
            build(result, path, INSTRUCTION)


@pytest.mark.parametrize('damage', ['signature', 'header-only', 'truncated', 'CRC',
                                   'missing-IDAT', 'missing-IEND', 'duplicate-IHDR',
                                   'trailing-bytes', 'IHDR-parameters'])
def test_png_chunk_integrity(case, damage):
    _, result, image = case
    data = png()
    if damage == 'signature':
        data = b'bad' + data[3:]
    elif damage == 'header-only':
        data = data[:8]
    elif damage == 'truncated':
        data = data[:-1]
    elif damage == 'CRC':
        data = data[:20] + bytes([data[20] ^ 1]) + data[21:]
    elif damage == 'missing-IDAT':
        data = data[:33] + data[-12:]
    elif damage == 'missing-IEND':
        data = data[:-12]
    elif damage == 'duplicate-IHDR':
        data = data[:33] + data[8:33] + data[33:]
    elif damage == 'trailing-bytes':
        data += b'extra'
    else:
        data = data[:8] + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 3, 2, 0, 0, 0)) + data[33:]
    image.write_bytes(data)
    result['output_sha256'] = hashlib.sha256(data).hexdigest()
    with pytest.raises(VisionContractError):
        build(result, image, INSTRUCTION)


@pytest.mark.parametrize('failure', ['SHA', 'width', 'height'])
def test_actual_png_must_match_result(case, failure):
    _, result, image = case
    if failure == 'SHA':
        result['output_sha256'] = '0'*64
    else:
        result['pixel_' + failure] += 1
    with pytest.raises(VisionContractError, match='PNG SHA' if failure == 'SHA' else 'PNG dimension'):
        build(result, image, INSTRUCTION)


def test_closed_schema_validator_and_copy_without_io(case, monkeypatch):
    _, result, image = case
    request = build(result, image, INSTRUCTION)
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    for field, value in [('bbox', [0, 0, 1, 1]), ('image_path', str(image)), ('image_bytes', 'forbidden'),
                         ('base64', 'forbidden'), ('kind', 'other'), ('policy', 'other'),
                         ('margin_pt', 143), ('stage', 'V3'), ('clamped_edges', ['top', 'left']),
                         ('clamped_edges', ['left', 'left']), ('contract_version', True)]:
        changed = dict(request, **{field: value})
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(changed, SCHEMA)
        with pytest.raises(VisionContractError):
            validate(changed)
    for field in SCHEMA['required']:
        changed = deepcopy(request)
        del changed[field]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(changed, SCHEMA)
        with pytest.raises(VisionContractError):
            validate(changed)
    changed = dict(request, instruction='tampered without a new ID')
    with pytest.raises(VisionContractError, match='ID mismatch'):
        validate(changed)
    def forbid_read(*args):
        raise AssertionError('validation must not read files')
    monkeypatch.setattr(Path, 'read_bytes', forbid_read)
    copy = validate(request)
    copy['document']['id'] = 'mutated'
    copy['context_bbox'][0] = 0
    copy['renderer']['name'] = 'mutated'
    assert copy != request
    assert validate(request) == request


def test_v1_and_v3_contracts_remain_separate(case):
    source, result, image = case
    request = build(result, image, INSTRUCTION)
    with pytest.raises(VisionContractError):
        build_v3(result, image, INSTRUCTION)
    with pytest.raises(VisionContractError):
        validate_vision_inspection_request(request)
    with pytest.raises(VisionContractError):
        build_vision_observation(request, 'p', 'm', 'text', 'supported', provider_run_id='run')
    exact = render_exact(source, dpi=72)
    exact_image = output_file(source, exact)
    with pytest.raises(VisionContractError):
        build(exact, exact_image, INSTRUCTION)
    with pytest.raises(VisionContractError):
        validate(build_v3(exact, exact_image, INSTRUCTION))


@pytest.mark.parametrize('scope,expected', [
    ('region', 'vision-request-b74d0089201319991f48ebd3f51acc3fe41996b9af5f9dcf722f09ee3bc8c3b7'),
    ('page', 'vision-request-1914a910a0943152dfa16c07b428e983fdc43271787920e12612c3e538e525e3'),
])
def test_pinned_phase_5c_request_ids(tmp_path, scope, expected):
    # Golden IDs computed from the unchanged Phase 5C implementation on the base.
    data = png()
    image = tmp_path / 'image.png'
    image.write_bytes(data)
    result = dict(status='ok', semantic_entity_id='entity', candidate_id='vision-candidate-'+'1'*64,
                  document=dict(id='document', identity='original/synthetic.pdf', source_sha256='2'*64),
                  pdf_page=1, input_scope=scope, bbox=[20.0,30.0,100.0,70.0] if scope == 'region' else None,
                  coordinate_space='pdf_points_top_left' if scope == 'region' else None,
                  dpi=72, pixel_width=1, pixel_height=1, output_sha256=hashlib.sha256(data).hexdigest(),
                  renderer=dict(name='tools.pdf_pipeline.vision_render', library='PyMuPDF',
                                library_version='1.28.2', page_rotation=0, alpha=False))
    request = build_v3(result, image, '  Inspect 電気錠\n')
    assert request['request_id'] == expected
    assert request['bbox'] == result['bbox']
    assert validate_vision_inspection_request(request) == request


def test_stdlib_import_does_not_load_pdf_or_provider_dependencies():
    subprocess.run([sys.executable, '-S', '-c',
                    'import tools.vision, tools.query_core.query, sys; '
                    "assert not {'fitz', 'pymupdf', 'jsonschema', 'requests', 'openai'} & sys.modules.keys()"],
                   check=True)
