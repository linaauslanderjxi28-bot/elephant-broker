"""
Cross-Border Ontology — Product, Supplier, MarketSignal, Decision.
Standalone — no EB import needed. Outputs FactAssertion-compatible dicts.
"""
from __future__ import annotations
import json, uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc


def _make_fact(text: str, category: str, entity_type: str, entity_name: str,
               confidence: float = 0.9, decision_domain: str = "ecommerce",
               goal_ids: list[str] | None = None,
               decision_status: str | None = None,
               extra: dict | None = None) -> dict:
    """Build a FactAssertion-compatible dict."""
    fact: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "text": text,
        "category": category,
        "scope": "global",
        "confidence": confidence,
        "memory_class": "semantic",
        "entity_type": entity_type,
        "entity_name": entity_name,
        "decision_domain": decision_domain,
        "provenance_refs": [],
        "goal_ids": goal_ids or [],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if decision_status:
        fact["decision_status"] = decision_status
    if extra:
        fact.update(extra)
    return fact

# ================================================================
# Entity Types
# ================================================================

class Product:
    """A product in the cross-border research pipeline."""
    entity_type = "Product"
    
    def __init__(self, name: str, category: str = "", asin: str = "",
                 price: float = 0, bsr: int = 0, rating: float = 0,
                 reviews: int = 0, platform: str = "amazon",
                 competitor_brands: list[str] | None = None):
        self.name = name
        self.category = category
        self.asin = asin
        self.price = price
        self.bsr = bsr
        self.rating = rating
        self.reviews = reviews
        self.platform = platform
        self.competitor_brands = competitor_brands or []

    def to_fact(self) -> dict:
        return _make_fact(
            text=json.dumps({"name":self.name,"category":self.category,"asin":self.asin,
                "price":self.price,"bsr":self.bsr,"rating":self.rating,"reviews":self.reviews,
                "platform":self.platform}, ensure_ascii=False),
            category=self.category, entity_type=self.entity_type, entity_name=self.name,
        )

    def estimated_monthly_sales(self) -> int:
        """BSR → monthly sales (US, general category)."""
        REF = {100: 12000, 500: 4000, 1000: 2200, 3000: 900, 5000: 600, 10000: 300, 30000: 80, 100000: 20}
        for threshold in sorted(REF):
            if self.bsr <= threshold: return REF[threshold]
        return 5


class Supplier:
    """A factory/supplier from 1688 or AliExpress."""
    entity_type = "Supplier"
    
    def __init__(self, name: str, platform: str = "1688",
                 price: float = 0, moq: int = 0, location: str = "",
                 contact: str = ""):
        self.name = name
        self.platform = platform
        self.price = price
        self.moq = moq
        self.location = location
        self.contact = contact

    def to_fact(self) -> dict:
        return _make_fact(
            text=json.dumps({"name":self.name,"platform":self.platform,"price":self.price,
                "moq":self.moq,"location":self.location,"contact":self.contact}, ensure_ascii=False),
            category="supplier", entity_type=self.entity_type, entity_name=self.name, confidence=0.8,
        )


class MarketSignal:
    """A trend/social signal for a keyword."""
    entity_type = "MarketSignal"
    
    def __init__(self, keyword: str, platform: str = "google",
                 avg_interest: float = 0, growth: str = "stable",
                 source: str = "pytrends"):
        self.keyword = keyword
        self.platform = platform
        self.avg_interest = avg_interest
        self.growth = growth
        self.source = source

    def to_fact(self) -> dict:
        return _make_fact(
            text=json.dumps({"keyword":self.keyword,"platform":self.platform,
                "avg_interest":self.avg_interest,"growth":self.growth,"source":self.source}, ensure_ascii=False),
            category="market_signal", entity_type=self.entity_type, entity_name=self.keyword, confidence=0.85,
        )


class ResearchDecision:
    """A research decision connecting signals → products → prospects → deals.

    Status lifecycle: proposed → investigating → pursued → dropped → completed
    Entity links enable graph traversal: MarketSignal → [triggered] → ResearchDecision
    """
    entity_type = "ResearchDecision"
    VALID_STATUSES = ("proposed", "investigating", "pursued", "dropped", "completed")
    
    def __init__(self, verdict: str, confidence: float = 0.7,
                 reasoning: str = "", status: str = "proposed",
                 linked_products: list[str] | None = None,
                 linked_signals: list[str] | None = None,
                 linked_prospects: list[str] | None = None,
                 linked_deals: list[str] | None = None,
                 entity_links: list[dict] | None = None,
                 evidence_count: int = 0,
                 maturity: str = "experimental"):
        self.verdict = verdict
        self.confidence = confidence
        self.reasoning = reasoning
        self.status = status if status in self.VALID_STATUSES else "proposed"
        self.linked_products = linked_products or []
        self.linked_signals = linked_signals or []
        self.linked_prospects = linked_prospects or []
        self.linked_deals = linked_deals or []
        self.entity_links = entity_links or []
        self.evidence_count = evidence_count
        self.maturity = maturity

    def to_fact(self) -> FactAssertion:
        text = json.dumps({
            "verdict": self.verdict, "reasoning": self.reasoning,
            "status": self.status, "maturity": self.maturity,
            "linked_products": self.linked_products,
            "linked_signals": self.linked_signals,
            "linked_prospects": self.linked_prospects,
            "linked_deals": self.linked_deals,
            "evidence_count": self.evidence_count,
        }, ensure_ascii=False)
        goal_ids = []
        for pid in self.linked_products:
            try: goal_ids.append(uuid.UUID(pid))
            except ValueError: pass
        return FactAssertion(
            text=text, category="decision", scope=Scope.GLOBAL,
            memory_class=MemoryClass.SEMANTIC, confidence=self.confidence,
            entity_type=self.entity_type, entity_name=self.verdict,
            decision_domain="ecommerce",
            decision_status=self.status,
            goal_ids=goal_ids,
            quality_score=self.confidence,
        )


def make_entity_link(from_type: str, from_id: str, to_type: str, to_id: str,
                     relation: str = "references") -> dict:
    """Create an entity link dict for graph traversal.

    Args:
        from_type: source entity_type (e.g. "ResearchDecision")
        from_id: source entity ID
        to_type: target entity_type (e.g. "MarketSignal")
        to_id: target entity ID
        relation: link type (triggered_by, found, leads_to, references)
    """
    return {
        "from_type": from_type, "from_id": from_id,
        "to_type": to_type, "to_id": to_id,
        "relation": relation,
    }


# ================================================================
# B2B Entity Types — Foreign Trade Customer Acquisition
# ================================================================

class Prospect:
    """A potential overseas buyer."""
    entity_type = "Prospect"

    def __init__(self, company: str, industry: str = "",
                 source: str = "", contact_name: str = "",
                 email: str = "", phone: str = "",
                 importing: list[str] | None = None,
                 status: str = "new"):
        self.company = company
        self.industry = industry
        self.source = source
        self.contact_name = contact_name
        self.email = email
        self.phone = phone
        self.importing = importing or []
        self.status = status

    def to_fact(self) -> dict:
        return _make_fact(
            text=json.dumps({"company":self.company,"industry":self.industry,"source":self.source,
                "contact":self.contact_name,"email":self.email,"phone":self.phone,
                "importing":self.importing,"status":self.status}, ensure_ascii=False),
            category="prospect", entity_type=self.entity_type, entity_name=self.company,
            confidence=0.8, decision_status=self.status,
        )


class CustomsRecord:
    """A customs import/export record from UN Comtrade or similar API."""
    entity_type = "CustomsRecord"

    def __init__(self, buyer: str, hs_code: str = "", product_desc: str = "",
                 volume_kg: float = 0, value_usd: float = 0,
                 period: str = "", country: str = "", source_api: str = "UN_Comtrade"):
        self.buyer = buyer
        self.hs_code = hs_code
        self.product_desc = product_desc
        self.volume_kg = volume_kg
        self.value_usd = value_usd
        self.period = period
        self.country = country
        self.source_api = source_api

    def to_fact(self) -> dict:
        return _make_fact(
            text=json.dumps({"buyer":self.buyer,"hs_code":self.hs_code,
                "product":self.product_desc,"volume_kg":self.volume_kg,
                "value_usd":self.value_usd,"period":self.period,
                "country":self.country,"source":self.source_api}, ensure_ascii=False),
            category="customs_data", entity_type=self.entity_type, entity_name=self.buyer,
            confidence=0.95,
        )


class Deal:
    """A B2B deal in the pipeline."""
    entity_type = "Deal"

    def __init__(self, buyer_company: str, product: str = "",
                 amount: float = 0, currency: str = "USD",
                 probability: float = 0.5, stage: str = "new",
                 linked_prospects: list[str] | None = None,
                 notes: str = ""):
        self.buyer_company = buyer_company
        self.product = product
        self.amount = amount
        self.currency = currency
        self.probability = probability
        self.stage = stage  # new/negotiating/won/lost
        self.linked_prospects = linked_prospects or []
        self.notes = notes

    def to_fact(self) -> dict:
        return _make_fact(
            text=json.dumps({"buyer":self.buyer_company,"product":self.product,
                "amount":self.amount,"currency":self.currency,
                "probability":self.probability,"stage":self.stage,"notes":self.notes},
                ensure_ascii=False),
            category="deal", entity_type=self.entity_type, entity_name=self.buyer_company,
            confidence=self.probability, decision_status=self.stage,
            goal_ids=self.linked_prospects,
        )


# ================================================================
# Pipeline: Trends → Products → Suppliers → Decisions
# ================================================================

async def run_product_discovery(store_func) -> dict:
    """
    Full pipeline: trend discovery → product search → BSR estimate → report.
    store_func: async function to store FactAssertion (calls EB /memory/store).
    """
    results = {"signals": 0, "products": 0, "suppliers": 0, "decisions": 0}
    
    # 1. Trend signals
    signals = [
        MarketSignal("bluetooth earbuds", avg_interest=45, growth="stable"),
        MarketSignal("neck massager", avg_interest=10, growth="down"),
        MarketSignal("tiktok shop", avg_interest=53, growth="up"),
    ]
    for s in signals:
        await store_func(s.to_fact())
        results["signals"] += 1
    
    # 2. Products (simulated — real data from searxng+scrapling)
    products = [
        Product("蓝牙耳机 Pro", category="electronics", bsr=1500, price=24.99, rating=4.2, reviews=3500),
        Product("颈部按摩仪", category="health", bsr=800, price=19.99, rating=3.8, reviews=1200),
    ]
    for p in products:
        await store_func(p.to_fact())
        results["products"] += 1
    
    # 3. Suppliers
    suppliers = [
        Supplier("深圳华强北电子", price=4.50, moq=500, location="深圳"),
    ]
    for s in suppliers:
        fact = s.to_fact()
        if products:
            fact.goal_ids.append(products[0].to_fact().id)
        await store_func(fact)
        results["suppliers"] += 1
    
    # 4. Decision
    decision = ResearchDecision(
        verdict="蓝牙耳机 Pro — GO",
        confidence=0.85,
        reasoning="45 avg interest, BSR 1500 (~900/mo), supplier $4.50, margin 82%",
        linked_products=[str(products[0].to_fact().id)],
    )
    await store_func(decision.to_fact())
    results["decisions"] += 1
    
    return results


# ================================================================
# Quick Start
# ================================================================
def demo():
    """Print what the ontology produces."""
    p = Product("蓝牙耳机Pro", "electronics", bsr=1500, price=24.99, rating=4.2)
    s = Supplier("深圳华强北电子", price=4.50, moq=500)
    m = MarketSignal("bluetooth earbuds", avg_interest=45, growth="up")
    d = ResearchDecision("GO", 0.85, "good margin, rising trend", ["product-id"])
    
    pf = p.to_fact()
    sf = s.to_fact()
    mf = m.to_fact()
    df = d.to_fact()
    
    print(f"Product.fact:       entity={pf['entity_type']}, name={pf['entity_name']}")
    print(f"                   sales_est={p.estimated_monthly_sales()}/mo, margin={(1-s.price/p.price)*100:.0f}%")
    print(f"Supplier.fact:      entity={sf['entity_type']}, moq={s.moq}, price=${s.price}")
    print(f"MarketSignal.fact:  entity={mf['entity_type']}, interest={m.avg_interest}")
    print(f"Decision.fact:      entity={df['entity_type']}, status={df.get('decision_status')}")
    print(f"                   linked: {d.linked_products}")
