"""Tests for KG-5 trade graph query layer."""
from __future__ import annotations

from unittest.mock import AsyncMock

from elephantbroker.runtime.trade_graph import TradeGraphQuery


class TestTradeGraphQuery:
    async def test_get_product_profile_returns_related_trade_entities(self):
        graph = AsyncMock()
        graph.query_cypher = AsyncMock(return_value=[{
            "product": {"name": "portable fan", "trade_id": "TradeProduct:portable-fan"},
            "hs_codes": ["841451"],
            "markets": ["US"],
            "certifications": ["FCC", "UL"],
            "suppliers": ["Shenzhen Cooling Tech Co., Ltd."],
        }])
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
        graph.query_cypher = AsyncMock(return_value=[
            {"product": "portable fan", "certifications": ["FCC", "UL"]},
        ])
        query = TradeGraphQuery(graph, gateway_id="gw-test")

        rows = await query.get_market_requirements("US")

        assert rows == [{"product": "portable fan", "certifications": ["FCC", "UL"]}]
        params = graph.query_cypher.call_args.args[1]
        assert params["market_id"] == "Market:us"
