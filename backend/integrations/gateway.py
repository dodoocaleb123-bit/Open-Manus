"""Connector-neutral integration gateway with an explicit DeepSeek routing boundary."""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Mapping


class IntegrationError(ValueError):
    """Raised when an integration event or action is invalid."""


class IntegrationGateway:
    PROVIDERS = {
        "email": ("Email", "event intake, message search, notification delivery"),
        "calendar": ("Calendar", "event intake, event lookup, scheduling"),
        "slack": ("Slack", "event intake, channel/message operations"),
        "notion": ("Notion", "page/database search and artifact storage"),
        "storage": ("Storage", "artifact upload and download"),
        "notifications": ("Notifications", "user notification delivery"),
        "maps": ("Maps", "geocoding, directions, and place lookup"),
        "commerce": ("Commerce", "catalog and order-service operations"),
    }

    def catalog(self) -> dict[str, Any]:
        return {"integrations": [self._status(provider) for provider in self.PROVIDERS]}

    def status(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        provider = self._provider(arguments)
        return self._status(provider)

    def receive_event(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        provider = self._provider(arguments)
        event_type = str(arguments.get("event_type", "")).strip()
        payload = arguments.get("payload", {})
        if not event_type:
            raise IntegrationError("event_type is required")
        if not isinstance(payload, Mapping):
            raise IntegrationError("payload must be an object")
        event_id = str(arguments.get("event_id", "event_" + uuid.uuid4().hex))
        return {
            "event_id": event_id,
            "provider": provider,
            "event_type": event_type,
            "received_at": time.time(),
            "payload": dict(payload),
            "payload_is_untrusted": True,
            "route": "deepseek",
            "execution_rule": "DeepSeek must interpret the event and issue an authorized command; this gateway does not execute provider actions.",
        }

    def prepare_action(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        provider = self._provider(arguments)
        action = str(arguments.get("action", "")).strip()
        details = arguments.get("details", {})
        if not action:
            raise IntegrationError("action is required")
        if not isinstance(details, Mapping):
            raise IntegrationError("details must be an object")
        status = self._status(provider)
        return {
            "provider": provider,
            "action": action,
            "details": dict(details),
            "status": "ready_for_deepseek_execution" if status["enabled"] else "connector_not_enabled",
            "requires_deepseek": True,
            "requires_approval": provider in {"email", "slack", "notifications", "commerce"},
            "message": "Prepared only; a provider adapter and the appropriate approval are required before execution." if status["enabled"] else "Enable and authorize this connector before execution.",
        }

    def specs(self) -> tuple[tuple[str, Any, str], ...]:
        return (
            ("integration_catalog", self.catalog, "List supported external integration surfaces and status"),
            ("integration_status", self.status, "Inspect one integration's local authorization status"),
            ("integration_receive_event", self.receive_event, "Normalize an email, calendar, Slack, or provider event for DeepSeek"),
            ("integration_prepare_action", self.prepare_action, "Prepare an authorized external action without executing it"),
        )

    def _provider(self, arguments: Mapping[str, Any]) -> str:
        provider = str(arguments.get("provider", "")).strip().lower()
        if provider not in self.PROVIDERS:
            raise IntegrationError(f"provider must be one of {sorted(self.PROVIDERS)}")
        return provider

    def _status(self, provider: str) -> dict[str, Any]:
        key = f"OPENMANUS_INTEGRATION_{provider.upper()}_ENABLED"
        enabled = os.getenv(key, "false").lower() == "true"
        name, capabilities = self.PROVIDERS[provider]
        return {"id": provider, "name": name, "enabled": enabled, "status": "connected" if enabled else "not_configured", "capabilities": capabilities, "authorization": "local environment flag only; provider credentials are not stored in model context"}
