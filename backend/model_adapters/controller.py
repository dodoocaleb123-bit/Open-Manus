"""DeepSeek controller facade for Phase 3 platform knowledge."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .base import ModelAdapter
from .platform_knowledge import PlatformContext, PlatformKnowledge, default_platform_knowledge
from .types import ModelRequest, ModelResponse, ModelRole, ModelConfigurationError
from .protocol import ControllerDecision, parse_decision


class DeepSeekController:
    """The only user-facing model entry point in the platform.

    This class does not route requests itself. It prepares DeepSeek's context and
    calls only the controller adapter. DeepSeek remains responsible for choosing
    specialist work in later phases.
    """

    def __init__(
        self,
        adapter: ModelAdapter,
        knowledge: PlatformKnowledge | None = None,
    ) -> None:
        if adapter.role != ModelRole.CONTROLLER:
            raise ModelConfigurationError(
                "DeepSeekController requires the controller adapter; specialist adapters cannot receive user requests first."
            )
        self.adapter = adapter
        self.knowledge = knowledge or default_platform_knowledge()

    def system_prompt(self, context: PlatformContext | None = None) -> str:
        return self.knowledge.system_prompt(context)

    def build_request(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        context: PlatformContext | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ModelRequest:
        """Build a request with exactly one platform system message first."""
        conversation = tuple(messages)
        if any(message.get("role") == "system" for message in conversation):
            raise ValueError("Controller messages must not include a caller-supplied system message")
        system_message = {"role": "system", "content": self.system_prompt(context)}
        return ModelRequest(messages=(system_message, *conversation), tools=tuple(tools))

    async def respond(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        context: PlatformContext | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ModelResponse:
        """Send the user conversation directly to DeepSeek with platform knowledge."""
        return await self.adapter.generate(self.build_request(messages, context=context, tools=tools))

    async def decide(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        context: PlatformContext | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ControllerDecision:
        """Ask DeepSeek for one validated user message plus one protocol command."""
        request = self.build_request(messages, context=context, tools=tools)
        request = ModelRequest(
            messages=request.messages,
            tools=request.tools,
            response_format={"type": "json_object"},
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            metadata=request.metadata,
        )
        response = await self.adapter.generate(request)
        return parse_decision(response.content)
