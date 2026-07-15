"""Unit tests for bounded chat graph extraction queue claims and audit persistence."""
from __future__ import annotations

import importlib.util
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


WORKER_PATH = Path(__file__).resolve().parents[3] / "scripts" / "chat_graph_worker.py"
SPEC = importlib.util.spec_from_file_location("chat_graph_worker", WORKER_PATH)
assert SPEC and SPEC.loader
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class FakeConnection:
    def __init__(self, job=None) -> None:
        self.fetchrow = AsyncMock(return_value=job)
        self.execute = AsyncMock()

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


class FakeSession:
    async def run(self, *args, **kwargs):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeDriver:
    def session(self):
        return FakeSession()

    async def close(self):
        return None


class FakeLLMClient:
    def __init__(self, _config):
        pass

    async def complete_json(self, *_args, **_kwargs):
        return {
            "triples": [
                {
                    "subject": "浙江出口商",
                    "predicate": "SUPPLIES_TO",
                    "object": "德国买家",
                    "confidence": 0.95,
                }
            ]
        }

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_run_one_records_accepted_relation_in_append_only_ledger(monkeypatch):
    job = {
        "id": 99,
        "fact_id": uuid.uuid4(),
        "fact_text": "浙江出口商向德国买家供应带CE认证的充电器，贸易术语为FOB宁波。",
    }
    conn = FakeConnection(job=job)
    config = SimpleNamespace(
        llm=SimpleNamespace(model="test-model"),
        cognee=SimpleNamespace(neo4j_uri="bolt://test", neo4j_user="neo4j", neo4j_password="secret"),
    )
    monkeypatch.setattr(worker, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(worker.AsyncGraphDatabase, "driver", lambda *_args, **_kwargs: FakeDriver())

    result = await worker.run_one(conn, config)

    assert result == {
        "status": "completed",
        "job_id": 99,
        "triples": 1,
        "raw_triples": 1,
        "rejected_triples": 0,
        "nodes": 2,
        "edges": 1,
        "namespace": "llm_chat_v1",
    }
    ledger_writes = [
        call for call in conn.execute.call_args_list
        if "graph_relationship_audit_v1" in call.args[0]
    ]
    assert len(ledger_writes) == 1
    ledger_args = ledger_writes[0].args
    assert len(ledger_args[1]) == 32
    assert ledger_args[2:] == (
        99,
        str(job["fact_id"]),
        "浙江出口商",
        "SUPPLIES_TO",
        "德国买家",
        0.95,
        "test-model",
        "llm_chat_v1",
    )
