"""Unit tests for :class:`ApiKeyStore` (Phase 11 auth).

Covers create/validate/revoke/list_masked, SHA-256 hashing (plaintext never
persisted), the ``eb_ak_`` key format, and strict gateway-scoping/isolation.
Uses a real temp-file SQLite store — no external DB, no SuperTokens.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile

import pytest

from elephantbroker.api.auth.api_key_store import ApiKeyRecord, ApiKeyStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = ApiKeyStore(db_path=os.path.join(tmp, "api_keys.db"))
        await s.init_db()
        yield s
        await s.close()


class TestGenerateAndHash:
    def test_generate_plaintext_has_prefix(self):
        key = ApiKeyStore.generate_plaintext()
        assert key.startswith(ApiKeyStore.KEY_PREFIX)
        assert key.startswith("eb_ak_")
        # prefix + urlsafe body → comfortably long, and unique per call.
        assert len(key) > len("eb_ak_") + 20
        assert ApiKeyStore.generate_plaintext() != key

    def test_hash_is_sha256_hex(self):
        plaintext = "eb_ak_example"
        expected = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        assert ApiKeyStore._hash(plaintext) == expected
        assert len(ApiKeyStore._hash(plaintext)) == 64


class TestCreate:
    async def test_create_returns_record_and_plaintext(self, store):
        record, plaintext = await store.create(
            gateway_id="gw1", label="ci-runner", authority_level=70, actor_id="actor-1"
        )
        assert isinstance(record, ApiKeyRecord)
        assert plaintext.startswith("eb_ak_")
        assert record.gateway_id == "gw1"
        assert record.label == "ci-runner"
        assert record.authority_level == 70
        assert record.actor_id == "actor-1"
        assert record.revoked_at is None
        assert record.created_at  # ISO timestamp stamped
        # key_id is a public uuid4 hex (32 chars), distinct from the plaintext.
        assert len(record.key_id) == 32
        assert record.key_id not in plaintext

    async def test_key_prefix_masks_plaintext(self, store):
        record, plaintext = await store.create(gateway_id="gw1", label="l")
        assert record.key_prefix == plaintext[:8]
        assert record.key_prefix.startswith("eb_ak_")
        # The masked prefix must not reveal the full secret.
        assert len(record.key_prefix) < len(plaintext)

    async def test_defaults(self, store):
        record, _ = await store.create(gateway_id="gw1", label="l")
        assert record.authority_level == 0
        assert record.actor_id is None

    async def test_plaintext_never_persisted(self, store):
        """The raw plaintext must not appear anywhere in the DB — only its hash."""
        record, plaintext = await store.create(gateway_id="gw1", label="l")
        # Inspect the underlying sqlite file directly.
        conn = sqlite3.connect(store._db_path)
        rows = conn.execute("SELECT * FROM api_keys").fetchall()
        conn.close()
        flat = " ".join(str(v) for row in rows for v in row)
        assert plaintext not in flat
        assert ApiKeyStore._hash(plaintext) in flat


class TestValidate:
    async def test_validate_accepts_live_key(self, store):
        record, plaintext = await store.create(
            gateway_id="gw1", label="l", authority_level=50, actor_id="a"
        )
        got = await store.validate(plaintext)
        assert got is not None
        assert got.key_id == record.key_id
        assert got.authority_level == 50
        assert got.actor_id == "a"

    async def test_validate_rejects_unknown_key(self, store):
        assert await store.validate("eb_ak_not_a_real_key") is None

    async def test_validate_rejects_empty(self, store):
        assert await store.validate("") is None

    async def test_validate_rejects_revoked(self, store):
        record, plaintext = await store.create(gateway_id="gw1", label="l")
        assert await store.revoke(record.key_id, gateway_id="gw1") is True
        assert await store.validate(plaintext) is None


class TestRevoke:
    async def test_revoke_marks_row(self, store):
        record, _ = await store.create(gateway_id="gw1", label="l")
        assert await store.revoke(record.key_id, gateway_id="gw1") is True
        masked = await store.list_masked(gateway_id="gw1")
        assert masked[0].revoked_at is not None

    async def test_revoke_unknown_returns_false(self, store):
        assert await store.revoke("does-not-exist", gateway_id="gw1") is False

    async def test_revoke_is_idempotent_second_call_false(self, store):
        record, _ = await store.create(gateway_id="gw1", label="l")
        assert await store.revoke(record.key_id, gateway_id="gw1") is True
        # Already revoked → no row updated the second time.
        assert await store.revoke(record.key_id, gateway_id="gw1") is False

    async def test_revoke_wrong_gateway_returns_false(self, store):
        record, plaintext = await store.create(gateway_id="gw1", label="l")
        # Cross-tenant revoke must not touch another gateway's key.
        assert await store.revoke(record.key_id, gateway_id="gw2") is False
        assert await store.validate(plaintext) is not None


class TestListMasked:
    async def test_list_masked_scoped_to_gateway(self, store):
        await store.create(gateway_id="gw1", label="a")
        await store.create(gateway_id="gw1", label="b")
        await store.create(gateway_id="gw2", label="c")
        gw1 = await store.list_masked(gateway_id="gw1")
        gw2 = await store.list_masked(gateway_id="gw2")
        assert {r.label for r in gw1} == {"a", "b"}
        assert {r.label for r in gw2} == {"c"}

    async def test_list_masked_contains_no_secret(self, store):
        _, plaintext = await store.create(gateway_id="gw1", label="l")
        masked = await store.list_masked(gateway_id="gw1")
        assert len(masked) == 1
        rec = masked[0]
        # Only the masked prefix and public id — never the plaintext/hash.
        assert rec.key_prefix == plaintext[:8]
        dumped = rec.model_dump()
        assert "key_hash" not in dumped
        assert plaintext not in str(dumped)

    async def test_list_masked_empty_gateway(self, store):
        assert await store.list_masked(gateway_id="nobody") == []
