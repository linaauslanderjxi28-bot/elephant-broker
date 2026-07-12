"""Fail-closed classifier for automatic document graph extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

AUTHORITATIVE_SOURCE_TYPES = {
    "official_api",
    "official_dataset",
    "official_report",
    "first_party_api",
    "first_party_page",
    "first_party_document",
}
ALLOWED_CLASSES = {
    "official_regulation",
    "exhibitor_profile",
    "customs_record",
    "procurement_document",
}
MIN_CHARS = 160
AUTO_APPROVE_SCORE = 0.85


@dataclass(frozen=True)
class DocumentGateDecision:
    document_class: str
    status: str
    score: float
    reasons: list[str]
    source_url: str
    source_type: str
    authority_tier: str


def _text(value: Any) -> str:
    return str(value or "").strip()


def _detect_class(text: str, metadata: dict[str, Any]) -> str:
    lower = text.lower()
    filename = _text(metadata.get("filename")).lower()
    source_url = _text(metadata.get("source_url") or metadata.get("provenance_url"))
    host = urlparse(source_url).netloc.lower()
    if (
        metadata.get("expo_id")
        and metadata.get("edition")
        and (metadata.get("company_name") or "exhibitor" in lower or "booth" in lower)
    ):
        return "exhibitor_profile"
    if any(
        token in lower or token in filename
        for token in ("customs", "hs code", "harmonized", "comtrade", "import record")
    ):
        return "customs_record"
    if any(
        token in lower or token in filename
        for token in ("procurement", "tender", "rfq", "invitation to bid", "contract notice")
    ):
        return "procurement_document"
    if any(
        token in lower or token in filename
        for token in ("regulation", "directive", "legal notice", "mandatory requirement")
    ) and any(token in host for token in ("gov", "europa.eu", "eur-lex", "ec.europa.eu")):
        return "official_regulation"
    return "unknown"


def classify_document(doc_id: str, text: str, metadata: dict[str, Any] | None = None) -> DocumentGateDecision:
    """Classify a document and issue a fail-closed automatic execution decision."""
    del doc_id
    metadata = metadata or {}
    text = _text(text)
    source_url = _text(metadata.get("source_url") or metadata.get("provenance_url"))
    source_type = _text(metadata.get("source_type"))
    authority_tier = _text(metadata.get("authority_tier") or source_type)
    document_class = _detect_class(text, metadata)
    reasons: list[str] = []
    score = 0.0

    if not source_url:
        reasons.append("missing_source_url")
    else:
        score += 0.20
    if source_type not in AUTHORITATIVE_SOURCE_TYPES and authority_tier not in AUTHORITATIVE_SOURCE_TYPES:
        reasons.append("non_authoritative_source")
    else:
        score += 0.35
    if len(text) < MIN_CHARS:
        reasons.append("content_too_short")
    else:
        score += 0.20
    if document_class not in ALLOWED_CLASSES:
        reasons.append("unsupported_document_class")
    else:
        score += 0.25

    status = "eligible" if not reasons and score >= AUTO_APPROVE_SCORE else "rejected_by_gate"
    return DocumentGateDecision(
        document_class=document_class,
        status=status,
        score=round(score, 3),
        reasons=reasons,
        source_url=source_url,
        source_type=source_type,
        authority_tier=authority_tier,
    )
