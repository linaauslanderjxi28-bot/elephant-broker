#!/usr/bin/env python3
"""KG-4 smoke: verify deterministic trade ontology graph relations."""
from __future__ import annotations

import argparse, json, os, subprocess, time, urllib.error, urllib.request, uuid


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


def cypher_count(query: str, container: str, user: str, password: str) -> int:
    out = subprocess.check_output(["docker", "exec", container, "cypher-shell", "-u", user, "-p", password, query], text=True, timeout=60)
    for ln in reversed([x.strip() for x in out.splitlines() if x.strip()]):
        try:
            return int(ln)
        except ValueError:
            pass
    raise RuntimeError(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.getenv("EB_BASE_URL", "http://localhost:8420"))
    ap.add_argument("--auth-token", default=os.getenv("EB_AUTH_TOKEN"))
    ap.add_argument("--neo4j-container", default=os.getenv("EB_NEO4J_CONTAINER", "elephantbroker-neo4j-1"))
    ap.add_argument("--neo4j-user", default=os.getenv("EB_NEO4J_USER", "neo4j"))
    ap.add_argument("--neo4j-password", default=os.getenv("EB_NEO4J_PASSWORD", "elephant_dev"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    marker = f"kg4-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    product_name = f"portable fan {marker}"
    supplier_name = f"Shenzhen Cooling Tech {marker}"
    payload = {
        "name": product_name,
        "category": "small_appliance",
        "hs_code": "841451",
        "market": "US",
        "certifications": ["FCC", "UL"],
        "supplier": supplier_name,
    }
    fact = {
        "id": str(uuid.uuid4()),
        "text": json.dumps(payload, ensure_ascii=False),
        "category": "trade",
        "scope": "session",
        "confidence": 1.0,
        "memory_class": "semantic",
        "session_key": "kg4-smoke",
        "provenance_refs": ["kg4-smoke"],
        "entity_type": "Product",
        "entity_name": product_name,
        "decision_domain": "cross-border-trade",
        "decision_status": "proposed",
    }
    stored = post_json(args.base_url, "/memory/store", {"fact": fact, "dedup_threshold": 1.1}, args.auth_token)
    time.sleep(3)

    product_tid = "TradeProduct:" + product_name.lower().replace(" ", "-")
    supplier_tid = "Supplier:" + supplier_name.lower().replace(" ", "-")
    checks = {
        "has_hs_code": f"MATCH (:TradeProduct {{trade_id:'{product_tid}'}})-[r:HAS_HS_CODE]->(:HSCode {{trade_id:'HSCode:841451'}}) RETURN count(r) AS c;",
        "sold_in_market": f"MATCH (:TradeProduct {{trade_id:'{product_tid}'}})-[r:SOLD_IN_MARKET]->(:Market {{trade_id:'Market:us'}}) RETURN count(r) AS c;",
        "requires_certification": f"MATCH (:TradeProduct {{trade_id:'{product_tid}'}})-[r:REQUIRES_CERTIFICATION]->(:Certification) RETURN count(r) AS c;",
        "supplies": f"MATCH (:Supplier {{trade_id:'{supplier_tid}'}})-[r:SUPPLIES]->(:TradeProduct {{trade_id:'{product_tid}'}}) RETURN count(r) AS c;",
    }
    counts = {k: cypher_count(q, args.neo4j_container, args.neo4j_user, args.neo4j_password) for k, q in checks.items()}
    ok = counts["has_hs_code"] >= 1 and counts["sold_in_market"] >= 1 and counts["requires_certification"] >= 2 and counts["supplies"] >= 1
    result = {"ok": ok, "marker": marker, "fact_id": stored.get("id"), "product_trade_id": product_tid, "supplier_trade_id": supplier_tid, "counts": counts}
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
