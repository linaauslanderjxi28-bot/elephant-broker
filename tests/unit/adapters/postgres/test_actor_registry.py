"""Tests for PostgreSQL-backed actor registry KG dual-write."""
from __future__ import annotations

from unittest.mock import AsyncMock

from elephantbroker.runtime.adapters.postgres.actor_registry import PostgresActorRegistry
from tests.fixtures.factories import make_actor_ref


class _AcquireCtx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self):
        self.conn = AsyncMock()
        self.conn.execute = AsyncMock()

    def acquire(self):
        return _AcquireCtx(self.conn)


class TestPostgresActorRegistryKGDualWrite:
    async def test_register_actor_dual_writes_actor_datapoint(self, monkeypatch, mock_add_data_points):
        monkeypatch.setattr(
            "elephantbroker.runtime.adapters.postgres.actor_registry.add_data_points",
            mock_add_data_points,
        )
        pool = _FakePool()
        registry = PostgresActorRegistry(pool=pool, gateway_id="gw-test")  # type: ignore[arg-type]
        actor = make_actor_ref(display_name="KG3 Actor")

        result = await registry.register_actor(actor)

        assert result.id == actor.id
        assert pool.conn.execute.await_count == 1
        assert len(mock_add_data_points.calls) == 1
        dp = mock_add_data_points.calls[0]["data_points"][0]
        assert dp.eb_id == str(actor.id)
        assert dp.display_name == "KG3 Actor"
        assert dp.gateway_id == "gw-test"
