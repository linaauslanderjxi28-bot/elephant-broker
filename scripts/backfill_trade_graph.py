#!/usr/bin/env python3
"""Backfill deterministic P0-P2 trade ontology edges from PostgreSQL evidence.

Read-only against PostgreSQL; writes idempotent MERGE nodes/edges to Neo4j.
Does not infer buyer intent or trade relationships beyond source fields.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

import asyncpg
from neo4j import AsyncGraphDatabase

from elephantbroker.runtime.trade_relations import apply_trade_relation_plan
from elephantbroker.schemas.fact import FactAssertion, MemoryClass


class Neo4jGraph:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def query_cypher(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        async with self._driver.session() as session:
            result = await session.run(cypher, **params)
            return await result.data()

    async def close(self) -> None:
        await self._driver.close()


def fact(
    entity_type: str, entity_name: str, payload: dict[str, Any], provenance: str, gateway_id: str
) -> FactAssertion:
    return FactAssertion(
        text=json.dumps(payload, default=str, ensure_ascii=False),
        category="trade_backfill",
        memory_class=MemoryClass.SEMANTIC,
        entity_type=entity_type,
        entity_name=entity_name,
        gateway_id=gateway_id,
        provenance_refs=[provenance],
        autorecall_blacklisted=True,
    )


async def rows(conn: asyncpg.Connection, table: str, limit: int) -> list[asyncpg.Record]:
    return await conn.fetch(f"SELECT * FROM {table} ORDER BY 1 LIMIT $1", limit)


async def build_facts(conn: asyncpg.Connection, limit: int, gateway_id: str) -> list[FactAssertion]:
    output: list[FactAssertion] = []
    for row in await rows(conn, "supplier_quotes", limit):
        d = dict(row)
        name = str(d.get("supplier_name") or "")
        product = str(d.get("product_name") or d.get("keyword") or "")
        if name and product:
            output.append(
                fact(
                    "Supplier",
                    name,
                    {
                        "name": name,
                        "platform": d.get("platform") or "",
                        "location": d.get("origin_country") or "",
                        "product": product,
                    },
                    f"pg:supplier_quotes:{d['id']}",
                    gateway_id,
                )
            )
    for row in await rows(conn, "compliance_requirements", limit):
        d = dict(row)
        product = str(d.get("product_name") or d.get("product_category") or "")
        if product:
            output.append(
                fact(
                    "Product",
                    product,
                    {
                        "name": product,
                        "hs_code": d.get("hs_code") or "",
                        "market": d.get("market") or d.get("country") or "",
                        "certification": d.get("certification") or "",
                    },
                    f"pg:compliance_requirements:{d['id']}",
                    gateway_id,
                )
            )
    for row in await rows(conn, "tariff_cache", limit):
        d = dict(row)
        if d.get("hs_code") and d.get("origin_country") and d.get("destination_country"):
            name = f"{d['hs_code']} {d['origin_country']}-{d['destination_country']}"
            output.append(fact("TariffRule", name, d, f"pg:tariff_cache:{d['id']}", gateway_id))
    for row in await rows(conn, "exporter_demands", limit):
        d = dict(row)
        company = await conn.fetchval(
            "SELECT company_name FROM exporter_companies WHERE company_id=$1", d["company_id"]
        )
        if company:
            output.append(
                fact(
                    "ExporterDemand",
                    str(d["demand_id"]),
                    {
                        "company_name": company,
                        "demand_type": d.get("demand_type") or "",
                        "product": d.get("product") or "",
                        "target_markets": d.get("target_markets") or [],
                    },
                    f"pg:exporter_demands:{d['demand_id']}",
                    gateway_id,
                )
            )
    for row in await rows(conn, "expo_exhibitors", limit):
        d = dict(row)
        output.append(
            fact(
                "ExpoExhibitor",
                d["company_name"],
                {
                    "company_name": d["company_name"],
                    "expo_id": d["expo_id"],
                    "edition": d["edition"],
                    "expo_name": d["expo_name"],
                    "country": d.get("country") or "",
                },
                f"pg:expo_exhibitors:{d['exhibitor_id']}",
                gateway_id,
            )
        )
    for row in await rows(conn, "hot_product_predictions", limit):
        d = dict(row)
        signals = await conn.fetch("SELECT id FROM hot_product_signals WHERE prediction_id=$1", d["id"])
        output.append(
            fact(
                "HotProductPrediction",
                f"{d['keyword']} {d['market']}",
                {
                    "run_id": d["run_id"],
                    "keyword": d["keyword"],
                    "market": d["market"],
                    "hs_code": d.get("hs_code") or "",
                    "signal_ids": [str(x["id"]) for x in signals],
                },
                f"pg:hot_product_predictions:{d['id']}",
                gateway_id,
            )
        )
    for row in await rows(conn, "skill_graph", limit):
        d = dict(row)
        output.append(
            fact(
                "SkillIndex",
                d["skill_name"],
                {"name": d["skill_name"], "produces": d.get("produces") or [], "consumes": d.get("consumes") or []},
                f"pg:skill_graph:{d['skill_name']}",
                gateway_id,
            )
        )
    return output


async def main_async(args: argparse.Namespace) -> int:
    conn = await asyncpg.connect(args.postgres_dsn)
    graph = Neo4jGraph(args.neo4j_uri, args.neo4j_user, args.neo4j_password)
    try:
        facts = await build_facts(conn, args.limit, args.gateway_id)
        totals = {"facts": len(facts), "nodes": 0, "edges": 0}
        for item in facts:
            counts = await apply_trade_relation_plan(graph, item)
            totals["nodes"] += counts["nodes"]
            totals["edges"] += counts["edges"]
        print(json.dumps(totals, ensure_ascii=False))
    finally:
        await graph.close()
        await conn.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1000)
    p.add_argument("--postgres-dsn", default=os.environ.get("EB_POSTGRES_DSN", ""))
    p.add_argument("--neo4j-uri", default=os.environ.get("EB_NEO4J_URI", ""))
    p.add_argument("--neo4j-user", default=os.environ.get("EB_NEO4J_USER", "neo4j"))
    p.add_argument("--neo4j-password", default=os.environ.get("EB_NEO4J_PASSWORD", ""))
    p.add_argument("--gateway-id", default=os.environ.get("EB_GATEWAY_ID", ""))
    args = p.parse_args()
    if not all((args.postgres_dsn, args.neo4j_uri, args.neo4j_password, args.gateway_id)):
        p.error("EB_POSTGRES_DSN, EB_NEO4J_URI, EB_NEO4J_PASSWORD and EB_GATEWAY_ID are required")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
