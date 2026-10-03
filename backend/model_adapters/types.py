"""Shared types for the DeepSeek-first model adapter layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence


class ModelRole(StrEnum):
    CONTROLLER = "controller"
    RESEARCH = "research"
    CODER = "coder"
    VISION = "vision"
    CREATIVE = "creative"


@dataclass(frozen=True)
class ModelConfig:
    role: ModelRole
    model: str
    base_url: str
    api_key: str = "ollama"
    max_tokens: int = 4096
    temperature: float = 1.0
    timeout_seconds: float = 300.0

    def endpoint(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


@dataclass(frozen=True)
class ModelRequest:
    messages: Sequence[Mapping[str, Any]]
    tools: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    response_format: Mapping[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    role: ModelRole
    model: str
    content: str
    finish_reason: str | None = None
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)
    request_id: str | None = None


@dataclass(frozen=True)
class HealthStatus:
    role: ModelRole
    model: str
    reachable: bool
    status_code: int | None = None
    detail: str = ""


class ModelAdapterError(RuntimeError):
    """Base error raised by a model adapter."""


class ModelConfigurationError(ModelAdapterError):
    """Raised when a model is assigned to the wrong product role."""


class ModelTransportError(ModelAdapterError):
    """Raised when an OpenAI-compatible endpoint cannot be reached."""
