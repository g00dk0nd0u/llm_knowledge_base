"""Provider-neutral contracts and stdlib execution boundary; no source authority."""

from .contract import (
    VisionContractError,
    build_vision_inspection_request,
    build_vision_observation,
    validate_vision_inspection_request,
)
from .context_contract import (
    build_vision_context_inspection_request,
    validate_vision_context_inspection_request,
)
from .context_observation import (
    build_vision_context_observation,
    validate_vision_context_observation,
)

__all__ = ["VisionContractError", "build_vision_inspection_request",
           "build_vision_observation", "validate_vision_inspection_request",
           "build_vision_context_inspection_request",
           "validate_vision_context_inspection_request",
           "build_vision_context_observation", "validate_vision_context_observation"]

from .runner import run_vision, VisionExecutionError
from .stages import build_stage_request, validate_request, validate_stage_observation
from .review import plan_review, execute_review

__all__ += ["run_vision", "VisionExecutionError", "build_stage_request",
            "validate_request", "validate_stage_observation", "plan_review", "execute_review"]
