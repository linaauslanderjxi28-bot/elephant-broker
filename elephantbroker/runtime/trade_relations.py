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
    return TradeNode("Certification", _slug(name), name, {"name": name, "code": name})


def _named_node(label: str, name: str, properties: dict[str, Any] | None = None) -> TradeNode | None:
    name = str(name or "").strip()
    if not name:
        return None
    return TradeNode(label, _slug(name), name, properties or {})


def _exporter_company_node(name: str) -> TradeNode | None:
    return _named_node("ExporterCompany", name)


def _exporter_demand_node(name: str, demand_type: str = "") -> TradeNode | None:
    return _named_node("ExporterDemand", name, {"demand_type": demand_type or ""})


def _exhibitor_company_node(name: str) -> TradeNode | None:
    return _named_node("ExhibitorCompany", name)


def _expo_edition_node(expo_id: str, edition: str, expo_name: str) -> TradeNode | None:
    name = " ".join(x for x in (expo_name, edition) if x).strip()
    key = _slug(":".join(x for x in (expo_id, edition) if x))
    return TradeNode("ExpoEdition", key, name, {"expo_id": expo_id, "edition": edition}) if key and name else None


def _country_node(name: str) -> TradeNode | None:
    return _named_node("Country", name)


def _prediction_node(name: str, run_id: str = "") -> TradeNode | None:
    key = _slug(run_id or name)
    return TradeNode("HotProductPrediction", key, name, {"run_id": run_id or ""}) if key and name else None


def _signal_node(name: str) -> TradeNode | None:
    return _named_node("MarketSignal", name)


def _skill_node(name: str) -> TradeNode | None:
    return _named_node("TradeSkill", name)


def _object_type_node(name: str) -> TradeNode | None:
    return _named_node("ObjectType", name)


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
            add_node(src)
            add_node(dst)
            plan.edges.append(TradeEdge(src, rel, dst))

    # Product fact: Product -> HSCode / Market / Certification
    if etype == "Product":
        product_name = str(payload.get("name") or ename).strip()
        product = add_node(_product_node(product_name, str(payload.get("category") or fact.category or "")))
        add_edge(product, "HAS_HS_CODE", _hs_node(str(payload.get("hs_code") or payload.get("hs") or "")))
        for market in _as_list(
            payload.get("market") or payload.get("markets") or payload.get("target_market") or payload.get("region")
        ):
            add_edge(product, "SOLD_IN_MARKET", _market_node(market))
        for cert in _as_list(
            payload.get("certifications") or payload.get("certification") or payload.get("compliance")
        ):
            add_edge(product, "REQUIRES_CERTIFICATION", _cert_node(cert))
        supplier_name = str(payload.get("supplier") or payload.get("supplier_name") or "").strip()
        add_edge(_supplier_node(supplier_name), "SUPPLIES", product)
        return plan

    # Supplier fact: Supplier -> Product
    if etype == "Supplier":
        supplier_name = str(payload.get("name") or ename).strip()
        supplier = add_node(
            _supplier_node(
                supplier_name,
                str(payload.get("platform") or ""),
                str(payload.get("location") or payload.get("country") or ""),
            )
        )
        for product_name in _as_list(
            payload.get("product") or payload.get("product_name") or payload.get("keyword") or payload.get("products")
        ):
            add_edge(supplier, "SUPPLIES", _product_node(product_name))
        return plan

    # P0: tariff facts retain route-specific applicability rather than collapsing rates into HS nodes.
    if etype == "TariffRule":
        rule = _named_node(
            "TariffRule",
            ename
            or " ".join(
                filter(
                    None,
                    [
                        str(payload.get("hs_code") or ""),
                        str(payload.get("origin_country") or ""),
                        str(payload.get("destination_country") or ""),
                    ],
                )
            ),
            {
                "total_rate": payload.get("total_rate"),
                "mfn_rate": payload.get("mfn_rate"),
                "source": str(payload.get("source") or ""),
            },
        )
        add_edge(rule, "APPLIES_TO_HS_CODE", _hs_node(str(payload.get("hs_code") or "")))
        add_edge(rule, "ORIGINATES_IN", _country_node(str(payload.get("origin_country") or "")))
        add_edge(rule, "DESTINED_FOR", _market_node(str(payload.get("destination_country") or "")))
        return plan

    # P1: exporter needs are evidence-backed objects, not inferred buyer leads.
    if etype == "ExporterDemand":
        company = _exporter_company_node(str(payload.get("company_name") or payload.get("exporter_company") or ""))
        demand = _exporter_demand_node(
            ename or str(payload.get("demand_id") or ""), str(payload.get("demand_type") or "")
        )
        add_edge(company, "HAS_DEMAND", demand)
        for product_name in _as_list(payload.get("product") or payload.get("products")):
            add_edge(demand, "CONCERNS_PRODUCT", _product_node(product_name))
        for market in _as_list(payload.get("target_markets") or payload.get("target_market") or payload.get("markets")):
            add_edge(demand, "TARGETS_MARKET", _market_node(market))
        return plan

    # P1: official exhibition listing proves participation only; it never proves trade.
    if etype == "ExpoExhibitor":
        exhibitor = _exhibitor_company_node(str(payload.get("company_name") or ename))
        edition = _expo_edition_node(
            str(payload.get("expo_id") or ""),
            str(payload.get("edition") or ""),
            str(payload.get("expo_name") or payload.get("expo_id") or ""),
        )
        add_edge(exhibitor, "EXHIBITED_AT", edition)
        add_edge(exhibitor, "LOCATED_IN", _country_node(str(payload.get("country") or "")))
        return plan

    # P2: retain prediction evidence, rather than promoting a score to a fact.
    if etype == "HotProductPrediction":
        prediction = _prediction_node(ename or str(payload.get("keyword") or ""), str(payload.get("run_id") or ""))
        product = _product_node(str(payload.get("keyword") or payload.get("product") or ""))
        add_edge(prediction, "PREDICTS", product)
        add_edge(prediction, "FOR_MARKET", _market_node(str(payload.get("market") or "")))
        add_edge(prediction, "HAS_HS_CODE", _hs_node(str(payload.get("hs_code") or "")))
        for signal_id in _as_list(payload.get("signal_ids") or payload.get("signals")):
            add_edge(prediction, "SUPPORTED_BY", _signal_node(signal_id))
        return plan

    # P2: skill capability relations are operational metadata for the composer.
    if etype == "SkillIndex":
        skill = _skill_node(str(payload.get("name") or ename))
        for output in _as_list(payload.get("produces")):
            add_edge(skill, "PRODUCES", _object_type_node(output))
        for input_name in _as_list(payload.get("consumes")):
            add_edge(skill, "CONSUMES", _object_type_node(input_name))
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
    """Apply a plan using existing Neo4j master-data unique keys when defined."""
    plan = build_trade_relation_plan(fact)
    if not plan.nodes and not plan.edges:
        return {"nodes": 0, "edges": 0}
    gateway_id = getattr(fact, "gateway_id", "") or ""
    master_data_keys = {
        "TradeProduct": "name",
        "HSCode": "code",
        "Market": "code",
        "Certification": "code",
    }

    def node_identity(node: TradeNode) -> tuple[str, str]:
        field = master_data_keys.get(node.label, "trade_id")
        return field, str(node.properties.get(field) or (node.name if field == "name" else node.trade_id))

    for node in plan.nodes:
        props = dict(node.properties)
        props.update({"trade_id": node.trade_id, "key": node.key, "name": node.name})
        match_field, match_value = node_identity(node)
        cypher = f"MERGE (n:{node.label} {{{match_field}: $match_value}}) SET n += $props"
        await graph.query_cypher(cypher, {"match_value": match_value, "props": props})

    for edge in plan.edges:
        source_field, source_value = node_identity(edge.source)
        target_field, target_value = node_identity(edge.target)
        cypher = (
            f"MATCH (a:{edge.source.label} {{{source_field}: $source_value}}), "
            f"(b:{edge.target.label} {{{target_field}: $target_value}}) "
            f"MERGE (a)-[r:{edge.rel_type}]->(b) "
            "SET r.gateway_id = $gateway_id"
        )
        await graph.query_cypher(
            cypher,
            {
                "source_value": source_value,
                "target_value": target_value,
                "gateway_id": gateway_id,
            },
        )

    return {"nodes": plan.node_count, "edges": plan.edge_count}
