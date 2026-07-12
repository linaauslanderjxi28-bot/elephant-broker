"""Append high-value trade chat facts to the asynchronous Cognee graph-extraction queue."""

from __future__ import annotations

import hashlib
import json

import asyncpg

from elephantbroker.runtime.chat_graph_gate import classify_trade_chat
from elephantbroker.schemas.fact import FactAssertion


async def enqueue_trade_chat_fact(fact: FactAssertion, postgres_dsn: str) -> bool:
    """Queue only eligible facts; callers treat errors as non-fatal to fact storage."""
    if not postgres_dsn:
        return False
    decision = classify_trade_chat(
        text=fact.text,
        confidence=fact.confidence,
        decision_domain=fact.decision_domain,
    )
    if decision.status != "eligible":
        return False
    content_hash = hashlib.sha256(fact.text.encode("utf-8")).hexdigest()
    conn = await asyncpg.connect(postgres_dsn)
    try:
        job_id = await conn.fetchval(
            """
            INSERT INTO chat_graph_extraction_jobs
              (fact_id, content_hash, fact_text, session_key, gateway_id, confidence,
               decision_domain, gate_status, gate_score, gate_reasons)
            VALUES ($1,$2,$3,$4,$5,$6,$7,'eligible',$8,$9::jsonb)
            ON CONFLICT (fact_id, content_hash) DO NOTHING
            RETURNING id
            """,
            fact.id,
            content_hash,
            fact.text,
            fact.session_key,
            fact.gateway_id,
            fact.confidence,
            fact.decision_domain,
            decision.score,
            json.dumps(decision.reasons),
        )
        if job_id is None:
            return False
        await conn.execute(
            """
            INSERT INTO chat_graph_extraction_events (job_id,event_type,payload)
            VALUES ($1,'queued',$2::jsonb)
            """,
            job_id,
            json.dumps({"score": decision.score, "reasons": decision.reasons}),
        )
        return True
    finally:
        await conn.close()
