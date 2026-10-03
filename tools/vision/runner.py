"""Common execution boundary. No optional imports, logging, or persistence."""
import hashlib
from copy import deepcopy
from pathlib import Path
from .contract import VisionContractError, _require, _text
from .png import verify_png
from .stages import validate_request, image_inputs, normalize_observation


class VisionExecutionError(RuntimeError):
    """Safe failure category; never includes transport response or credentials."""
    _CODES = frozenset({
        'live_opt_in_required', 'auth_missing', 'auth_failed', 'http_error',
        'timeout', 'transport_error', 'malformed_response', 'partial_response',
        'refusal', 'optional_dependency_missing', 'provider_failure', 'source_unreadable',
    })

    def __init__(self, code):
        code = code if isinstance(code, str) and code in self._CODES else 'provider_failure'
        self.code = code
        super().__init__(code)


def run_vision(request, image_paths, provider, *, model, live=False, timeout=60.0):
    """Read once, revalidate and send those same immutable PNG bytes.

    Provider implements name, requires_live and inspect(request, images, model,
    timeout). Fake transports opt out of live consent; real transports must opt in.
    """
    evidence = validate_request(request)
    _text(model, 'model')
    provider_name = provider.name
    _text(provider_name, 'provider')
    _require(type(live) is bool, 'invalid live opt-in')
    _require(type(timeout) in (int, float) and 0 < timeout <= 300, 'invalid timeout')
    if getattr(provider, 'requires_live', True) and not live:
        raise VisionExecutionError('live_opt_in_required')
    inputs = image_inputs(evidence)
    _require(isinstance(image_paths, (list, tuple)) and len(image_paths) == len(inputs),
             'image count mismatch')
    images = []
    for item, path in zip(inputs, image_paths, strict=True):
        try:
            with Path(path).open('rb') as handle:
                data = handle.read(64 * 1024 * 1024 + 1)
        except (OSError, TypeError, ValueError):
            raise VisionContractError('cannot read PNG') from None
        _require(verify_png(data) == (item['pixel_width'], item['pixel_height']),
                 'PNG dimension mismatch')
        _require(hashlib.sha256(data).hexdigest() == item['output_sha256'], 'PNG SHA mismatch')
        images.append(data)
    try:
        result = provider.inspect(deepcopy(evidence), tuple(images), model=model, timeout=timeout)
        return normalize_observation(evidence, provider_name, model, result)
    except VisionExecutionError:
        raise
    except VisionContractError:
        raise VisionExecutionError('malformed_response') from None
    except Exception:
        raise VisionExecutionError('provider_failure') from None
