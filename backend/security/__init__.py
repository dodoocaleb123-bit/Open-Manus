"""Approval-gated external, destructive, and irreversible actions."""

from .actions import SecureActionError, SecureActionSpec, SecureActions

__all__ = ["SecureActionError", "SecureActionSpec", "SecureActions"]
