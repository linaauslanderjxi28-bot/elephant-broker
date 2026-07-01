"""Memory fact schemas."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from elephantbroker.schemas.base import Scope


class FactCategory(StrEnum):
    """Categories of facts stored in memory."""
    IDENTITY = "identity"
    PREFERENCE = "preference"
    EVENT = "event"
    DECISION = "decision"
    SYSTEM = "system"
    RELATIONSHIP = "relationship"
    TRAIT = "trait"
    PROJECT = "project"
    GENERAL = "general"
    CONSTRAINT = "constraint"
    PROCEDURE_REF = "procedure_ref"
    VERIFICATION = "verification"


BUILTIN_CATEGORIES: list[str] = [member.value for member in FactCategory]
"""All built-in FactCategory values as plain strings."""


class MemoryClass(StrEnum):
    """Classification of fact durability and retrieval behavior."""
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    POLICY = "policy"
    WORKING_MEMORY = "working_memory"


class FactAssertion(BaseModel):
    """A single fact stored in memory."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    text: str = Field(min_length=1)
    category: str = "general"
    scope: Scope = Scope.SESSION
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    memory_class: MemoryClass = MemoryClass.EPISODIC
    session_key: str | None = None
    session_id: uuid.UUID | None = None
    source_actor_id: uuid.UUID | None = None
    target_actor_ids: list[uuid.UUID] = Field(default_factory=list)
    goal_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None
    use_count: int = Field(default=0, ge=0)
    successful_use_count: int = Field(default=0, ge=0)
    freshness_score: float | None = None
    provenance_refs: list[str] = Field(default_factory=list)
    embedding_ref: str | None = None
    token_size: int | None = None
    goal_relevance_tags: dict[str, str] = Field(default_factory=dict)
    gateway_id: str = ""
    decision_domain: str | None = None
    archived: bool = False
    autorecall_blacklisted: bool = False


class FactConflict(BaseModel):
    """Records a detected conflict between two facts."""
    conflict_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    fact_a_id: uuid.UUID
    fact_b_id: uuid.UUID
    description: str
    conflict_type: str = ""
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved: bool = False
    resolution: str | None = None


class FactSortField(StrEnum):
    """Whitelisted sortable columns for the dashboard fact browser (Phase 11).

    Each value is an exact ``FactDataPoint`` property name; it is interpolated
    into the Cypher ``ORDER BY`` clause by ``MemoryStoreFacade.query_facts``, so
    this enum is the injection-safety boundary — user input never reaches the
    query string.
    """
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    CONFIDENCE = "confidence"
    USE_COUNT = "use_count"
    LAST_USED_AT = "last_used_at"


class FactSort(BaseModel):
    """Sort specification for the paginated dashboard fact query (Phase 11)."""
    field: FactSortField = FactSortField.CREATED_AT
    descending: bool = True


class FactFilters(BaseModel):
    """Structural filters for the dashboard memory browser (Phase 11).

    Every field is optional, so ``FactFilters()`` selects all facts in the
    gateway. All values are passed to Cypher as bound parameters (never
    interpolated). Consumed by ``MemoryStoreFacade.query_facts`` and
    ``/dashboard/memory/browse``.
    """
    scope: Scope | None = None
    memory_class: MemoryClass | None = None
    category: str | None = None
    actor_id: str | None = None
    session_key: str | None = None
    session_id: str | None = None
    archived: bool | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    text_contains: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


class FactPage(BaseModel):
    """One page of facts returned by ``MemoryStoreFacade.query_facts`` (Phase 11)."""
    items: list[FactAssertion] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
    total_pages: int = 0
