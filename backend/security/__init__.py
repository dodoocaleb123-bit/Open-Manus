"""Approval-gated actions and Phase 17 security controls."""

from .actions import SecureActionError, SecureActionSpec, SecureActions
from .access import AuthenticationError, AuthorizationError, Principal, ProjectAccessController, RateLimitDecision, RateLimiter, TokenAuthenticator, fingerprint_secret
from .secrets import SecretStore, SecretStoreError, mask_secrets

__all__ = [
    "AuthenticationError", "AuthorizationError", "Principal", "ProjectAccessController",
    "RateLimitDecision", "RateLimiter", "SecureActionError", "SecureActionSpec", "SecureActions",
    "SecretStore", "SecretStoreError", "TokenAuthenticator", "fingerprint_secret", "mask_secrets",
]
