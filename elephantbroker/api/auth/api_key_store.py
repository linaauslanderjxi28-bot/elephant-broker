"""SQLite-backed API key store (Phase 11 dashboard auth).

Keys are stored as SHA-256 hashes — the plaintext key is returned exactly once
at creation and is never persisted or retrievable again. Every mutating and
listing operation is gateway-scoped (single-tenant-per-process, mirroring the
``GatewayIdentityMiddleware`` contract).

Follows the established SQLite store pattern (see
``elephantbroker/runtime/profiles/org_override_store.py``): a synchronous
``sqlite3.Connection`` held on ``self._conn``, ``async def init_db()`` for table
creation, ``async def`` CRUD wrappers, ``ON CONFLICT``/upsert semantics where
relevant, and ``async def close()``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ApiKeyRecord(BaseModel):
    """A stored API key (never contains the plaintext key or its hash)."""

    key_id: str  # public identifier (uuid4 hex, 32 chars) — safe to log/display
    gateway_id: str
    label: str
    key_prefix: str  # first chars of plaintext, for masked display (e.g. "eb_ak_9f")
    authority_level: int = 0  # authority granted to requests authenticated by this key
    actor_id: str | None = None  # optional bound actor for authority checks
    created_at: str = ""  # ISO 8601
    revoked_at: str | None = None


class ApiKeyStore:
    """SQLite persistence for API keys, gateway-scoped, SHA-256 hashed."""

    KEY_PREFIX = "eb_ak_"  # plaintext keys look like eb_ak_<43 urlsafe chars>

    def __init__(self, db_path: str = "data/api_keys.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    async def init_db(self) -> None:
        """Create the ``api_keys`` table if it does not exist."""
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS api_keys (
                key_hash TEXT PRIMARY KEY,
                key_id TEXT NOT NULL UNIQUE,
                gateway_id TEXT NOT NULL,
                label TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                authority_level INTEGER NOT NULL DEFAULT 0,
                actor_id TEXT,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )"""
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Crypto helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(plaintext: str) -> str:
        """Return the SHA-256 hex digest used as the lookup key."""
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_plaintext() -> str:
        """Generate a fresh plaintext key (never stored)."""
        return f"{ApiKeyStore.KEY_PREFIX}{secrets.token_urlsafe(32)}"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        gateway_id: str,
        label: str,
        authority_level: int = 0,
        actor_id: str | None = None,
    ) -> tuple[ApiKeyRecord, str]:
        """Create a key. Returns ``(record, plaintext)``.

        The plaintext is NEVER stored or retrievable again — surface it to the
        caller exactly once.
        """
        if not self._conn:
            raise RuntimeError("ApiKeyStore not initialized — call init_db() first")
        plaintext = self.generate_plaintext()
        key_hash = self._hash(plaintext)
        record = ApiKeyRecord(
            key_id=uuid.uuid4().hex,
            gateway_id=gateway_id,
            label=label,
            key_prefix=plaintext[:8],
            authority_level=authority_level,
            actor_id=actor_id,
            created_at=datetime.now(UTC).isoformat(),
            revoked_at=None,
        )
        self._conn.execute(
            """INSERT INTO api_keys
                (key_hash, key_id, gateway_id, label, key_prefix,
                 authority_level, actor_id, created_at, revoked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key_hash,
                record.key_id,
                record.gateway_id,
                record.label,
                record.key_prefix,
                record.authority_level,
                record.actor_id,
                record.created_at,
                record.revoked_at,
            ),
        )
        self._conn.commit()
        logger.info(
            "Created API key key_id=%s gateway=%s label=%s authority=%d",
            record.key_id, gateway_id, label, authority_level,
        )
        return record, plaintext

    async def validate(self, plaintext: str) -> ApiKeyRecord | None:
        """Look up a key by SHA-256 hash; return it only if not revoked."""
        if not self._conn or not plaintext:
            return None
        key_hash = self._hash(plaintext)
        cursor = self._conn.execute(
            """SELECT key_id, gateway_id, label, key_prefix, authority_level,
                      actor_id, created_at, revoked_at
               FROM api_keys WHERE key_hash = ?""",
            (key_hash,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        record = self._row_to_record(row)
        if record.revoked_at is not None:
            return None
        return record

    async def revoke(self, key_id: str, *, gateway_id: str) -> bool:
        """Mark a key revoked. Returns True iff a matching row was updated."""
        if not self._conn:
            return False
        now = datetime.now(UTC).isoformat()
        cursor = self._conn.execute(
            """UPDATE api_keys SET revoked_at = ?
               WHERE key_id = ? AND gateway_id = ? AND revoked_at IS NULL""",
            (now, key_id, gateway_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    async def list_masked(self, *, gateway_id: str) -> list[ApiKeyRecord]:
        """List all keys for a gateway (masked — no hash or plaintext)."""
        if not self._conn:
            return []
        cursor = self._conn.execute(
            """SELECT key_id, gateway_id, label, key_prefix, authority_level,
                      actor_id, created_at, revoked_at
               FROM api_keys WHERE gateway_id = ?
               ORDER BY created_at DESC""",
            (gateway_id,),
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    async def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: tuple) -> ApiKeyRecord:
        return ApiKeyRecord(
            key_id=row[0],
            gateway_id=row[1],
            label=row[2],
            key_prefix=row[3],
            authority_level=row[4],
            actor_id=row[5],
            created_at=row[6],
            revoked_at=row[7],
        )
