"""Tests for deterministic trade relation builder (KG-4)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

from elephantbroker.runtime.trade_relations import build_trade_relation_plan, apply_trade_relation_plan
from elephantbroker.schemas.fact import FactAssertion, MemoryClass


def _fact(payload: dict, entity_type: str, entity_name: str) -> FactAssertion:
    return FactAssertion(
        text=json.dumps(payload, ensure_ascii=False),
        category="trade",
        memory_class=MemoryClass.SEMANTIC,
        entity_type=entity_type,
        entity_name=entity_name,
        gateway_id="gw-test",
    )


class TestTradeRelationBuilder:
    def test_product_builds_trade_edges(self):
        fact = _fact({
            "name": "portable fan",
            "category": "small_appliance",
            "hs_code": "841451",
            "market": "US",
            "certifications": ["FCC", "UL"],
            "supplier": "Shenzhen Cooling Tech Co., Ltd.",
        }, "Product", "portable fan")

        plan = build_trade_relation_plan(fact)
        rels = {(e.source.label, e.rel_type, e.target.label, e.target.name) for e in plan.edges}

        assert plan.node_count == 6
        assert ("TradeProduct", "HAS_HS_CODE", "HSCode", "841451") in rels
        assert ("TradeProduct", "SOLD_IN_MARKET", "Market", "US") in rels
        assert ("TradeProduct", "REQUIRES_CERTIFICATION", "Certification", "FCC") in rels
        assert ("TradeProduct", "REQUIRES_CERTIFICATION", "Certification", "UL") in rels
        assert ("Supplier", "SUPPLIES", "TradeProduct", "portable fan") in rels

    def test_supplier_builds_supplies_edge(self):
        fact = _fact({
            "name": "Shenzhen Cooling Tech Co., Ltd.",
            "platform": "alibaba",
            "location": "CN",
            "product": "portable fan",
        }, "Supplier", "Shenzhen Cooling Tech Co., Ltd.")

        plan = build_trade_relation_plan(fact)
        rels = {(e.source.label, e.rel_type, e.target.label, e.target.name) for e in plan.edges}
        assert plan.node_count == 2
        assert ("Supplier", "SUPPLIES", "TradeProduct", "portable fan") in rels

    async def test_apply_trade_relation_plan_writes_nodes_and_edges(self):
        fact = _fact({
            "name": "portable fan",
            "hs_code": "841451",
            "market": "US",
            "certifications": ["FCC"],
            "supplier": "Shenzhen Cooling Tech Co., Ltd.",
        }, "Product", "portable fan")
        graph = AsyncMock()
        graph.query_cypher = AsyncMock(return_value=[])

        counts = await apply_trade_relation_plan(graph, fact)

        assert counts == {"nodes": 5, "edges": 4}
        cyphers = [call.args[0] for call in graph.query_cypher.call_args_list]
        assert any("MERGE (n:TradeProduct" in c for c in cyphers)
        assert any("MERGE (n:HSCode" in c for c in cyphers)
        assert any("HAS_HS_CODE" in c for c in cyphers)
        assert any("SUPPLIES" in c for c in cyphers)
