"""Additive V0/V2 envelopes; V1/V3 identities and contracts stay unchanged."""
from copy import deepcopy
from .contract import (_id, _require, _text, validate_vision_inspection_request,
                       build_vision_observation)
from .context_contract import validate_vision_context_inspection_request
from .context_observation import build_vision_context_observation
from .provenance import validate_created_at

STATUSES = ('supported', 'conflict', 'ambiguous', 'insufficient_evidence', 'unresolved')


def validate_request(request):
    _require(isinstance(request, dict), 'invalid request')
    stage = request.get('stage')
    if stage == 'V1':
        return validate_vision_context_inspection_request(request)
    if stage == 'V3':
        return validate_vision_inspection_request(request)
    _require(stage in ('V0', 'V2'), 'invalid stage')
    _require(set(request) == {'contract_version', 'stage', 'instruction', 'inputs',
                             'relation', 'request_id'}, 'invalid stage envelope')
    _require(type(request['contract_version']) is int and request['contract_version'] == 1,
             'invalid stage version')
    _text(request['instruction'], 'instruction')
    inputs = request['inputs']
    _require(isinstance(inputs, list) and len(inputs) == (1 if stage == 'V0' else 2),
             'invalid stage input count')
    for item in inputs:
        validate_vision_inspection_request(item)
    _require(len({i['request_id'] for i in inputs}) == len(inputs), 'duplicate inputs')
    if stage == 'V0':
        _require(inputs[0]['input_scope'] == 'page' and request['relation'] is None,
                 'V0 requires a whole page and no relation')
    else:
        relation = request['relation']
        _require(isinstance(relation, dict) and set(relation) == {'relation_type', 'source_refs'},
                 'invalid V2 relation')
        _text(relation['relation_type'], 'relation type')
        _require(isinstance(relation['source_refs'], list) and bool(relation['source_refs']),
                 'V2 relation source trace missing')
        for ref in relation['source_refs']:
            _require(isinstance(ref, dict) and set(ref) == {'kind', 'id'}, 'invalid V2 source ref')
            _require(ref['kind'] in ('drawing_reference', 'semantic_entity'), 'invalid V2 source ref kind')
            _text(ref['id'], 'V2 source ref ID')
    content = {k: v for k, v in request.items() if k != 'request_id'}
    _require(request['request_id'] == _id('vision-stage-request-', content), 'stage ID mismatch')
    return deepcopy(request)


def build_stage_request(stage, inputs, instruction, *, relation=None):
    content = dict(contract_version=1, stage=stage, instruction=instruction,
                   inputs=inputs, relation=relation)
    return validate_request(dict(content, request_id=_id('vision-stage-request-', content)))


def image_inputs(request):
    return request['inputs'] if request['stage'] in ('V0', 'V2') else [request]


def normalize_observation(request, provider, model, result):
    """Only closed, provider-neutral fields may enter canonical observations."""
    _require(isinstance(result, dict) and set(result) in (
        {'observation', 'status', 'provider_run_id'},
        {'observation', 'status', 'provider_run_id', 'created_at'}), 'invalid provider result')
    _text(result['provider_run_id'], 'provider run ID')
    created_at = validate_created_at(result.get('created_at'))
    kwargs = dict(provider_run_id=result['provider_run_id'], created_at=created_at)
    if request['stage'] == 'V1':
        return build_vision_context_observation(request, provider, model,
                                               result['observation'], result['status'], **kwargs)
    if request['stage'] == 'V3':
        return build_vision_observation(request, provider, model,
                                       result['observation'], result['status'], **kwargs)
    _text(provider, 'provider')
    _text(model, 'model')
    _text(result['observation'], 'observation')
    _require(isinstance(result['status'], str) and result['status'] in STATUSES,
             'invalid observation status')
    content = dict(contract_version=1, evidence_class='vision_observation',
                   request=validate_request(request), provider=provider, model=model,
                   observation=result['observation'], status=result['status'],
                   provider_run_id=result['provider_run_id'], created_at=created_at)
    return dict(content, observation_id=_id('vision-stage-observation-', content))


def validate_stage_observation(observation):
    """Validate additive V0/V2 observations, including nested evidence and both IDs."""
    _require(isinstance(observation, dict) and set(observation) == {
        'contract_version', 'evidence_class', 'request', 'provider', 'model',
        'observation', 'status', 'provider_run_id', 'created_at', 'observation_id'}, 'invalid stage observation')
    _require(isinstance(observation['request'], dict), 'invalid stage observation request')
    _require(observation['request'].get('stage') in ('V0', 'V2'), 'invalid stage observation request')
    request = validate_request(observation['request'])
    result = {k: observation[k] for k in ('observation', 'status', 'provider_run_id', 'created_at')}
    expected = normalize_observation(request, observation['provider'], observation['model'], result)
    _require(observation == expected, 'stage observation ID/content mismatch')
    return expected
