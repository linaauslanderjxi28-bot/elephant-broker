"""Typed provenance references for fact assertions.

Replaces free-text ``provenance_refs: list[str]`` with validated
provenance entries that capture source, collector, and content
integrity — needed for Palantir-style lineage/auditability.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ProvenanceRef(BaseModel):
    """A typed provenance reference attached to a fact.

    Each ref records exactly one piece of evidence: a search result,
    a tool output, a model inference, an API observation, or a human
    annotation. Together they form the derivation chain for any
    ``ResearchDecision`` or stored fact.

    Enum-style values for ``source_type`` and ``collector`` are not
    enforced at the Pydantic level (backward-compatible with existing
    free-text ``provenance_refs``), but the ontology-aware validation
    in the memory facade will surface mismatches as warnings.
    """
    source_type: str = Field(
        default="",
        description="Kind of evidence source. Known: web_search, api, tool_output, "
                    "model_inference, user_input, file_import, market_intel, "
                    "social_listening, customs_data, pricing_observation",
    )
    source_name: str = Field(
        default="",
        description="Human-readable source identifier (e.g. 'searxng', '1688-cli', 'amazon')",
    )
    source_uri: str = Field(
        default="",
        description="URI of the source (URL, tool path, API endpoint)",
    )
    collector: str = Field(
        default="",
        description="Hermes skill or pipeline that collected this evidence "
                    "(e.g. 'social-scout', 'market-intel', 'customs-data')",
    )
    observed_at: str = Field(
        default="",
        description="ISO-8601 timestamp when the evidence was observed/collected",
    )
    content_hash: str = Field(
        default="",
        description="SHA-256 hex digest of the raw source content (empty if not computed)",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Collector-assigned confidence in this piece of evidence (0-1)",
    )
    note: str = Field(
        default="",
        description="Free-text annotation for human readers (e.g. 'page 3, paragraph 2')",
    )

    @property
    def is_valid(self) -> bool:
        """A provenance ref is valid if it has at least source_type and source_name."""
        return bool(self.source_type and self.source_name)

    def to_legacy_string(self) -> str:
        """Serialize to the legacy ``provenance_refs`` string format."""
        parts = [f"{self.collector}:{self.source_type}"] if self.collector else [self.source_type]
        if self.source_uri:
            parts.append(self.source_uri)
        return " ".join(parts)

    def model_dump_keyed(self) -> dict[str, Any]:
        """Dump for inclusion in fact JSON (snake_case keys)."""
        return {
            "source_type": self.source_type,
            "source_name": self.source_name,
            "source_uri": self.source_uri,
            "collector": self.collector,
            "observed_at": self.observed_at or datetime.now(UTC).isoformat(),
            "content_hash": self.content_hash,
            "confidence": self.confidence,
            "note": self.note,
        }


def typed_provenance_from_legacy(ref_strings: list[str]) -> list[ProvenanceRef]:
    """Convert legacy string provenance_refs to typed ProvenanceRefs.

    Best-effort: parses known patterns like 'skill_name:hint' or
    'source_uri'. Unknown strings become ProvenanceRef with only
    source_uri set.
    """
    result: list[ProvenanceRef] = []
    for ref in ref_strings:
        if ":" in ref and not ref.startswith("http"):
            collector, _, hint = ref.partition(":")
            result.append(ProvenanceRef(
                source_type="unknown",
                source_name=hint.strip() or collector.strip(),
                collector=collector.strip(),
            ))
        elif ref.startswith("http"):
            result.append(ProvenanceRef(
                source_type="web_source",
                source_uri=ref,
            ))
        else:
            result.append(ProvenanceRef(
                source_type="unknown",
                source_name=ref,
            ))
    return result
