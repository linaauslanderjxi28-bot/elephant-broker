"""Unit tests for bounded chat graph extraction queue claims."""
from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock


WORKER_PATH = Path(__file__).resolve().parents[3] / "scripts" / "chat_graph_worker.py"
SPEC = importlib.util.spec_from_file_location("chat_graph_worker", WORKER_PATH)
assert SPEC and SPEC.loader
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class FakeConnection:
    def __init__(self) -> None:
        self.fetchrow = AsyncMock(return_value=None)

    @asynccontextmanager
    async def transaction(self):
        yield self


async def test_run_one_claims_only_eligible_by_default():
    conn = FakeConnection()

    result = await worker.run_one(conn, object())

    assert result == {"status": "idle"}
    sql, statuses, attempts = conn.fetchrow.call_args.args
    assert "gate_status = ANY" in sql
    assert statuses == ("eligible",)
    assert attempts == 3


async def test_run_one_can_retry_failed_with_bounded_attempts():
    conn = FakeConnection()

    result = await worker.run_one(conn, object(), retry_failed=True, max_attempts=2)

    assert result == {"status": "idle"}
    sql, statuses, attempts = conn.fetchrow.call_args.args
    assert "gate_status = ANY" in sql
    assert statuses == ("eligible", "failed")
    assert attempts == 2
