"""Phase 2 role-aware model adapters."""

from .base import ModelAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .registry import ModelRegistry
from .types import (
    HealthStatus,
    ModelAdapterError,
    ModelConfig,
    ModelConfigurationError,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelTransportError,
)

__all__ = [
    "HealthStatus",
    "ModelAdapter",
    "ModelAdapterError",
    "ModelConfig",
    "ModelConfigurationError",
    "ModelRegistry",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "ModelTransportError",
    "OpenAICompatibleAdapter",
]
