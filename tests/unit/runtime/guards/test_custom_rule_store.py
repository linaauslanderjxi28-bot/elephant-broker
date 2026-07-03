"""Tests for CustomRuleStore (SQLite persistence + FIX-4 version counter)."""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from elephantbroker.runtime.guards.custom_rule_store import CustomRuleStore
from elephantbroker.schemas.guards import StaticRule

GW = "gw-test"


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = CustomRuleStore(db_path=os.path.join(tmp, "test_custom_rules.db"))
        await s.init_db()
        yield s
        await s.close()


def _rule(rule_id: str = "r1", pattern: str = "danger") -> StaticRule:
    return StaticRule(id=rule_id, pattern=pattern)


class TestCustomRuleStoreCrud:
    async def test_create_and_get_roundtrip(self, store):
        created = await store.create_rule(gateway_id=GW, rule=_rule())
        assert created.source == "custom"
        loaded = await store.get_rule(gateway_id=GW, rule_id="r1")
        assert loaded is not None
        assert loaded.pattern == "danger"

    async def test_list_rules_gateway_scoped(self, store):
        await store.create_rule(gateway_id=GW, rule=_rule("r1"))
        await store.create_rule(gateway_id="other-gw", rule=_rule("r2"))
        rules = await store.list_rules(gateway_id=GW)
        assert [r.id for r in rules] == ["r1"]

    async def test_update_rule_whitelisted_fields(self, store):
        await store.create_rule(gateway_id=GW, rule=_rule())
        updated = await store.update_rule(
            gateway_id=GW, rule_id="r1", updates={"enabled": False}
        )
        assert updated is not None
        assert updated.enabled is False

    async def test_delete_rule(self, store):
        await store.create_rule(gateway_id=GW, rule=_rule())
        assert await store.delete_rule(gateway_id=GW, rule_id="r1") is True
        assert await store.get_rule(gateway_id=GW, rule_id="r1") is None


class TestRulesVersion:
    """FIX-4: monotonic version counter for cheap change detection."""

    async def test_fresh_db_version_is_zero(self, store):
        assert await store.get_rules_version() == 0

    async def test_uninitialized_store_version_is_zero(self):
        s = CustomRuleStore(db_path="unused.db")
        assert await s.get_rules_version() == 0

    async def test_create_bumps_version(self, store):
        await store.create_rule(gateway_id=GW, rule=_rule())
        assert await store.get_rules_version() == 1

    async def test_update_bumps_version(self, store):
        await store.create_rule(gateway_id=GW, rule=_rule())
        await store.update_rule(gateway_id=GW, rule_id="r1", updates={"enabled": False})
        assert await store.get_rules_version() == 2

    async def test_delete_bumps_version(self, store):
        """Deletes MUST bump the counter — this is why MAX(updated_at) over the
        rules table would be insufficient."""
        await store.create_rule(gateway_id=GW, rule=_rule())
        await store.delete_rule(gateway_id=GW, rule_id="r1")
        assert await store.get_rules_version() == 2

    async def test_noop_delete_does_not_bump(self, store):
        assert await store.delete_rule(gateway_id=GW, rule_id="missing") is False
        assert await store.get_rules_version() == 0

    async def test_noop_update_does_not_bump(self, store):
        await store.create_rule(gateway_id=GW, rule=_rule())
        # Missing rule → no write.
        assert await store.update_rule(gateway_id=GW, rule_id="nope", updates={"enabled": False}) is None
        # No whitelisted fields → early return, no write.
        await store.update_rule(gateway_id=GW, rule_id="r1", updates={"bogus": 1})
        assert await store.get_rules_version() == 1

    async def test_version_is_monotonic_across_mixed_writes(self, store):
        await store.create_rule(gateway_id=GW, rule=_rule("r1"))
        await store.create_rule(gateway_id=GW, rule=_rule("r2"))
        await store.update_rule(gateway_id=GW, rule_id="r1", updates={"enabled": False})
        await store.delete_rule(gateway_id=GW, rule_id="r2")
        assert await store.get_rules_version() == 4

    async def test_init_db_idempotent_preserves_version(self, store):
        """Re-running init_db (restart) must NOT reset the counter — the seed
        row is INSERT OR IGNORE."""
        await store.create_rule(gateway_id=GW, rule=_rule())
        await store.init_db()
        assert await store.get_rules_version() == 1

    async def test_legacy_db_without_meta_table_migrates(self):
        """A pre-FIX-4 DB (rules table only, no meta table) gets the meta table
        seeded at version 0 on init_db and bumps normally afterwards."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "legacy.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE custom_guard_rules (
                    gateway_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    rule_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (gateway_id, rule_id)
                )"""
            )
            conn.commit()
            conn.close()

            s = CustomRuleStore(db_path=db_path)
            await s.init_db()
            assert await s.get_rules_version() == 0
            await s.create_rule(gateway_id=GW, rule=_rule())
            assert await s.get_rules_version() == 1
            await s.close()
