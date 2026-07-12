"""Fail-closed eligibility gate for asynchronous LLM graph extraction of trade chat facts."""

from __future__ import annotations

import re
from dataclasses import dataclass

TRADE_TERMS = {
    "hs code",
    "tariff",
    "customs",
    "supplier",
    "supplier",
    "factory",
    "manufacturer",
    "exhibitor",
    "expo",
    "trade fair",
    "procurement",
    "tender",
    "rfq",
    "buyer",
    "import",
    "export",
    "fob",
    "cif",
    "ddp",
    "incoterm",
    "certificate",
    "certification",
    "compliance",
    "vat",
    "epr",
    "rohs",
    "fda",
    "ce",
    "freight",
    "logistics",
    "关税",
    "海关",
    "供应商",
    "工厂",
    "展商",
    "展会",
    "采购",
    "招标",
    "买家",
    "出口",
    "进口",
    "认证",
    "合规",
    "运费",
    "物流",
    "原产地",
    "hs编码",
}
MIN_CHARS = 80
MIN_TERM_HITS = 2
MIN_CONFIDENCE = 0.80


@dataclass(frozen=True)
class ChatGraphDecision:
    status: str
    score: float
    reasons: list[str]


def classify_trade_chat(*, text: str, confidence: float, decision_domain: str | None = None) -> ChatGraphDecision:
    """Allow only sufficiently detailed, high-confidence cross-border trade facts."""
    normalized = re.sub(r"\s+", " ", text or "").lower()
    reasons: list[str] = []
    score = 0.0
    if len(normalized) < MIN_CHARS:
        reasons.append("content_too_short")
    else:
        score += 0.25
    if confidence < MIN_CONFIDENCE:
        reasons.append("confidence_below_threshold")
    else:
        score += 0.25
    domain = (decision_domain or "").lower()
    hits = sum(1 for term in TRADE_TERMS if term in normalized)
    if domain == "cross-border-trade":
        score += 0.30
    elif hits >= MIN_TERM_HITS:
        score += 0.30
    else:
        reasons.append("not_trade_relevant")
    if any(char.isdigit() for char in normalized) or any(
        token in normalized for token in ("http", "source", "evidence", "证据", "数据")
    ):
        score += 0.20
    else:
        reasons.append("insufficient_structured_evidence")
    return ChatGraphDecision(
        status="eligible" if not reasons and score >= 0.90 else "rejected_by_gate",
        score=round(score, 3),
        reasons=reasons,
    )
