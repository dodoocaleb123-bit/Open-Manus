"""Role-aware model registry for the DeepSeek-first platform."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from .base import ModelAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .types import ModelConfig, ModelConfigurationError, ModelRole


_ROLE_ENV = {
    ModelRole.CONTROLLER: ("DEEPSEEK_CONTROLLER_MODEL", "DEEPSEEK_CONTROLLER_BASE_URL", "DEEPSEEK_CONTROLLER_API_KEY"),
    ModelRole.RESEARCH: ("RESEARCH_LLM_MODEL", "RESEARCH_LLM_BASE_URL", "RESEARCH_LLM_API_KEY"),
    ModelRole.CODER: ("CODER_LLM_MODEL", "CODER_LLM_BASE_URL", "CODER_LLM_API_KEY"),
    ModelRole.VISION: ("VISION_LLM_MODEL", "VISION_LLM_BASE_URL", "VISION_LLM_API_KEY"),
    ModelRole.CREATIVE: ("CREATIVE_LLM_MODEL", "CREATIVE_LLM_BASE_URL", "CREATIVE_LLM_API_KEY"),
}

_EXPECTED_PREFIX = {
    ModelRole.CONTROLLER: ("deepseek-r1:",),
    ModelRole.RESEARCH: ("qwen2.5:3b",),
    ModelRole.CODER: ("qwen2.5-coder:7b",),
    ModelRole.VISION: ("gemma3:4b",),
    ModelRole.CREATIVE: ("llama3.2:3b",),
}


@dataclass(frozen=True)
class ModelRegistry:
    """All five model adapters, indexed by their explicit product role."""

    adapters: Mapping[ModelRole, ModelAdapter]

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ModelRegistry":
        env = os.environ if environ is None else environ
        max_tokens = int(env.get("LLM_MAX_TOKENS", "4096"))
        temperature = float(env.get("LLM_TEMPERATURE", "1.0"))
        timeout = float(env.get("LLM_REQUEST_TIMEOUT", "300"))
        adapters: dict[ModelRole, ModelAdapter] = {}
        for role, (model_var, url_var, key_var) in _ROLE_ENV.items():
            model = env.get(model_var, _default_model(role))
            _validate_model_name(role, model)
            config = ModelConfig(
                role=role,
                model=model,
                base_url=env.get(url_var, "http://localhost:11434/v1"),
                api_key=env.get(key_var, "ollama"),
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_seconds=timeout,
            )
            adapters[role] = OpenAICompatibleAdapter(config)
        return cls(adapters=adapters)

    def get(self, role: ModelRole) -> ModelAdapter:
        try:
            return self.adapters[role]
        except KeyError as exc:
            raise ModelConfigurationError(f"No adapter configured for role {role.value}") from exc

    def roles(self) -> tuple[ModelRole, ...]:
        return tuple(self.adapters.keys())


def _default_model(role: ModelRole) -> str:
    return {
        ModelRole.CONTROLLER: "deepseek-r1:7b",
        ModelRole.RESEARCH: "qwen2.5:3b",
        ModelRole.CODER: "qwen2.5-coder:7b",
        ModelRole.VISION: "gemma3:4b",
        ModelRole.CREATIVE: "llama3.2:3b",
    }[role]


def _validate_model_name(role: ModelRole, model: str) -> None:
    expected = _EXPECTED_PREFIX[role]
    if not any(model == value or model.startswith(value) for value in expected):
        choices = ", ".join(expected)
        raise ModelConfigurationError(
            f"Model '{model}' is assigned to role '{role.value}', but that role requires: {choices}. "
            "Qwen 2.5:3b and Qwen2.5-Coder:7b are intentionally distinct."
        )
