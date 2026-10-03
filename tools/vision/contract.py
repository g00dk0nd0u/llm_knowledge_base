"""Strict JSON contracts using only Python's standard library."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import zlib


class VisionContractError(ValueError):
    """Invalid evidence or provider output; no fallback is permitted."""


def _require(condition, message):
    if not condition:
        raise VisionContractError(message)


def _text(value, name):
    _require(isinstance(value, str) and bool(value.strip()), f"invalid {name}")


def _sha(value):
    _require(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None,
             'invalid SHA-256')


def _integer(value, low, high, name):
    _require(type(value) is int and low <= value <= high, f"invalid {name}")


def _id(prefix, content):
    try:
        data = json.dumps(content, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError) as exc:
        raise VisionContractError('content must be finite JSON') from exc
    return prefix + hashlib.sha256(data).hexdigest()


_FIELDS = {'contract_version', 'stage', 'semantic_entity_id', 'candidate_id', 'document',
           'pdf_page', 'input_scope', 'bbox', 'coordinate_space', 'instruction', 'dpi',
           'pixel_width', 'pixel_height', 'renderer', 'output_sha256'}


def _validate(content):
    _require(isinstance(content, dict) and set(content) == _FIELDS, 'invalid request fields')
    _require(type(content['contract_version']) is int and content['contract_version'] == 1,
             'invalid contract version')
    _require(content['stage'] == 'V3', 'only V3 is supported')
    for name in ('semantic_entity_id', 'instruction'):
        _text(content[name], name)
    _require(isinstance(content['candidate_id'], str) and re.fullmatch(
        'vision-candidate-[a-f0-9]{64}', content['candidate_id']) is not None, 'invalid candidate ID')
    document = content['document']
    _require(isinstance(document, dict) and set(document) == {'id', 'identity', 'source_sha256'},
             'invalid document identity')
    for name in ('id', 'identity'):
        _text(document[name], name)
    _sha(document['source_sha256'])
    _sha(content['output_sha256'])
    _integer(content['pdf_page'], 1, 2**31 - 1, 'page')
    _integer(content['dpi'], 36, 1200, 'DPI')
    for name in ('pixel_width', 'pixel_height'):
        _integer(content[name], 1, 2**31 - 1, name)
    bbox, space = content['bbox'], content['coordinate_space']
    _require(space in (None, 'pdf_points_top_left'), 'invalid coordinate space')
    if content['input_scope'] == 'page':
        _require(bbox is None, 'page bbox must be null')
    else:
        _require(content['input_scope'] == 'region' and space == 'pdf_points_top_left',
                 'invalid region scope/coordinate space')
        try:
            valid = (isinstance(bbox, list) and len(bbox) == 4
                     and all(type(v) in (int, float) and math.isfinite(v) for v in bbox)
                     and 0 <= bbox[0] < bbox[2] and 0 <= bbox[1] < bbox[3])
        except OverflowError:
            valid = False
        _require(valid, 'invalid bbox')
    renderer = content['renderer']
    _require(isinstance(renderer, dict) and set(renderer) == {
        'name', 'library', 'library_version', 'page_rotation', 'alpha'}, 'invalid renderer')
    for name in ('name', 'library', 'library_version'):
        _text(renderer[name], name)
    _require(type(renderer['page_rotation']) is int and renderer['page_rotation'] in (0, 90, 180, 270)
             and renderer['alpha'] is False, 'invalid rendering parameters')
    _id('', content)


def validate_vision_inspection_request(request):
    """Validate content and its ID, returning an isolated copy (no image I/O)."""
    _require(isinstance(request, dict) and set(request) == _FIELDS | {'request_id'},
             'invalid request fields')
    content = {k: v for k, v in request.items() if k != 'request_id'}
    _validate(content)
    _require(request['request_id'] == _id('vision-request-', content), 'request ID mismatch')
    return deepcopy(request)


def _png_dimensions(data):
    _require(data[:8] == b'\x89PNG\r\n\x1a\n', 'invalid PNG signature')
    offset, dimensions, has_data, ended = 8, None, False, False
    while offset < len(data):
        _require(offset + 12 <= len(data), 'truncated PNG')
        length, kind = struct.unpack('>I4s', data[offset:offset + 8])
        end = offset + 12 + length
        _require(end <= len(data), 'truncated PNG chunk')
        payload = data[offset + 8:end - 4]
        _require(zlib.crc32(kind + payload) == struct.unpack('>I', data[end - 4:end])[0],
                 'invalid PNG CRC')
        if dimensions is None:
            _require(kind == b'IHDR' and length == 13, 'invalid PNG IHDR')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
            _require(width > 0 and height > 0 and compression == filtering == 0
                     and interlace in (0, 1) and color in (0, 2, 3, 4, 6)
                     and depth in {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8),
                                   4: (8, 16), 6: (8, 16)}[color], 'invalid PNG IHDR')
            dimensions = width, height
        else:
            _require(kind != b'IHDR', 'duplicate PNG IHDR')
        has_data |= kind == b'IDAT'
        if kind == b'IEND':
            _require(length == 0 and end == len(data), 'invalid PNG end')
            ended = True
            break
        offset = end
    _require(ended and has_data, 'incomplete PNG')
    return dimensions


def build_vision_inspection_request(render_result, image_path, instruction, stage='V3'):
    """Verify a Phase 5B result against PNG bytes. Paths never enter the contract."""
    _require(isinstance(render_result, dict) and render_result.get('status') == 'ok',
             'render result must be successful')
    try:
        content = {k: deepcopy(render_result[k]) for k in _FIELDS - {
            'contract_version', 'stage', 'instruction'}}
        content['document'] = {k: deepcopy(render_result['document'][k])
                               for k in ('id', 'identity', 'source_sha256')}
    except (KeyError, TypeError) as exc:
        raise VisionContractError('incomplete render result') from exc
    content.update(contract_version=1, stage=stage, instruction=instruction)
    _validate(content)
    try:
        data = Path(image_path).read_bytes()
    except (OSError, TypeError) as exc:
        raise VisionContractError('cannot read PNG') from exc
    dimensions = _png_dimensions(data)
    _require(hashlib.sha256(data).hexdigest() == content['output_sha256'], 'PNG SHA mismatch')
    _require(dimensions == (content['pixel_width'], content['pixel_height']), 'PNG dimension mismatch')
    return dict(content, request_id=_id('vision-request-', content))


def build_vision_observation(request, provider, model, observation, status, *, run_id=None, created_at=None):
    """Attach provider text to validated evidence; callers cannot replace its identity."""
    evidence = validate_vision_inspection_request(request)
    for value, name in ((provider, 'provider'), (model, 'model'), (observation, 'observation')):
        _text(value, name)
    _require(status in ('supported', 'conflict', 'ambiguous', 'insufficient_evidence', 'unresolved'),
             'invalid observation status')
    if run_id is not None:
        _text(run_id, 'run ID')
    if created_at is not None:
        _text(created_at, 'timestamp')
        try:
            stamp = datetime.fromisoformat(created_at)
            _require(stamp.utcoffset() is not None, 'timestamp must be timezone-aware')
        except ValueError as exc:
            raise VisionContractError('invalid timezone-aware timestamp') from exc
    _require(run_id is not None or created_at is not None, 'run ID or timestamp required')
    content = dict(contract_version=1, evidence_class='vision_observation', request=evidence,
                   provider=provider, model=model, observation=observation, status=status,
                   run_id=run_id, created_at=created_at)
    return dict(content, observation_id=_id('vision-observation-', content))
