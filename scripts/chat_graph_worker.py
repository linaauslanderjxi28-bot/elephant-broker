#!/usr/bin/env python3
"""Single-Neo4j asynchronous LLM trade-chat graph extractor (no Cognee cognify)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import UTC, datetime

import asyncpg
from neo4j import AsyncGraphDatabase

from elephantbroker.runtime.adapters.llm.client import LLMClient
from elephantbroker.schemas.config import ElephantBrokerConfig

SCHEMA = {
    "type": "object",
    "properties": {
        "triples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "object": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["subject", "predicate", "object", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["triples"],
    "additionalProperties": False,
}
SYSTEM = (
    "Extract only explicit cross-border-trade relations from this chat fact. "
    "Return JSON only. A triple must have concrete subject and object, an "
    "UPPER_SNAKE_CASE predicate, and confidence 0..1. Never infer unstated facts. "
    'Return {"triples":[]} if no reliable explicit relation exists.'
)
NAMESPACE = "llm_chat_v1"


def key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:180]


async def run_one(conn, config, *, retry_failed: bool = False, max_attempts: int = 3):
    claim_statuses = ("eligible", "failed") if retry_failed else ("eligible",)
    async with conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT * FROM chat_graph_extraction_jobs
            WHERE gate_status = ANY($1::text[])
              AND attempt_count < $2
            ORDER BY CASE gate_status WHEN 'eligible' THEN 0 ELSE 1 END, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            claim_statuses,
            max_attempts,
        )
        if not job:
            return {"status": "idle"}
        await conn.execute(
            """
            UPDATE chat_graph_extraction_jobs
            SET gate_status='running', attempt_count=attempt_count+1, started_at=NOW()
            WHERE id=$1
            """,
            job["id"],
        )
        await conn.execute(
            """
            INSERT INTO chat_graph_extraction_events(job_id,event_type,payload)
            VALUES($1,'started','{}'::jsonb)
            """,
            job["id"],
        )
    client = LLMClient(config.llm)
    try:
        parsed = await asyncio.wait_for(
            client.complete_json(SYSTEM, job["fact_text"], max_tokens=600, json_schema=SCHEMA),
            timeout=90,
        )
        triples = [
            item
            for item in parsed.get("triples", [])
            if item.get("subject")
            and item.get("object")
            and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", str(item.get("predicate", "")))
            and float(item.get("confidence", 0)) >= 0.80
        ]
        driver = AsyncGraphDatabase.driver(
            config.cognee.neo4j_uri,
            auth=(config.cognee.neo4j_user, config.cognee.neo4j_password),
        )
        try:
            async with driver.session() as session:
                await session.run(
                    """
                    CREATE CONSTRAINT llm_chat_entity_key IF NOT EXISTS
                    FOR (n:LLMChatEntity) REQUIRE (n.namespace,n.canonical_key) IS UNIQUE
                    """
                )
                for item in triples:
                    await session.run(
                        """
                        MERGE (s:LLMChatEntity {namespace:$ns,canonical_key:$sk})
                        ON CREATE SET s.name=$subject,s.created_at=$now
                        MERGE (o:LLMChatEntity {namespace:$ns,canonical_key:$ok})
                        ON CREATE SET o.name=$object,o.created_at=$now
                        MERGE (f:FactDataPoint {eb_id:$fact_id})
                        MERGE (s)-[r:LLM_CHAT_RELATION {
                            source_fact_id:$fact_id,predicate:$predicate
                        }]->(o)
                        SET r.namespace=$ns,r.confidence=$confidence,
                            r.extraction_model=$model,r.extracted_at=$now
                        MERGE (f)-[:LLM_EXTRACTED_FROM {namespace:$ns}]->(s)
                        MERGE (f)-[:LLM_EXTRACTED_FROM {namespace:$ns}]->(o)
                        """,
                        ns=NAMESPACE,
                        sk=key(item["subject"]),
                        ok=key(item["object"]),
                        subject=item["subject"],
                        object=item["object"],
                        fact_id=str(job["fact_id"]),
                        predicate=item["predicate"],
                        confidence=float(item["confidence"]),
                        model=config.llm.model,
                        now=datetime.now(UTC).isoformat(),
                    )
        finally:
            await driver.close()
        result = {
            "triples": len(triples),
            "raw_triples": len(parsed.get("triples", [])),
            "rejected_triples": len(parsed.get("triples", [])) - len(triples),
            "nodes": len({key(item["subject"]) for item in triples} | {key(item["object"]) for item in triples}),
            "edges": len(triples),
            "namespace": NAMESPACE,
        }
        await conn.execute(
            """
            UPDATE chat_graph_extraction_jobs
            SET gate_status='completed', completed_at=NOW(), cognee_run_id=$2
            WHERE id=$1
            """,
            job["id"],
            json.dumps(result),
        )
        await conn.execute(
            """
            INSERT INTO chat_graph_extraction_events(job_id,event_type,payload)
            VALUES($1,'completed',$2::jsonb)
            """,
            job["id"],
            json.dumps(result),
        )
        return {"status": "completed", "job_id": job["id"]} | result
    except Exception as exc:
        await conn.execute(
            "UPDATE chat_graph_extraction_jobs SET gate_status='failed',last_error=$2 WHERE id=$1",
            job["id"],
            str(exc)[:4000],
        )
        await conn.execute(
            """
            INSERT INTO chat_graph_extraction_events(job_id,event_type,payload)
            VALUES($1,'failed',$2::jsonb)
            """,
            job["id"],
            json.dumps({"error": str(exc)[:4000]}),
        )
        raise
    finally:
        await client.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-path", default="/etc/elephantbroker/default.yaml")
    parser.add_argument("--postgres-dsn", default=os.getenv("EB_POSTGRES_DSN", ""))
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    config = ElephantBrokerConfig.load(args.config_path)
    conn = await asyncpg.connect(args.postgres_dsn or config.postgres_dsn)
    try:
        print(
            json.dumps(
                await run_one(
                    conn,
                    config,
                    retry_failed=args.retry_failed,
                    max_attempts=max(1, args.max_attempts),
                ),
                ensure_ascii=False,
            )
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
