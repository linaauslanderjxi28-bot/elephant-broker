#!/usr/bin/env python3
"""KG-5 smoke: write trade fact then query it through TradeGraphQuery."""
from __future__ import annotations

import argparse, asyncio, json, os, time, urllib.error, urllib.request, uuid

from elephantbroker.runtime.adapters.cognee.graph import GraphAdapter
from elephantbroker.runtime.trade_graph import TradeGraphQuery
from elephantbroker.schemas.config import CogneeConfig


def post_json(base: str, path: str, payload: dict, token: str | None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {path} failed: HTTP {exc.code}: {body}") from exc


async def query_profile(product_name: str, supplier_name: str, gateway_id: str, neo4j_uri: str, neo4j_user: str, neo4j_password: str) -> dict:
    graph = GraphAdapter(CogneeConfig(neo4j_uri=neo4j_uri, neo4j_user=neo4j_user, neo4j_password=neo4j_password))
    try:
        q = TradeGraphQuery(graph, gateway_id=gateway_id)
        return {
            "profile": await q.get_product_profile(product_name),
            "supplier_products": await q.get_supplier_products(supplier_name),
            "hs_products": await q.get_hs_code_products("841451"),
            "market_requirements": await q.get_market_requirements("US"),
        }
    finally:
        await graph.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.getenv("EB_BASE_URL", "http://localhost:8420"))
    ap.add_argument("--auth-token", default=os.getenv("EB_AUTH_TOKEN"))
    ap.add_argument("--gateway-id", default=os.getenv("EB_GATEWAY_ID", "gw-enterprise-prod"))
    ap.add_argument("--neo4j-uri", default=os.getenv("EB_NEO4J_URI", "bolt://localhost:7687"))
    ap.add_argument("--neo4j-user", default=os.getenv("EB_NEO4J_USER", "neo4j"))
    ap.add_argument("--neo4j-password", default=os.getenv("EB_NEO4J_PASSWORD", "elephant_dev"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    marker = f"kg5-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    product_name = f"portable fan {marker}"
    supplier_name = f"Shenzhen Cooling Tech {marker}"
    payload = {"name": product_name, "category": "small_appliance", "hs_code": "841451", "market": "US", "certifications": ["FCC", "UL"], "supplier": supplier_name}
    fact = {
        "id": str(uuid.uuid4()), "text": json.dumps(payload, ensure_ascii=False),
        "category": "trade", "scope": "session", "confidence": 1.0,
        "memory_class": "semantic", "session_key": "kg5-smoke",
        "provenance_refs": ["kg5-smoke"], "entity_type": "Product",
        "entity_name": product_name, "decision_domain": "cross-border-trade",
        "decision_status": "proposed",
    }
    stored = post_json(args.base_url, "/memory/store", {"fact": fact, "dedup_threshold": 1.1}, args.auth_token)
    time.sleep(3)
    result = asyncio.run(query_profile(product_name, supplier_name, args.gateway_id, args.neo4j_uri, args.neo4j_user, args.neo4j_password))
    profile = result["profile"]
    ok = (
        profile["product"] and profile["product"].get("name") == product_name
        and "841451" in profile["hs_codes"]
        and "US" in profile["markets"]
        and {"FCC", "UL"}.issubset(set(profile["certifications"]))
        and supplier_name in profile["suppliers"]
        and product_name in result["supplier_products"]
        and product_name in result["hs_products"]
        and any(r["product"] == product_name for r in result["market_requirements"])
    )
    out = {"ok": ok, "marker": marker, "fact_id": stored.get("id"), "product_name": product_name, "supplier_name": supplier_name, "query_result": result}
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
