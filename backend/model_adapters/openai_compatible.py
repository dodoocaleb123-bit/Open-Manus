"""OpenAI-compatible adapter with no provider-specific client dependency."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Mapping

from .base import ModelAdapter
from .types import (
    HealthStatus,
    ModelConfig,
    ModelRequest,
    ModelResponse,
    ModelTransportError,
)

HttpTransport = Callable[[str, str, Mapping[str, str], bytes, float], tuple[int, bytes]]


def _default_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body or None, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise ModelTransportError(f"HTTP {exc.code} from {url}: {payload[:500]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ModelTransportError(f"Unable to reach {url}: {exc}") from exc


class OpenAICompatibleAdapter(ModelAdapter):
    """Adapter for `/v1/chat/completions` and `/v1/models` endpoints."""

    def __init__(self, config: ModelConfig, transport: HttpTransport | None = None):
        self.config = config
        self._transport = transport or _default_transport

    @property
    def role(self):
        return self.config.role

    @property
    def model(self) -> str:
        return self.config.model

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def generate(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": list(request.messages),
            "temperature": self.config.temperature if request.temperature is None else request.temperature,
            "max_tokens": self.config.max_tokens if request.max_tokens is None else request.max_tokens,
        }
        if request.tools:
            payload["tools"] = list(request.tools)
        if request.response_format is not None:
            payload["response_format"] = dict(request.response_format)

        body = json.dumps(payload).encode("utf-8")
        status, raw_body = await asyncio.to_thread(
            self._transport,
            "POST",
            self.config.endpoint("chat/completions"),
            self._headers(),
            body,
            self.config.timeout_seconds,
        )
        if status < 200 or status >= 300:
            raise ModelTransportError(f"Unexpected HTTP status {status} from {self.config.endpoint('chat/completions')}")
        try:
            data = json.loads(raw_body.decode("utf-8"))
            choice = data["choices"][0]
            message = choice.get("message", {})
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelTransportError("Model returned an invalid chat-completions response") from exc

        tool_calls = tuple(message.get("tool_calls") or ())
        content = message.get("content") or ""
        return ModelResponse(
            role=self.config.role,
            model=data.get("model", self.config.model),
            content=content,
            finish_reason=choice.get("finish_reason"),
            tool_calls=tool_calls,
            usage=data.get("usage") or {},
            raw=data,
            request_id=data.get("id"),
        )

    async def health_check(self) -> HealthStatus:
        try:
            status, _ = await asyncio.to_thread(
                self._transport,
                "GET",
                self.config.endpoint("models"),
                self._headers(),
                b"",
                min(self.config.timeout_seconds, 5.0),
            )
            return HealthStatus(
                role=self.config.role,
                model=self.config.model,
                reachable=200 <= status < 300,
                status_code=status,
                detail="Model endpoint reachable" if 200 <= status < 300 else "Unexpected status",
            )
        except ModelTransportError as exc:
            return HealthStatus(
                role=self.config.role,
                model=self.config.model,
                reachable=False,
                detail=str(exc),
            )
