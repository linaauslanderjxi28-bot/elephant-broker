"""Tests for deterministic trade relation builder (KG-4)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from elephantbroker.runtime.trade_relations import apply_trade_relation_plan, build_trade_relation_plan
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
        fact = _fact(
            {
                "name": "portable fan",
                "category": "small_appliance",
                "hs_code": "841451",
                "market": "US",
                "certifications": ["FCC", "UL"],
                "supplier": "Shenzhen Cooling Tech Co., Ltd.",
            },
            "Product",
            "portable fan",
        )

        plan = build_trade_relation_plan(fact)
        rels = {(e.source.label, e.rel_type, e.target.label, e.target.name) for e in plan.edges}

        assert plan.node_count == 6
        assert ("TradeProduct", "HAS_HS_CODE", "HSCode", "841451") in rels
        assert ("TradeProduct", "SOLD_IN_MARKET", "Market", "US") in rels
        assert ("TradeProduct", "REQUIRES_CERTIFICATION", "Certification", "FCC") in rels
        assert ("TradeProduct", "REQUIRES_CERTIFICATION", "Certification", "UL") in rels
        assert ("Supplier", "SUPPLIES", "TradeProduct", "portable fan") in rels

    def test_supplier_builds_supplies_edge(self):
        fact = _fact(
            {
                "name": "Shenzhen Cooling Tech Co., Ltd.",
                "platform": "alibaba",
                "location": "CN",
                "product": "portable fan",
            },
            "Supplier",
            "Shenzhen Cooling Tech Co., Ltd.",
        )

        plan = build_trade_relation_plan(fact)
        rels = {(e.source.label, e.rel_type, e.target.label, e.target.name) for e in plan.edges}
        assert plan.node_count == 2
        assert ("Supplier", "SUPPLIES", "TradeProduct", "portable fan") in rels

    async def test_apply_trade_relation_plan_writes_nodes_and_edges(self):
        fact = _fact(
            {
                "name": "portable fan",
                "hs_code": "841451",
                "market": "US",
                "certifications": ["FCC"],
                "supplier": "Shenzhen Cooling Tech Co., Ltd.",
            },
            "Product",
            "portable fan",
        )
        graph = AsyncMock()
        graph.query_cypher = AsyncMock(return_value=[])

        counts = await apply_trade_relation_plan(graph, fact)

        assert counts == {"nodes": 5, "edges": 4}
        cyphers = [call.args[0] for call in graph.query_cypher.call_args_list]
        assert any("MERGE (n:TradeProduct" in c for c in cyphers)
        assert any("MERGE (n:HSCode" in c for c in cyphers)
        assert any("HAS_HS_CODE" in c for c in cyphers)
        assert any("SUPPLIES" in c for c in cyphers)

    def test_tariff_rule_links_hs_origin_and_destination(self):
        fact = _fact(
            {
                "hs_code": "841451",
                "origin_country": "CN",
                "destination_country": "US",
                "total_rate": 0.075,
            },
            "TariffRule",
            "841451 CN-US",
        )

        plan = build_trade_relation_plan(fact)
        rels = {(e.source.label, e.rel_type, e.target.label, e.target.name) for e in plan.edges}

        assert ("TariffRule", "APPLIES_TO_HS_CODE", "HSCode", "841451") in rels
        assert ("TariffRule", "ORIGINATES_IN", "Country", "CN") in rels
        assert ("TariffRule", "DESTINED_FOR", "Market", "US") in rels

    def test_exporter_demand_links_company_product_and_market(self):
        fact = _fact(
            {
                "company_name": "Ningbo Fan Export Co.",
                "product": "portable fan",
                "target_markets": ["US", "DE"],
                "demand_type": "market_entry",
            },
            "ExporterDemand",
            "market entry demand",
        )

        plan = build_trade_relation_plan(fact)
        rels = {(e.source.label, e.rel_type, e.target.label, e.target.name) for e in plan.edges}

        assert ("ExporterCompany", "HAS_DEMAND", "ExporterDemand", "market entry demand") in rels
        assert ("ExporterDemand", "CONCERNS_PRODUCT", "TradeProduct", "portable fan") in rels
        assert ("ExporterDemand", "TARGETS_MARKET", "Market", "US") in rels
        assert ("ExporterDemand", "TARGETS_MARKET", "Market", "DE") in rels

    def test_exhibitor_links_company_to_edition(self):
        fact = _fact(
            {
                "company_name": "Ningbo Fan Export Co.",
                "expo_id": "ifa",
                "edition": "2026",
                "expo_name": "IFA Berlin",
                "country": "China",
            },
            "ExpoExhibitor",
            "Ningbo Fan Export Co.",
        )

        plan = build_trade_relation_plan(fact)
        rels = {(e.source.label, e.rel_type, e.target.label, e.target.name) for e in plan.edges}

        assert ("ExhibitorCompany", "EXHIBITED_AT", "ExpoEdition", "IFA Berlin 2026") in rels
        assert ("ExhibitorCompany", "LOCATED_IN", "Country", "China") in rels

    def test_hot_prediction_links_product_market_and_signals(self):
        fact = _fact(
            {
                "run_id": "run-42",
                "keyword": "portable fan",
                "market": "US",
                "hs_code": "841451",
                "signal_ids": ["signal-a", "signal-b"],
            },
            "HotProductPrediction",
            "portable fan US",
        )

        plan = build_trade_relation_plan(fact)
        rels = {(e.source.label, e.rel_type, e.target.label, e.target.name) for e in plan.edges}

        assert ("HotProductPrediction", "PREDICTS", "TradeProduct", "portable fan") in rels
        assert ("HotProductPrediction", "FOR_MARKET", "Market", "US") in rels
        assert ("HotProductPrediction", "SUPPORTED_BY", "MarketSignal", "signal-a") in rels
        assert ("HotProductPrediction", "SUPPORTED_BY", "MarketSignal", "signal-b") in rels

    def test_skill_index_links_skill_to_produced_and_consumed_object_types(self):
        fact = _fact(
            {
                "name": "supplier-sourcing",
                "produces": ["SupplierQuote", "Supplier"],
                "consumes": ["Product"],
            },
            "SkillIndex",
            "supplier-sourcing",
        )

        plan = build_trade_relation_plan(fact)
        rels = {(e.source.label, e.rel_type, e.target.label, e.target.name) for e in plan.edges}

        assert ("TradeSkill", "PRODUCES", "ObjectType", "SupplierQuote") in rels
        assert ("TradeSkill", "PRODUCES", "ObjectType", "Supplier") in rels
        assert ("TradeSkill", "CONSUMES", "ObjectType", "Product") in rels
