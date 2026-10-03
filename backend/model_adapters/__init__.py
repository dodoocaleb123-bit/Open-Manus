"""Phase 2 role-aware model adapters and Phase 3 controller knowledge."""

from .base import ModelAdapter
from .controller import DeepSeekController
from .openai_compatible import OpenAICompatibleAdapter
from .platform_knowledge import (
    ModelRoleProfile,
    PlatformContext,
    PlatformKnowledge,
    default_platform_knowledge,
)
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
    "DeepSeekController",
    "HealthStatus",
    "ModelAdapter",
    "ModelAdapterError",
    "ModelConfig",
    "ModelConfigurationError",
    "ModelRegistry",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "ModelRoleProfile",
    "ModelTransportError",
    "OpenAICompatibleAdapter",
    "PlatformContext",
    "PlatformKnowledge",
    "default_platform_knowledge",
]
