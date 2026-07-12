"""Trade graph query layer (KG-5).

Reads the deterministic trade ontology created by KG-4:
TradeProduct / Supplier / HSCode / Market / Certification.
"""

from __future__ import annotations

import re
from typing import Any


def _slug(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff ._-]+", "", value)
    return value.replace(" ", "-")[:160]


def _hs_key(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


class TradeGraphQuery:
    def __init__(self, graph, gateway_id: str = "") -> None:
        self._graph = graph
        self._gateway_id = gateway_id

    async def get_product_profile(self, product_name: str) -> dict[str, Any]:
        product_id = f"TradeProduct:{_slug(product_name)}"
        cypher = """
        MATCH (p:TradeProduct {trade_id: $product_id, gateway_id: $gateway_id})
        OPTIONAL MATCH (p)-[:HAS_HS_CODE]->(h:HSCode)
        OPTIONAL MATCH (p)-[:SOLD_IN_MARKET]->(m:Market)
        OPTIONAL MATCH (p)-[:REQUIRES_CERTIFICATION]->(c:Certification)
        OPTIONAL MATCH (s:Supplier)-[:SUPPLIES]->(p)
        RETURN properties(p) AS product,
               collect(DISTINCT h.code) AS hs_codes,
               collect(DISTINCT m.code) AS markets,
               collect(DISTINCT c.name) AS certifications,
               collect(DISTINCT s.name) AS suppliers
        """
        rows = await self._graph.query_cypher(cypher, {"product_id": product_id, "gateway_id": self._gateway_id})
        if not rows:
            return {"product": None, "hs_codes": [], "markets": [], "certifications": [], "suppliers": []}
        row = rows[0]
        return {
            "product": row.get("product"),
            "hs_codes": [x for x in row.get("hs_codes", []) if x],
            "markets": [x for x in row.get("markets", []) if x],
            "certifications": [x for x in row.get("certifications", []) if x],
            "suppliers": [x for x in row.get("suppliers", []) if x],
        }

    async def get_supplier_products(self, supplier_name: str) -> list[str]:
        supplier_id = f"Supplier:{_slug(supplier_name)}"
        cypher = """
        MATCH (:Supplier {trade_id: $supplier_id, gateway_id: $gateway_id})-[:SUPPLIES]->(p:TradeProduct)
        RETURN collect(DISTINCT p.name) AS products
        """
        rows = await self._graph.query_cypher(cypher, {"supplier_id": supplier_id, "gateway_id": self._gateway_id})
        return [x for x in (rows[0].get("products", []) if rows else []) if x]

    async def get_hs_code_products(self, hs_code: str) -> list[str]:
        hs_id = f"HSCode:{_hs_key(hs_code)}"
        cypher = """
        MATCH (p:TradeProduct)-[:HAS_HS_CODE]->(:HSCode {trade_id: $hs_id, gateway_id: $gateway_id})
        RETURN collect(DISTINCT p.name) AS products
        """
        rows = await self._graph.query_cypher(cypher, {"hs_id": hs_id, "gateway_id": self._gateway_id})
        return [x for x in (rows[0].get("products", []) if rows else []) if x]

    async def get_market_requirements(self, market: str) -> list[dict[str, Any]]:
        market_id = f"Market:{_slug(market)}"
        cypher = """
        MATCH (p:TradeProduct)-[:SOLD_IN_MARKET]->(:Market {trade_id: $market_id, gateway_id: $gateway_id})
        OPTIONAL MATCH (p)-[:REQUIRES_CERTIFICATION]->(c:Certification)
        RETURN p.name AS product, collect(DISTINCT c.name) AS certifications
        """
        rows = await self._graph.query_cypher(cypher, {"market_id": market_id, "gateway_id": self._gateway_id})
        return [
            {"product": r.get("product"), "certifications": [x for x in r.get("certifications", []) if x]} for r in rows
        ]

    async def get_exporter_demand_profile(self, demand_name: str) -> dict[str, Any]:
        demand_id = f"ExporterDemand:{_slug(demand_name)}"
        cypher = """
        MATCH (c:ExporterCompany {gateway_id: $gateway_id})
        -[:HAS_DEMAND]->(d:ExporterDemand {trade_id: $demand_id, gateway_id: $gateway_id})
        OPTIONAL MATCH (d)-[:CONCERNS_PRODUCT]->(p:TradeProduct)
        OPTIONAL MATCH (d)-[:TARGETS_MARKET]->(m:Market)
        RETURN c.name AS company, d.name AS demand, d.demand_type AS demand_type,
               collect(DISTINCT p.name) AS products, collect(DISTINCT m.code) AS markets
        """
        rows = await self._graph.query_cypher(cypher, {"demand_id": demand_id, "gateway_id": self._gateway_id})
        if not rows:
            return {"company": None, "demand": None, "demand_type": None, "products": [], "markets": []}
        row = rows[0]
        return {
            "company": row.get("company"),
            "demand": row.get("demand"),
            "demand_type": row.get("demand_type"),
            "products": [x for x in row.get("products", []) if x],
            "markets": [x for x in row.get("markets", []) if x],
        }

    async def get_prediction_evidence(self, run_id: str) -> dict[str, Any]:
        prediction_id = f"HotProductPrediction:{_slug(run_id)}"
        cypher = """
        MATCH (p:HotProductPrediction {trade_id: $prediction_id, gateway_id: $gateway_id})
        OPTIONAL MATCH (p)-[:PREDICTS]->(product:TradeProduct)
        OPTIONAL MATCH (p)-[:FOR_MARKET]->(market:Market)
        OPTIONAL MATCH (p)-[:SUPPORTED_BY]->(signal:MarketSignal)
        RETURN p.name AS prediction, product.name AS product, market.code AS market,
               collect(DISTINCT signal.name) AS signals
        """
        rows = await self._graph.query_cypher(cypher, {"prediction_id": prediction_id, "gateway_id": self._gateway_id})
        if not rows:
            return {"prediction": None, "product": None, "market": None, "signals": []}
        row = rows[0]
        return {
            "prediction": row.get("prediction"),
            "product": row.get("product"),
            "market": row.get("market"),
            "signals": [x for x in row.get("signals", []) if x],
        }
