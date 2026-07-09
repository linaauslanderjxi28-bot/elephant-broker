"""Deterministic trade ontology relation builder.

KG-4 adds explicit, testable cross-border commerce relations to the graph
without relying on probabilistic LLM extraction. It reads structured JSON from
FactAssertion.text plus entity_type/entity_name and creates normalized trade
nodes/edges in Neo4j.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from elephantbroker.schemas.fact import FactAssertion


def _slug(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff ._-]+", "", value)
    return value.replace(" ", "-")[:160]


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _load_payload(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class TradeNode:
    label: str
    key: str
    name: str
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def trade_id(self) -> str:
        return f"{self.label}:{self.key}"


@dataclass
class TradeEdge:
    source: TradeNode
    rel_type: str
    target: TradeNode


@dataclass
class TradeRelationPlan:
    nodes: list[TradeNode] = field(default_factory=list)
    edges: list[TradeEdge] = field(default_factory=list)

    @property
    def node_count(self) -> int:
        return len({n.trade_id for n in self.nodes})

    @property
    def edge_count(self) -> int:
        return len({(e.source.trade_id, e.rel_type, e.target.trade_id) for e in self.edges})


def _product_node(name: str, category: str = "") -> TradeNode | None:
    if not name:
        return None
    key = _slug(name)
    return TradeNode("TradeProduct", key, name, {"category": category or ""})


def _supplier_node(name: str, platform: str = "", location: str = "") -> TradeNode | None:
    if not name:
        return None
    key = _slug(name)
    return TradeNode("Supplier", key, name, {"platform": platform or "", "location": location or ""})


def _hs_node(code: str) -> TradeNode | None:
    code = re.sub(r"\D", "", str(code or ""))
    if not code:
        return None
    return TradeNode("HSCode", code, code, {"code": code})


def _market_node(code: str) -> TradeNode | None:
    code = str(code or "").strip().upper()
    if not code:
        return None
    return TradeNode("Market", _slug(code), code, {"code": code})


def _cert_node(name: str) -> TradeNode | None:
    name = str(name or "").strip().upper()
    if not name:
        return None
    return TradeNode("Certification", _slug(name), name, {"name": name})


def build_trade_relation_plan(fact: FactAssertion) -> TradeRelationPlan:
    """Build deterministic trade ontology nodes/edges for a fact."""
    payload = _load_payload(fact.text)
    etype = (fact.entity_type or "").strip()
    ename = (fact.entity_name or "").strip()
    plan = TradeRelationPlan()

    def add_node(node: TradeNode | None) -> TradeNode | None:
        if node and node.trade_id not in {n.trade_id for n in plan.nodes}:
            plan.nodes.append(node)
        return node

    def add_edge(src: TradeNode | None, rel: str, dst: TradeNode | None) -> None:
        if src and dst:
            add_node(src); add_node(dst); plan.edges.append(TradeEdge(src, rel, dst))

    # Product fact: Product -> HSCode / Market / Certification
    if etype == "Product":
        product_name = str(payload.get("name") or ename).strip()
        product = add_node(_product_node(product_name, str(payload.get("category") or fact.category or "")))
        add_edge(product, "HAS_HS_CODE", _hs_node(str(payload.get("hs_code") or payload.get("hs") or "")))
        for market in _as_list(payload.get("market") or payload.get("markets") or payload.get("target_market") or payload.get("region")):
            add_edge(product, "SOLD_IN_MARKET", _market_node(market))
        for cert in _as_list(payload.get("certifications") or payload.get("certification") or payload.get("compliance")):
            add_edge(product, "REQUIRES_CERTIFICATION", _cert_node(cert))
        supplier_name = str(payload.get("supplier") or payload.get("supplier_name") or "").strip()
        add_edge(_supplier_node(supplier_name), "SUPPLIES", product)
        return plan

    # Supplier fact: Supplier -> Product
    if etype == "Supplier":
        supplier_name = str(payload.get("name") or ename).strip()
        supplier = add_node(_supplier_node(supplier_name, str(payload.get("platform") or ""), str(payload.get("location") or payload.get("country") or "")))
        for product_name in _as_list(payload.get("product") or payload.get("product_name") or payload.get("keyword") or payload.get("products")):
            add_edge(supplier, "SUPPLIES", _product_node(product_name))
        return plan

    # Market/customs/signal facts can still link to product when structured.
    product_name = str(payload.get("product") or payload.get("product_name") or payload.get("keyword") or "").strip()
    product = _product_node(product_name)
    if product:
        add_node(product)
        for market in _as_list(payload.get("market") or payload.get("country") or payload.get("region")):
            add_edge(product, "SOLD_IN_MARKET", _market_node(market))
        add_edge(product, "HAS_HS_CODE", _hs_node(str(payload.get("hs_code") or "")))

    return plan


async def apply_trade_relation_plan(graph, fact: FactAssertion) -> dict[str, int]:
    """Apply trade relation plan to Neo4j via GraphAdapter.query_cypher."""
    plan = build_trade_relation_plan(fact)
    if not plan.nodes and not plan.edges:
        return {"nodes": 0, "edges": 0}
    gateway_id = getattr(fact, "gateway_id", "") or ""

    for node in plan.nodes:
        label = node.label
        props = dict(node.properties)
        props.update({"trade_id": node.trade_id, "key": node.key, "name": node.name, "gateway_id": gateway_id})
        cypher = (
            f"MERGE (n:{label} {{trade_id: $trade_id, gateway_id: $gateway_id}}) "
            "SET n += $props"
        )
        await graph.query_cypher(cypher, {"trade_id": node.trade_id, "gateway_id": gateway_id, "props": props})

    for edge in plan.edges:
        cypher = (
            f"MATCH (a:{edge.source.label} {{trade_id: $source_id, gateway_id: $gateway_id}}), "
            f"(b:{edge.target.label} {{trade_id: $target_id, gateway_id: $gateway_id}}) "
            f"MERGE (a)-[r:{edge.rel_type}]->(b) "
            "SET r.gateway_id = $gateway_id"
        )
        await graph.query_cypher(cypher, {
            "source_id": edge.source.trade_id,
            "target_id": edge.target.trade_id,
            "gateway_id": gateway_id,
        })

    return {"nodes": plan.node_count, "edges": plan.edge_count}
