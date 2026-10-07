"""Encrypted local secret storage for Phase 17 safe-secret references."""
from __future__ import annotations

import base64
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SecretStoreError(RuntimeError):
    """Raised for invalid or unavailable secret operations."""


_SECRET_PATTERN = re.compile(r"(?i)(bearer\s+|password\s*[=:]\s*|token\s*[=:]\s*|api[_-]?key\s*[=:]\s*)[^\s,;]+")


def mask_secrets(value: Any) -> Any:
    """Redact common secret-like values recursively before logging or displaying."""
    if isinstance(value, str):
        return _SECRET_PATTERN.sub(lambda match: match.group(1) + "[REDACTED]", value)
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if any(x in str(k).lower() for x in ("secret", "token", "password", "api_key", "key")) else mask_secrets(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_secrets(item) for item in value]
    return value


class SecretStore:
    """Encrypt values at rest and expose only opaque references to callers."""

    def __init__(self, database_path: str | Path, master_key: str | None = None) -> None:
        self.database_path = str(database_path)
        self._lock = threading.RLock()
        self._fernet = Fernet(self._derive_key(master_key or os.getenv("OPENMANUS_SECRET_KEY", "")))
        path = Path(self.database_path)
        if self.database_path != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.database_path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS secrets (secret_ref TEXT PRIMARY KEY, owner TEXT NOT NULL, label TEXT NOT NULL, ciphertext BLOB NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        self._db.commit()

    @staticmethod
    def _derive_key(master_key: str) -> bytes:
        if not master_key:
            raise SecretStoreError("OPENMANUS_SECRET_KEY must be configured before using the safe-secret panel")
        if master_key.startswith("gAAAA"):
            return master_key.encode("ascii")
        salt = b"openmanus-phase17-secret-store-v1"
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390000)
        return base64.urlsafe_b64encode(kdf.derive(master_key.encode("utf-8")))

    def put(self, owner: str, label: str, value: str) -> dict[str, Any]:
        if not owner or not label or not value:
            raise SecretStoreError("owner, label, and value are required")
        ref = "secret_" + uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._db.execute("INSERT INTO secrets VALUES (?, ?, ?, ?, ?, ?)", (ref, owner, label, self._fernet.encrypt(value.encode()), now, now))
            self._db.commit()
        return {"secret_ref": ref, "owner": owner, "label": label, "value_exposed": False, "created_at": now}

    def resolve(self, secret_ref: str, owner: str) -> str:
        with self._lock:
            row = self._db.execute("SELECT owner, ciphertext FROM secrets WHERE secret_ref=?", (secret_ref,)).fetchone()
        if not row or row[0] != owner:
            raise SecretStoreError("secret reference is unavailable")
        try:
            return self._fernet.decrypt(row[1]).decode("utf-8")
        except InvalidToken as exc:
            raise SecretStoreError("secret could not be decrypted") from exc

    def describe(self, owner: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._db.execute("SELECT secret_ref, label, created_at, updated_at FROM secrets WHERE owner=? ORDER BY created_at", (owner,)).fetchall()
        return tuple({"secret_ref": r[0], "label": r[1], "created_at": r[2], "updated_at": r[3], "value_exposed": False} for r in rows)

    def close(self) -> None:
        with self._lock:
            self._db.close()
