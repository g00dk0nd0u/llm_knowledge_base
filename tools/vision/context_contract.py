"""Provider-neutral V1 context requests; no execution or persistence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re

from .contract import (
    VisionContractError, _id, _integer, _png_dimensions, _require, _sha, _text,
)


_FIELDS = (
    'contract_version', 'kind', 'stage', 'instruction', 'semantic_entity_id',
    'candidate_id', 'document', 'pdf_page', 'input_scope', 'evidence_bbox',
    'context_bbox', 'coordinate_space', 'policy', 'margin_pt', 'clamped_edges',
    'dpi', 'pixel_width', 'pixel_height', 'output_sha256', 'renderer',
)
_EDGES = ('left', 'top', 'right', 'bottom')
_PREFIX = 'vision-context-request-'


def _bbox(value, name):
    try:
        valid = (isinstance(value, list) and len(value) == 4
                 and all(type(v) in (int, float) and math.isfinite(v) for v in value)
                 and 0 <= value[0] < value[2] and 0 <= value[1] < value[3])
    except OverflowError:
        valid = False
    _require(valid, f'invalid {name}')


def _validate(content):
    _require(isinstance(content, dict) and set(content) == set(_FIELDS),
             'invalid context request fields')
    _require(type(content['contract_version']) is int and content['contract_version'] == 1,
             'invalid contract version')
    _require(content['kind'] == 'vision_context_inspection_request', 'invalid request kind')
    _require(content['stage'] == 'V1', 'only V1 context is supported')
    _require(content['input_scope'] == 'region', 'V1 context requires region scope')
    _require(content['coordinate_space'] == 'pdf_points_top_left', 'invalid coordinate space')
    _require(content['policy'] == 'fixed_margin_v1', 'invalid context policy')
    _require(type(content['margin_pt']) in (int, float) and content['margin_pt'] == 144.0,
             'invalid context margin')
    for name in ('instruction', 'semantic_entity_id'):
        _text(content[name], name)
    _require(isinstance(content['candidate_id'], str) and re.fullmatch(
        'vision-candidate-[a-f0-9]{64}', content['candidate_id']) is not None,
        'invalid candidate ID')
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
    evidence, context = content['evidence_bbox'], content['context_bbox']
    _bbox(evidence, 'evidence bbox')
    _bbox(context, 'context bbox')
    _require(context[0] <= evidence[0] and context[1] <= evidence[1]
             and evidence[2] <= context[2] and evidence[3] <= context[3],
             'evidence bbox must be contained in context bbox')
    _require(context != evidence, 'context bbox must add context')
    edges = content['clamped_edges']
    _require(isinstance(edges, list) and all(isinstance(edge, str) for edge in edges)
             and edges == [edge for edge in _EDGES if edge in edges],
             'invalid clamped edges: require unique known edges in fixed order')
    # Phase 5D records exactly those edges whose requested expansion changed.
    # We cannot reverify right/bottom page bounds without the original PDF.
    expanded = [evidence[0] - 144.0, evidence[1] - 144.0,
                evidence[2] + 144.0, evidence[3] + 144.0]
    for index, edge in enumerate(_EDGES):
        if edge in edges:
            _require(context[index] > expanded[index] if index < 2
                     else context[index] < expanded[index],
                     'clamped edge must reduce the fixed margin')
            if index < 2:
                _require(context[index] == 0, 'left/top clamp must use page-local origin')
        else:
            _require(context[index] == expanded[index], 'unclamped edge must use fixed margin')
    renderer = content['renderer']
    _require(isinstance(renderer, dict) and set(renderer) == {
        'name', 'library', 'library_version', 'page_rotation', 'alpha'}, 'invalid renderer')
    for name in ('name', 'library', 'library_version'):
        _text(renderer[name], name)
    _require(type(renderer['page_rotation']) is int
             and renderer['page_rotation'] in (0, 90, 180, 270)
             and renderer['alpha'] is False, 'invalid rendering parameters')
    _id('', content)


def validate_vision_context_inspection_request(request):
    """Check the closed envelope and canonical content ID; no image I/O."""
    _require(isinstance(request, dict) and set(request) == set(_FIELDS) | {'request_id'},
             'invalid context request fields')
    content = {key: request[key] for key in _FIELDS}
    _validate(content)
    _require(request['request_id'] == _id(_PREFIX, content), 'context request ID mismatch')
    return deepcopy(request)


def build_vision_context_inspection_request(render_result, image_path, instruction):
    """Verify a successful Phase 5D result and explicit PNG, preserving input text.

    Paths and optional document metadata are transport/navigation only. Source
    identity is supplied by the renderer; this does not reopen a PDF or database.
    """
    _require(isinstance(render_result, dict) and render_result.get('status') == 'ok',
             'render result must be successful')
    _require('bbox' not in render_result, 'ambiguous bbox is not a V1 context result')
    supplied = {'contract_version': 1, 'kind': 'vision_context_inspection_request',
                'instruction': instruction}
    try:
        content = {key: deepcopy(supplied[key] if key in supplied else render_result[key])
                   for key in _FIELDS}
        content['document'] = {key: deepcopy(render_result['document'][key])
                               for key in ('id', 'identity', 'source_sha256')}
    except (KeyError, TypeError) as exc:
        raise VisionContractError('incomplete context render result') from exc
    _validate(content)
    try:
        data = Path(image_path).read_bytes()
    except (OSError, TypeError, ValueError) as exc:
        raise VisionContractError('cannot read PNG') from exc
    dimensions = _png_dimensions(data)
    _require(hashlib.sha256(data).hexdigest() == content['output_sha256'], 'PNG SHA mismatch')
    _require(dimensions == (content['pixel_width'], content['pixel_height']), 'PNG dimension mismatch')
    return dict(content, request_id=_id(_PREFIX, content))
