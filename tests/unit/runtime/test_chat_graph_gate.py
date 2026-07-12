"""Tests for high-value trade chat graph-extraction gate."""

from __future__ import annotations

from elephantbroker.runtime.chat_graph_gate import classify_trade_chat


def test_detailed_trade_fact_is_eligible():
    decision = classify_trade_chat(
        text=(
            "Supplier Shenzhen Cooling Tech quoted FOB USD 4.20 for portable fans, MOQ 500, "
            "HS code 841451, shipping from CN to US. FCC certification is required and tariff "
            "assessment should include the origin-country rule."
        ),
        confidence=0.92,
        decision_domain="cross-border-trade",
    )
    assert decision.status == "eligible"
    assert decision.score >= 0.90


def test_generic_chat_is_rejected():
    decision = classify_trade_chat(text="We should discuss this later after the meeting.", confidence=0.95)
    assert decision.status == "rejected_by_gate"
    assert "not_trade_relevant" in decision.reasons


def test_low_confidence_trade_chat_is_rejected():
    decision = classify_trade_chat(
        text=(
            "A supplier may offer a FOB quote for HS code 841451 and export the product "
            "to the US market with customs and certification requirements."
        ),
        confidence=0.5,
        decision_domain="cross-border-trade",
    )
    assert decision.status == "rejected_by_gate"
    assert "confidence_below_threshold" in decision.reasons
