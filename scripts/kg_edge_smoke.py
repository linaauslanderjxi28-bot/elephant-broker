#!/usr/bin/env python3
"""KG-3 smoke test: verify deterministic graph edge creation.

This test exercises the production path:
1. POST /actors/ registers an actor (PG + KG ActorDataPoint dual-write).
2. POST /memory/store stores a FactDataPoint with source_actor_id.
3. Neo4j must contain FactDataPoint-[:CREATED_BY]->ActorDataPoint.

It intentionally avoids relying on probabilistic LLM relation extraction; this
is the stable edge contract EB itself guarantees for typed relationships.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid


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


def cypher_count(query: str, neo4j_container: str, user: str, password: str) -> int:
    out = subprocess.check_output([
        "docker", "exec", neo4j_container, "cypher-shell", "-u", user, "-p", password, query,
    ], text=True, timeout=60)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    # cypher-shell table-free output is usually: header, value
    for ln in reversed(lines):
        try:
            return int(ln)
        except ValueError:
            continue
    raise RuntimeError(f"Could not parse cypher count from output: {out!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="EB KG edge smoke test")
    ap.add_argument("--base-url", default=os.getenv("EB_BASE_URL", "http://localhost:8420"))
    ap.add_argument("--auth-token", default=os.getenv("EB_AUTH_TOKEN"))
    ap.add_argument("--neo4j-container", default=os.getenv("EB_NEO4J_CONTAINER", "elephantbroker-neo4j-1"))
    ap.add_argument("--neo4j-user", default=os.getenv("EB_NEO4J_USER", "neo4j"))
    ap.add_argument("--neo4j-password", default=os.getenv("EB_NEO4J_PASSWORD", "elephant_dev"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    actor_id = str(uuid.uuid4())
    fact_id = str(uuid.uuid4())
    marker = f"eb_kg3_edge_smoke_{int(time.time())}_{fact_id[:8]}"

    actor = {
        "id": actor_id,
        "type": "worker_agent",
        "display_name": f"KG3 Edge Smoke Actor {actor_id[:8]}",
        "authority_level": 10,
        "handles": [],
        "team_ids": [],
        "trust_level": 0.5,
        "tags": ["kg3-smoke"],
    }
    actor_result = post_json(args.base_url, "/actors/", actor, args.auth_token)

    fact = {
        "id": fact_id,
        "text": f"KG-3 edge smoke marker {marker}. Actor creates this fact for CREATED_BY edge verification.",
        "category": "system",
        "scope": "session",
        "confidence": 1.0,
        "memory_class": "semantic",
        "session_key": "kg3-smoke",
        "source_actor_id": actor_id,
        "target_actor_ids": [],
        "goal_ids": [],
        "provenance_refs": ["kg3-smoke"],
        "entity_type": "KGSmokeTest",
        "entity_name": "KGSmokeTest:KG3Edge",
        "decision_domain": "backend-health-check",
        "decision_status": "proposed",
    }
    fact_result = post_json(args.base_url, "/memory/store", {"fact": fact, "dedup_threshold": 1.1}, args.auth_token)

    time.sleep(3)
    node_query = (
        f"MATCH (f:FactDataPoint {{eb_id: '{fact_id}'}}), "
        f"(a:ActorDataPoint {{eb_id: '{actor_id}'}}) RETURN count(f) + count(a) AS nodes;"
    )
    edge_query = (
        f"MATCH (:FactDataPoint {{eb_id: '{fact_id}'}})-[r:CREATED_BY]->"
        f"(:ActorDataPoint {{eb_id: '{actor_id}'}}) RETURN count(r) AS edges;"
    )
    node_count_sum = cypher_count(node_query, args.neo4j_container, args.neo4j_user, args.neo4j_password)
    edge_count = cypher_count(edge_query, args.neo4j_container, args.neo4j_user, args.neo4j_password)
    ok = node_count_sum >= 2 and edge_count >= 1
    result = {
        "ok": ok,
        "marker": marker,
        "actor_id": actor_id,
        "fact_id": fact_id,
        "actor_gateway_id": actor_result.get("gateway_id"),
        "fact_gateway_id": fact_result.get("gateway_id"),
        "node_count_sum": node_count_sum,
        "created_by_edges": edge_count,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
