#!/usr/bin/env python3
"""Asynchronously LLM-extract graphs from high-value trade chat facts.

`scan` reads recently stored FactDataPoint nodes from Neo4j, applies a fail-closed
trade gate, and appends only eligible facts to PostgreSQL's task ledger.
`run` processes at most one eligible fact in an isolated Cognee dataset.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from typing import Any

import asyncpg
from neo4j import AsyncGraphDatabase

from elephantbroker.runtime.adapters.cognee.config import configure_cognee
from elephantbroker.runtime.chat_graph_gate import classify_trade_chat
from elephantbroker.schemas.config import ElephantBrokerConfig


async def scan(conn: asyncpg.Connection, config: ElephantBrokerConfig, limit: int) -> dict[str, int]:
    driver = AsyncGraphDatabase.driver(
        config.cognee.neo4j_uri,
        auth=(config.cognee.neo4j_user, config.cognee.neo4j_password),
    )
    try:
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (f:FactDataPoint {gateway_id: $gateway_id, scope: 'session'})
                WHERE f.archived = false
                RETURN f.eb_id AS fact_id, f.text AS fact_text, f.session_key AS session_key,
                       coalesce(f.confidence, 0.0) AS confidence, f.decision_domain AS decision_domain
                ORDER BY f.eb_created_at DESC
                LIMIT $limit
                """,
                gateway_id=config.gateway.gateway_id,
                limit=limit,
            )
            facts = await result.data()
    finally:
        await driver.close()

    totals = {"facts": 0, "eligible": 0, "rejected": 0, "deduplicated": 0}
    for fact in facts:
        text = str(fact.get("fact_text") or "")
        decision = classify_trade_chat(
            text=text,
            confidence=float(fact.get("confidence") or 0.0),
            decision_domain=fact.get("decision_domain"),
        )
        if decision.status != "eligible":
            totals["rejected"] += 1
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        job_id = await conn.fetchval(
            """
            INSERT INTO chat_graph_extraction_jobs
              (fact_id,content_hash,fact_text,session_key,gateway_id,confidence,decision_domain,
               gate_status,gate_score,gate_reasons)
            VALUES ($1::uuid,$2,$3,$4,$5,$6,$7,'eligible',$8,$9::jsonb)
            ON CONFLICT (fact_id,content_hash) DO NOTHING
            RETURNING id
            """,
            fact["fact_id"],
            content_hash,
            text,
            fact.get("session_key"),
            config.gateway.gateway_id,
            float(fact.get("confidence") or 0.0),
            fact.get("decision_domain"),
            decision.score,
            json.dumps(decision.reasons),
        )
        if job_id is None:
            totals["deduplicated"] += 1
            continue
        await conn.execute(
            "INSERT INTO chat_graph_extraction_events (job_id,event_type,payload) VALUES ($1,'queued',$2::jsonb)",
            job_id,
            json.dumps({"score": decision.score}),
        )
        totals["facts"] += 1
        totals["eligible"] += 1
    return totals


async def run_one(conn: asyncpg.Connection, config: ElephantBrokerConfig) -> dict[str, Any]:
    async with conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT * FROM chat_graph_extraction_jobs WHERE gate_status='eligible'
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
            """
        )
        if job is None:
            return {"status": "idle"}
        dataset = f"trade-chat-{job['id']}-{job['content_hash'][:12]}"
        await conn.execute(
            """
            UPDATE chat_graph_extraction_jobs SET gate_status='running', attempt_count=attempt_count+1,
            started_at=NOW(), cognee_dataset=$2 WHERE id=$1
            """,
            job["id"],
            dataset,
        )
        await conn.execute(
            "INSERT INTO chat_graph_extraction_events (job_id,event_type,payload) VALUES ($1,'started',$2::jsonb)",
            job["id"],
            json.dumps({"dataset": dataset}),
        )
    try:
        import cognee

        await configure_cognee(config.cognee, config.llm, gateway_id=config.gateway.gateway_id)
        await cognee.add(job["fact_text"], dataset_name=dataset)
        result = await cognee.cognify(datasets=[dataset], run_in_background=True)
        run_id = str(result)
        await conn.execute(
            """
            UPDATE chat_graph_extraction_jobs
            SET gate_status='completed', completed_at=NOW(), cognee_run_id=$2
            WHERE id=$1
            """,
            job["id"],
            run_id,
        )
        await conn.execute(
            "INSERT INTO chat_graph_extraction_events (job_id,event_type,payload) VALUES ($1,'completed',$2::jsonb)",
            job["id"],
            json.dumps({"dataset": dataset, "cognee_result": run_id}),
        )
        return {"status": "completed", "job_id": job["id"], "dataset": dataset}
    except Exception as exc:
        await conn.execute(
            "UPDATE chat_graph_extraction_jobs SET gate_status='failed', last_error=$2 WHERE id=$1",
            job["id"],
            str(exc)[:4000],
        )
        await conn.execute(
            "INSERT INTO chat_graph_extraction_events (job_id,event_type,payload) VALUES ($1,'failed',$2::jsonb)",
            job["id"],
            json.dumps({"error": str(exc)[:4000]}),
        )
        raise


async def main_async(args: argparse.Namespace) -> int:
    config = ElephantBrokerConfig.load(args.config_path)
    conn = await asyncpg.connect(args.postgres_dsn)
    try:
        result = await scan(conn, config, args.limit) if args.command == "scan" else await run_one(conn, config)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await conn.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("scan", "run"))
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--postgres-dsn", default=os.getenv("EB_POSTGRES_DSN", ""))
    p.add_argument("--config-path", default=None)
    args = p.parse_args()
    if not args.postgres_dsn:
        p.error("EB_POSTGRES_DSN is required")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
