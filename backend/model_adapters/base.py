"""Adapter protocol for role-specific local or remote models."""
from __future__ import annotations

from abc import ABC, abstractmethod

from .types import HealthStatus, ModelRequest, ModelResponse


class ModelAdapter(ABC):
    """A model endpoint with one explicitly assigned product role."""

    @property
    @abstractmethod
    def role(self):
        """Return the adapter's immutable model role."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Return the configured model name."""

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate a response for the request."""

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Check endpoint reachability without generating text."""

    async def cancel(self, request_id: str) -> None:
        """Cancel a request when the provider supports cancellation.

        The OpenAI-compatible HTTP API does not provide a portable cancellation
        endpoint. Implementations may cancel the local task; the default is a
        deliberate no-op so callers can use one interface across providers.
        """
        del request_id
