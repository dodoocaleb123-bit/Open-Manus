import asyncio
import json

import pytest

from model_adapters import (
    ModelConfig,
    ModelConfigurationError,
    ModelRegistry,
    ModelRequest,
    ModelRole,
    OpenAICompatibleAdapter,
)


def test_registry_keeps_research_and_coder_distinct():
    registry = ModelRegistry.from_environment({})
    assert registry.get(ModelRole.RESEARCH).model == "qwen2.5:3b"
    assert registry.get(ModelRole.CODER).model == "qwen2.5-coder:7b"
    assert registry.get(ModelRole.RESEARCH).role == ModelRole.RESEARCH
    assert registry.get(ModelRole.CODER).role == ModelRole.CODER


def test_registry_rejects_wrong_specialist_model():
    with pytest.raises(ModelConfigurationError, match="Qwen 2.5:3b and Qwen2.5-Coder:7b"):
        ModelRegistry.from_environment({"RESEARCH_LLM_MODEL": "qwen2.5-coder:7b"})


def test_registry_accepts_both_documented_deepseek_versions():
    for model in ("deepseek-r1:1b", "deepseek-r1:7b"):
        registry = ModelRegistry.from_environment({"DEEPSEEK_CONTROLLER_MODEL": model})
        assert registry.get(ModelRole.CONTROLLER).model == model


def test_adapter_serializes_request_and_parses_response():
    captured = {}

    def transport(method, url, headers, body, timeout):
        captured.update(method=method, url=url, headers=headers, body=json.loads(body), timeout=timeout)
        return 200, json.dumps(
            {
                "id": "chatcmpl-test",
                "model": "qwen2.5:3b",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Research complete"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3},
            }
        ).encode()

    adapter = OpenAICompatibleAdapter(
        ModelConfig(
            role=ModelRole.RESEARCH,
            model="qwen2.5:3b",
            base_url="http://ollama.test/v1",
        ),
        transport=transport,
    )
    response = asyncio.run(adapter.generate(ModelRequest(messages=[{"role": "user", "content": "Find sources"}])))
    assert response.content == "Research complete"
    assert response.role == ModelRole.RESEARCH
    assert captured["method"] == "POST"
    assert captured["url"] == "http://ollama.test/v1/chat/completions"
    assert captured["body"]["model"] == "qwen2.5:3b"


def test_adapter_health_check_is_non_generating():
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, body))
        return 200, b'{"data": []}'

    adapter = OpenAICompatibleAdapter(
        ModelConfig(role=ModelRole.VISION, model="gemma3:4b", base_url="http://ollama.test/v1"),
        transport=transport,
    )
    health = asyncio.run(adapter.health_check())
    assert health.reachable is True
    assert calls == [("GET", "http://ollama.test/v1/models", b"")]
