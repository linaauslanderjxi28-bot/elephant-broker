"""Tests for KG-5 trade graph query layer."""

from __future__ import annotations

from unittest.mock import AsyncMock

from elephantbroker.runtime.trade_graph import TradeGraphQuery


class TestTradeGraphQuery:
    async def test_get_product_profile_returns_related_trade_entities(self):
        graph = AsyncMock()
        graph.query_cypher = AsyncMock(
            return_value=[
                {
                    "product": {"name": "portable fan", "trade_id": "TradeProduct:portable-fan"},
                    "hs_codes": ["841451"],
                    "markets": ["US"],
                    "certifications": ["FCC", "UL"],
                    "suppliers": ["Shenzhen Cooling Tech Co., Ltd."],
                }
            ]
        )
        query = TradeGraphQuery(graph, gateway_id="gw-test")

        profile = await query.get_product_profile("portable fan")

        assert profile["product"]["name"] == "portable fan"
        assert profile["hs_codes"] == ["841451"]
        assert profile["markets"] == ["US"]
        assert profile["certifications"] == ["FCC", "UL"]
        assert profile["suppliers"] == ["Shenzhen Cooling Tech Co., Ltd."]
        cypher = graph.query_cypher.call_args.args[0]
        params = graph.query_cypher.call_args.args[1]
        assert "HAS_HS_CODE" in cypher
        assert "SOLD_IN_MARKET" in cypher
        assert "REQUIRES_CERTIFICATION" in cypher
        assert "SUPPLIES" in cypher
        assert params["product_id"] == "TradeProduct:portable-fan"
        assert params["gateway_id"] == "gw-test"

    async def test_get_supplier_products_returns_product_names(self):
        graph = AsyncMock()
        graph.query_cypher = AsyncMock(return_value=[{"products": ["portable fan", "desk fan"]}])
        query = TradeGraphQuery(graph, gateway_id="gw-test")

        products = await query.get_supplier_products("Shenzhen Cooling Tech")

        assert products == ["portable fan", "desk fan"]
        params = graph.query_cypher.call_args.args[1]
        assert params["supplier_id"] == "Supplier:shenzhen-cooling-tech"

    async def test_get_hs_code_products_returns_product_names(self):
        graph = AsyncMock()
        graph.query_cypher = AsyncMock(return_value=[{"products": ["portable fan"]}])
        query = TradeGraphQuery(graph, gateway_id="gw-test")

        products = await query.get_hs_code_products("841451")

        assert products == ["portable fan"]
        params = graph.query_cypher.call_args.args[1]
        assert params["hs_id"] == "HSCode:841451"

    async def test_get_market_requirements_returns_products_and_certifications(self):
        graph = AsyncMock()
        graph.query_cypher = AsyncMock(
            return_value=[
                {"product": "portable fan", "certifications": ["FCC", "UL"]},
            ]
        )
        query = TradeGraphQuery(graph, gateway_id="gw-test")

        rows = await query.get_market_requirements("US")

        assert rows == [{"product": "portable fan", "certifications": ["FCC", "UL"]}]
        params = graph.query_cypher.call_args.args[1]
        assert params["market_id"] == "Market:us"

    async def test_get_exporter_demand_profile_returns_company_demand_products_and_markets(self):
        graph = AsyncMock()
        graph.query_cypher = AsyncMock(
            return_value=[
                {
                    "company": "Ningbo Fan Export Co.",
                    "demand": "market entry demand",
                    "demand_type": "market_entry",
                    "products": ["portable fan"],
                    "markets": ["US", "DE"],
                }
            ]
        )
        query = TradeGraphQuery(graph, gateway_id="gw-test")

        profile = await query.get_exporter_demand_profile("market entry demand")

        assert profile["company"] == "Ningbo Fan Export Co."
        assert profile["products"] == ["portable fan"]
        assert profile["markets"] == ["US", "DE"]
        cypher = graph.query_cypher.call_args.args[0]
        assert "HAS_DEMAND" in cypher
        assert "TARGETS_MARKET" in cypher

    async def test_get_prediction_evidence_returns_linked_product_market_and_signals(self):
        graph = AsyncMock()
        graph.query_cypher = AsyncMock(
            return_value=[
                {
                    "prediction": "portable fan US",
                    "product": "portable fan",
                    "market": "US",
                    "signals": ["signal-a", "signal-b"],
                }
            ]
        )
        query = TradeGraphQuery(graph, gateway_id="gw-test")

        evidence = await query.get_prediction_evidence("run-42")

        assert evidence["product"] == "portable fan"
        assert evidence["signals"] == ["signal-a", "signal-b"]
        params = graph.query_cypher.call_args.args[1]
        assert params["prediction_id"] == "HotProductPrediction:run-42"
