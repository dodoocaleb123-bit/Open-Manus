"""Phase 17 access-control primitives for local and multi-user deployments."""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping


class AuthenticationError(PermissionError):
    """Raised when a request does not authenticate."""


class AuthorizationError(PermissionError):
    """Raised when an authenticated principal lacks a permission."""


@dataclass(frozen=True)
class Principal:
    subject: str
    permissions: frozenset[str]
    authenticated: bool = True


class TokenAuthenticator:
    """Constant-time bearer-token authentication with local-development opt-out.

    When ``OPENMANUS_AUTH_TOKEN`` is unset, the local single-user deployment is
    represented by the explicit ``local`` principal. Setting the variable turns
    on authentication without putting credentials in prompts or task state.
    """

    def __init__(self, token: str | None = None) -> None:
        self._token = token if token is not None else os.getenv("OPENMANUS_AUTH_TOKEN", "")
        raw_permissions = os.getenv(
            "OPENMANUS_AUTH_PERMISSIONS",
            "task.read,task.write,project.read,project.write,tool.execute,secret.use",
        )
        self._permissions = frozenset(item.strip() for item in raw_permissions.split(",") if item.strip())

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    def authenticate(self, authorization: str | None) -> Principal:
        if not self.enabled:
            return Principal("local", self._permissions, authenticated=True)
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied, self._token):
            raise AuthenticationError("authentication required")
        return Principal("token-user", self._permissions, authenticated=True)


class ProjectAccessController:
    """Small ACL registry; owner/editor/viewer roles map to explicit permissions."""

    ROLE_PERMISSIONS = {
        "owner": frozenset({"project.read", "project.write", "project.delete", "tool.execute", "secret.use"}),
        "editor": frozenset({"project.read", "project.write", "tool.execute"}),
        "viewer": frozenset({"project.read"}),
    }

    def __init__(self) -> None:
        self._acl: dict[str, dict[str, str]] = {}
        self._lock = threading.RLock()

    def grant(self, project: str, subject: str, role: str) -> dict[str, Any]:
        if not project or not subject or role not in self.ROLE_PERMISSIONS:
            raise AuthorizationError("project, subject, and a valid role are required")
        with self._lock:
            self._acl.setdefault(project, {})[subject] = role
        return {"project": project, "subject": subject, "role": role, "permissions": sorted(self.ROLE_PERMISSIONS[role])}

    def revoke(self, project: str, subject: str) -> None:
        with self._lock:
            self._acl.get(project, {}).pop(subject, None)

    def role(self, project: str, principal: Principal) -> str | None:
        with self._lock:
            explicit = self._acl.get(project, {}).get(principal.subject)
        if explicit:
            return explicit
        if principal.subject == "local" and not os.getenv("OPENMANUS_AUTH_TOKEN"):
            return "owner"
        return None

    def authorize(self, project: str, principal: Principal, permission: str) -> None:
        role = self.role(project, principal)
        allowed = set(principal.permissions)
        if role:
            allowed.update(self.ROLE_PERMISSIONS[role])
        if permission not in allowed and "*" not in allowed:
            raise AuthorizationError(f"principal is not authorized for {permission} on project {project}")


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after: float = 0.0


class RateLimiter:
    """Thread-safe fixed-window limiter suitable for the local HTTP API."""

    def __init__(self, limit: int = 120, window_seconds: int = 60) -> None:
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self._windows: dict[str, tuple[int, float]] = {}
        self._lock = threading.RLock()

    def check(self, key: str) -> RateLimitDecision:
        now = time.monotonic()
        with self._lock:
            count, started = self._windows.get(key, (0, now))
            if now - started >= self.window_seconds:
                count, started = 0, now
            if count >= self.limit:
                return RateLimitDecision(False, max(0.0, self.window_seconds - (now - started)))
            self._windows[key] = (count + 1, started)
            return RateLimitDecision(True)


def fingerprint_secret(value: str) -> str:
    """Return a non-reversible identifier for audit metadata."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
