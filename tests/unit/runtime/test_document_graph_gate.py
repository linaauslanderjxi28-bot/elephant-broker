"""Tests for automatic high-value document graph extraction gates."""

from __future__ import annotations

from elephantbroker.runtime.document_graph_gate import classify_document


def test_official_regulation_with_provenance_is_eligible():
    decision = classify_document(
        doc_id="reg-1",
        text=(
            "Commission Regulation (EU) 2025/123 establishes mandatory product safety requirements "
            "for economic operators placing electrical appliances on the Union market, including "
            "conformity assessment, technical documentation, traceability, and enforcement measures."
        ),
        metadata={
            "source_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32025R0123",
            "source_type": "official_report",
            "authority_tier": "official_report",
            "filename": "commission-regulation.pdf",
        },
    )

    assert decision.document_class == "official_regulation"
    assert decision.status == "eligible"
    assert decision.score >= 0.85


def test_official_exhibitor_profile_is_eligible():
    decision = classify_document(
        doc_id="expo-1",
        text=(
            "A UNO TEC SRL, Hall 7 Booth B12, is listed in the official MEDICA 2026 directory as "
            "a medical packaging machinery exhibitor. The profile identifies the company, exhibition "
            "edition, hall location, product scope, and official directory provenance for trade verification."
        ),
        metadata={
            "source_url": "https://www.medica-tradefair.com/vis/v1/en/directory/a",
            "source_type": "first_party_page",
            "expo_id": "medica",
            "edition": "2026",
            "company_name": "A UNO TEC SRL",
        },
    )

    assert decision.document_class == "exhibitor_profile"
    assert decision.status == "eligible"


def test_missing_provenance_is_rejected_even_when_text_looks_regulatory():
    decision = classify_document(
        doc_id="bad-1",
        text="Regulation mandatory customs import requirements and HS code 841451.",
        metadata={"filename": "copied-regulation.txt"},
    )

    assert decision.status == "rejected_by_gate"
    assert "missing_source_url" in decision.reasons


def test_social_source_is_rejected():
    decision = classify_document(
        doc_id="social-1",
        text="Official customs requirements are changing this month.",
        metadata={
            "source_url": "https://reddit.com/r/trade/comments/1",
            "source_type": "social_post",
            "authority_tier": "community",
        },
    )

    assert decision.status == "rejected_by_gate"
    assert "non_authoritative_source" in decision.reasons


def test_short_navigation_document_is_rejected():
    decision = classify_document(
        doc_id="short-1",
        text="Home | About | Contact",
        metadata={
            "source_url": "https://gov.example/rules",
            "source_type": "official_report",
            "authority_tier": "official_report",
        },
    )

    assert decision.status == "rejected_by_gate"
    assert "content_too_short" in decision.reasons
