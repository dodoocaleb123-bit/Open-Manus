"""Authenticated client for the isolated Open-Manus sandbox runner.

This client deliberately has no subprocess or host-execution fallback. If the
runner is unavailable or misconfigured, generated-code execution fails closed.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping


class SandboxUnavailable(RuntimeError):
    """The isolated execution service is not configured or unavailable."""


class SandboxRunnerClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        request_timeout: float = 10.0,
    ) -> None:
        self.base_url = (base_url if base_url is not None else os.getenv("OPENMANUS_SANDBOX_URL", "")).rstrip("/")
        self.token = token if token is not None else os.getenv("OPENMANUS_SANDBOX_TOKEN", "")
        self.request_timeout = request_timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def health(self) -> Mapping[str, Any]:
        return self._request("GET", "/health")

    def run(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise SandboxUnavailable(
                "Isolated code execution is unavailable: configure OPENMANUS_SANDBOX_URL "
                "and OPENMANUS_SANDBOX_TOKEN. Host execution fallback is disabled."
            )
        return self._request("POST", "/v1/jobs", payload)

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self.base_url or not self.token:
            raise SandboxUnavailable("Sandbox runner URL/token is not configured")
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                data = response.read(1_000_001)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise SandboxUnavailable("Sandbox runner is unavailable; execution was not attempted") from exc
        if len(data) > 1_000_000:
            raise SandboxUnavailable("Sandbox runner response exceeded the client limit")
        try:
            result = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SandboxUnavailable("Sandbox runner returned an invalid response") from exc
        if not isinstance(result, dict):
            raise SandboxUnavailable("Sandbox runner returned an invalid response shape")
        return result
