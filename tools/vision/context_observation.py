"""Provider-neutral V1 observations attached only to validated context requests."""
from __future__ import annotations

from datetime import datetime
import re

from .contract import VisionContractError, _CREATED_AT_PATTERN, _id, _require, _text
from .context_contract import validate_vision_context_inspection_request


_FIELDS = (
    'contract_version', 'kind', 'evidence_class', 'request', 'provider', 'model',
    'observation', 'status', 'provider_run_id', 'created_at',
)
_STATUSES = ('supported', 'conflict', 'ambiguous', 'insufficient_evidence', 'unresolved')
_PREFIX = 'vision-context-observation-'


def _validate_content(content):
    _require(isinstance(content, dict) and set(content) == set(_FIELDS),
             'invalid context observation fields')
    _require(type(content['contract_version']) is int and content['contract_version'] == 1,
             'invalid contract version')
    _require(content['kind'] == 'vision_context_observation', 'invalid observation kind')
    _require(content['evidence_class'] == 'vision_observation', 'invalid evidence class')
    evidence = validate_vision_context_inspection_request(content['request'])
    for name in ('provider', 'model', 'observation'):
        _text(content[name], name)
    _require(isinstance(content['status'], str) and content['status'] in _STATUSES,
             'invalid observation status')
    if content['provider_run_id'] is not None:
        _text(content['provider_run_id'], 'provider run ID')
    created_at = content['created_at']
    if created_at is not None:
        _text(created_at, 'timestamp')
        _require(re.fullmatch(_CREATED_AT_PATTERN, created_at) is not None,
                 'timestamp must use YYYY-MM-DDTHH:MM:SS[.fraction] and Z or +/-HH:MM')
        try:
            stamp = datetime.fromisoformat(created_at)
            _require(stamp.utcoffset() is not None, 'timestamp must be timezone-aware')
        except ValueError as exc:
            raise VisionContractError('invalid timezone-aware timestamp') from exc
    _require(content['provider_run_id'] is not None or created_at is not None,
             'provider run ID or timestamp required')
    # The request validator supplies an isolated copy; all other fields are
    # validated immutable scalars. No evidence identity is accepted separately.
    return {key: evidence if key == 'request' else content[key] for key in _FIELDS}


def build_vision_context_observation(request, provider, model, observation, status, *,
                                     provider_run_id=None, created_at=None):
    """Preserve provider text and run identity on a deep copy of validated V1 evidence.

    This constructs a transient contract only. It performs no provider execution,
    file I/O, persistence or source-fact changes, and adds no implicit timestamp.
    """
    content = _validate_content(dict(
        contract_version=1, kind='vision_context_observation', evidence_class='vision_observation',
        request=request, provider=provider, model=model, observation=observation, status=status,
        provider_run_id=provider_run_id, created_at=created_at,
    ))
    return dict(content, observation_id=_id(_PREFIX, content))


def validate_vision_context_observation(observation):
    """Validate the closed V1 envelope, nested request and both IDs; return a copy."""
    _require(isinstance(observation, dict)
             and set(observation) == set(_FIELDS) | {'observation_id'},
             'invalid context observation fields')
    content = _validate_content({key: observation[key] for key in _FIELDS})
    _require(observation['observation_id'] == _id(_PREFIX, content), 'context observation ID mismatch')
    return dict(content, observation_id=observation['observation_id'])
