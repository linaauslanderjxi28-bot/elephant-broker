"""Entity type and relation registry for the cross-border ontology.

Defines registered entity types with their required JSON fields,
allowed inter-entity relations, and a soft-validate entry point.

Soft mode (default): unregistered entity types log a warning but
do not reject storage. This keeps backward compatibility while
surfacing ontology drift. Set ``EB_STRICT_ONTOLOGY=true`` to
reject unregistered types.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("elephantbroker.ontology.registry")

# ---------------------------------------------------------------------------
# Entity type definitions
# ---------------------------------------------------------------------------

ENTITY_TYPES: dict[str, dict[str, Any]] = {
    "Product": {
        "required_fields": ["name"],
        "optional_fields": ["category", "asin", "price", "bsr", "rating", "reviews", "platform"],
        "description": "A product in the cross-border research pipeline",
    },
    "Supplier": {
        "required_fields": ["name", "platform"],
        "optional_fields": ["price", "moq", "location", "contact"],
        "description": "A factory/supplier from 1688 or AliExpress",
    },
    "MarketSignal": {
        "required_fields": ["signal_type", "keyword"],
        "optional_fields": ["source", "volume", "trend", "sentiment_label"],
        "description": "A signal from social listening or market research",
    },
    "ResearchDecision": {
        "required_fields": ["verdict", "status"],
        "optional_fields": ["reasoning", "confidence", "linked_products", "linked_signals",
                           "evidence_count", "maturity"],
        "description": "A research decision connecting signals to actions",
    },
    "RiskAlert": {
        "required_fields": ["risk_type", "market"],
        "optional_fields": ["severity", "description", "source", "action_required"],
        "description": "A risk alert for a market or product",
    },
    "PricePoint": {
        "required_fields": ["product", "price", "currency"],
        "optional_fields": ["source", "observed_at", "market", "price_type"],
        "description": "A price observation for a product",
    },
    "CustomsRecord": {
        "required_fields": ["hs_code", "market"],
        "optional_fields": ["import_value", "quantity", "period", "tariff_rate"],
        "description": "A customs/trade data record",
    },
    "FinancialReport": {
        "required_fields": ["entity_name", "report_type"],
        "optional_fields": ["period", "revenue", "margin", "source", "currency"],
        "description": "A financial report or filing",
    },
    "Deal": {
        "required_fields": ["deal_type", "status"],
        "optional_fields": ["amount", "currency", "parties", "product", "close_date"],
        "description": "A business deal/transaction",
    },
    "Prospect": {
        "required_fields": ["company"],
        "optional_fields": ["industry", "source", "contact_name", "email", "phone",
                           "importing", "status"],
        "description": "A potential overseas buyer",
    },
    "SentimentReport": {
        "required_fields": ["entity", "sentiment"],
        "optional_fields": ["score", "source", "observed_at", "keywords"],
        "description": "A sentiment analysis report",
    },
    "Organization": {
        "required_fields": ["name"],
        "optional_fields": ["org_type", "region", "industry"],
        "description": "An organization entity",
    },
}

# ---------------------------------------------------------------------------
# Allowed relations between entity types
# ---------------------------------------------------------------------------

ALLOWED_RELATIONS: set[tuple[str, str]] = {
    ("Supplier", "Product"): "SUPPLIES",
    ("Product", "Supplier"): "SUPPLIED_BY",
    ("MarketSignal", "ResearchDecision"): "TRIGGERS",
    ("ResearchDecision", "MarketSignal"): "TRIGGERED_BY",
    ("RiskAlert", "Product"): "WARNS_ABOUT",
    ("ResearchDecision", "Product"): "CONCERNS",
    ("ResearchDecision", "Deal"): "LEADS_TO",
    ("Deal", "ResearchDecision"): "DERIVED_FROM",
    ("Deal", "Prospect"): "INVOLVES",
    ("Prospect", "Deal"): "PARTY_TO",
    ("Product", "FinancialReport"): "REFERENCED_BY",
    ("PricePoint", "Product"): "PRICES",
    ("CustomsRecord", "Product"): "TRACKS",
    ("MarketSignal", "Product"): "SIGNALS_ABOUT",
    ("SentimentReport", "Product"): "ANALYZES",
    ("SentimentReport", "MarketSignal"): "ANALYZES",
}


def validate_entity_type(text: str, entity_type: str | None) -> list[str]:
    """Validate fact text JSON against entity type's required fields.

    Returns a list of warning messages (empty = valid).
    In soft mode (default), missing fields are warnings, not errors.
    """
    if not entity_type or entity_type not in ENTITY_TYPES:
        if entity_type:
            logger.debug("Unregistered entity type: %s", entity_type)
        return []

    et_def = ENTITY_TYPES[entity_type]
    required = set(et_def["required_fields"])

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return [f"Entity type {entity_type} text is not valid JSON"]

    if not isinstance(data, dict):
        return [f"Entity type {entity_type} text must be a JSON object"]

    missing = [f for f in required if f not in data or data[f] is None]
    if missing:
        msg = f"Entity type {entity_type} missing required fields: {', '.join(missing)}"
        if os.environ.get("EB_STRICT_ONTOLOGY") == "true":
            raise ValueError(msg)
        logger.warning("Ontology validation (soft): %s", msg)
        return [msg]

    return []


def validate_relation(from_type: str, to_type: str) -> bool:
    """Check if a relation between two entity types is allowed."""
    key = (from_type, to_type)
    if key in ALLOWED_RELATIONS:
        return True
    logger.debug(
        "Unregistered relation: %s → %s (allowed: %s)",
        from_type, to_type,
        ", ".join(f"{s}→{t}" for s, t in ALLOWED_RELATIONS),
    )
    if os.environ.get("EB_STRICT_ONTOLOGY") == "true":
        raise ValueError(
            f"Relation {from_type} → {to_type} is not in the allowed relation set"
        )
    return False


def get_required_fields(entity_type: str) -> list[str]:
    """Return the required field list for an entity type, or empty list."""
    return list(ENTITY_TYPES.get(entity_type, {}).get("required_fields", []))
