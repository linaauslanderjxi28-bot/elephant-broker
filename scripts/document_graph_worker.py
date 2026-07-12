#!/usr/bin/env python3
"""Automatic, fail-closed async document graph extraction worker.

Commands:
  scan  - classify PG documents and append eligible/rejected jobs.
  run   - process one queued eligible document through an isolated Cognee dataset.

Only authoritative, provenance-complete regulatory, exhibitor, customs, or
procurement documents can enter the execution queue. This never scans chat
memory and never edits source doc_chunks.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from typing import Any

import asyncpg

from elephantbroker.runtime.adapters.cognee.config import configure_cognee
from elephantbroker.runtime.document_graph_gate import classify_document
from elephantbroker.schemas.config import ElephantBrokerConfig


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def merged_metadata(rows: list[asyncpg.Record]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in rows:
        value = row["metadata"] or {}
        if isinstance(value, str):
            value = json.loads(value)
        for key, item in value.items():
            if item not in (None, "", [], {}):
                result.setdefault(key, item)
    return result


async def scan(conn: asyncpg.Connection, limit: int) -> dict[str, int]:
    doc_ids = await conn.fetch("SELECT doc_id FROM doc_chunks GROUP BY doc_id ORDER BY min(id) DESC LIMIT $1", limit)
    totals = {"documents": 0, "eligible": 0, "rejected": 0, "deduplicated": 0}
    for item in doc_ids:
        doc_id = item["doc_id"]
        rows = await conn.fetch(
            "SELECT content, metadata FROM doc_chunks WHERE doc_id=$1 ORDER BY chunk_index NULLS LAST, id", doc_id
        )
        text = "\n\n".join(str(row["content"] or "") for row in rows)
        metadata = merged_metadata(rows)
        decision = classify_document(doc_id, text, metadata)
        content_hash = digest(text)
        created = await conn.fetchval(
            """
            INSERT INTO document_graph_extraction_jobs
              (doc_id, content_hash, document_class, gate_status, gate_score, gate_reasons,
               source_url, source_type, authority_tier, chunk_count, char_count)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11)
            ON CONFLICT (doc_id, content_hash) DO NOTHING
            RETURNING id
            """,
            doc_id,
            content_hash,
            decision.document_class,
            decision.status,
            decision.score,
            json.dumps(decision.reasons),
            decision.source_url,
            decision.source_type,
            decision.authority_tier,
            len(rows),
            len(text),
        )
        if created is None:
            totals["deduplicated"] += 1
            continue
        event = "classified" if decision.status == "eligible" else "rejected"
        await conn.execute(
            "INSERT INTO document_graph_extraction_events (job_id,event_type,payload) VALUES ($1,$2,$3::jsonb)",
            created,
            event,
            json.dumps({"reasons": decision.reasons, "score": decision.score}),
        )
        totals["documents"] += 1
        totals["eligible" if decision.status == "eligible" else "rejected"] += 1
    return totals


async def run_one(conn: asyncpg.Connection, config_path: str | None) -> dict[str, Any]:
    async with conn.transaction():
        job = await conn.fetchrow(
            """
            SELECT * FROM document_graph_extraction_jobs
            WHERE gate_status='eligible'
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
            """
        )
        if job is None:
            return {"status": "idle"}
        dataset = f"trade-doc-{job['id']}-{job['content_hash'][:12]}"
        await conn.execute(
            """
            UPDATE document_graph_extraction_jobs
            SET gate_status='running', attempt_count=attempt_count+1,
                started_at=NOW(), cognee_dataset=$2
            WHERE id=$1
            """,
            job["id"],
            dataset,
        )
        await conn.execute(
            "INSERT INTO document_graph_extraction_events (job_id,event_type,payload) VALUES ($1,'started',$2::jsonb)",
            job["id"],
            json.dumps({"dataset": dataset}),
        )
    rows = await conn.fetch(
        "SELECT content FROM doc_chunks WHERE doc_id=$1 ORDER BY chunk_index NULLS LAST,id", job["doc_id"]
    )
    text = "\n\n".join(str(row["content"] or "") for row in rows)
    try:
        import cognee

        config = ElephantBrokerConfig.load(config_path)
        await configure_cognee(config.cognee, config.llm, gateway_id=config.gateway.gateway_id)
        await cognee.add(text, dataset_name=dataset)
        result = await cognee.cognify(datasets=[dataset], run_in_background=True)
        run_id = str(result)
        await conn.execute(
            """
            UPDATE document_graph_extraction_jobs
            SET gate_status='completed', completed_at=NOW(), cognee_run_id=$2
            WHERE id=$1
            """,
            job["id"],
            run_id,
        )
        await conn.execute(
            """
            INSERT INTO document_graph_extraction_events (job_id,event_type,payload)
            VALUES ($1,'completed',$2::jsonb)
            """,
            job["id"],
            json.dumps({"dataset": dataset, "cognee_result": run_id}),
        )
        return {"status": "completed", "job_id": job["id"], "dataset": dataset}
    except Exception as exc:
        await conn.execute(
            "UPDATE document_graph_extraction_jobs SET gate_status='failed',last_error=$2 WHERE id=$1",
            job["id"],
            str(exc)[:4000],
        )
        await conn.execute(
            "INSERT INTO document_graph_extraction_events (job_id,event_type,payload) VALUES ($1,'failed',$2::jsonb)",
            job["id"],
            json.dumps({"error": str(exc)[:4000]}),
        )
        raise


async def main_async(args: argparse.Namespace) -> int:
    conn = await asyncpg.connect(args.postgres_dsn)
    try:
        result = await (scan(conn, args.limit) if args.command == "scan" else run_one(conn, args.config_path))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("scan", "run"))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--postgres-dsn", default=os.getenv("EB_POSTGRES_DSN", ""))
    parser.add_argument("--config-path", default=None)
    args = parser.parse_args()
    if not args.postgres_dsn:
        parser.error("EB_POSTGRES_DSN is required")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
