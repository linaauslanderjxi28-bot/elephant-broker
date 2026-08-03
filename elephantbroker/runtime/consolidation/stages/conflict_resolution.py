"""Detect conflicts between semantically related free-text facts without mutation."""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from elephantbroker.runtime.observability import traced
from elephantbroker.schemas.fact import FactConflict

if TYPE_CHECKING:
    from elephantbroker.schemas.consolidation import ConsolidationConfig, ConsolidationContext
    from elephantbroker.schemas.fact import FactAssertion

logger = logging.getLogger("elephantbroker.runtime.consolidation.stages.conflict_resolution")

ConflictType = Literal["semantic", "temporal", "preference", "decision", "identity"]
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class _EmbeddingService(Protocol):
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class _JsonCompletionClient(Protocol):
    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int | None = None,
        json_schema: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]: ...


_CONFLICT_SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "is_conflict": {"type": "boolean"},
        "description": {"type": "string"},
        "conflict_type": {
            "type": "string",
            "enum": ["semantic", "temporal", "preference", "decision", "identity"],
        },
    },
    "required": ["is_conflict", "description", "conflict_type"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT: Final = (
    "Classify whether two facts are logically incompatible. Similar wording alone is not a conflict. "
    "Return only JSON matching the supplied schema."
)


class _ConflictClassification(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    is_conflict: bool
    description: str
    conflict_type: ConflictType


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm < 1e-10 or right_norm < 1e-10:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


class ConflictResolutionStage:
    """Recall candidate pairs by embeddings and classify conflicts with an LLM."""

    def __init__(
        self,
        embedding_service: _EmbeddingService,
        llm_client: _JsonCompletionClient | None,
        config: ConsolidationConfig,
    ) -> None:
        self._embeddings: _EmbeddingService = embedding_service
        self._llm: _JsonCompletionClient | None = llm_client
        self._threshold: float = config.conflict_similarity_threshold

    @traced
    async def run(
        self,
        facts: list[FactAssertion],
        gateway_id: str,
        context: ConsolidationContext,
    ) -> list[FactConflict]:
        if len(facts) < 2 or self._llm is None or context.llm_calls_used >= context.llm_calls_cap:
            return []

        try:
            embeddings = await self._embeddings.embed_batch([fact.text for fact in facts])
        except Exception:
            logger.warning("Embedding batch failed during conflict candidate recall", exc_info=True)
            return []

        if len(embeddings) != len(facts):
            logger.warning("Conflict embedding count mismatch: %d vs %d facts", len(embeddings), len(facts))
            return []

        conflicts: list[FactConflict] = []
        for index_a, fact_a in enumerate(facts):
            for index_b in range(index_a + 1, len(facts)):
                if context.llm_calls_used >= context.llm_calls_cap:
                    logger.warning("LLM cap reached while classifying fact conflicts")
                    return conflicts

                fact_b = facts[index_b]
                if self._normalize(fact_a.text) == self._normalize(fact_b.text):
                    continue
                if _cosine_similarity(embeddings[index_a], embeddings[index_b]) < self._threshold:
                    continue

                context.llm_calls_used += 1
                classification = await self._classify(fact_a.text, fact_b.text)
                if classification is None or not classification.is_conflict:
                    continue

                description = classification.description.strip()
                if not description:
                    continue
                conflicts.append(FactConflict(
                    fact_a_id=fact_a.id,
                    fact_b_id=fact_b.id,
                    description=description,
                    conflict_type=classification.conflict_type,
                ))

        logger.info(
            "Conflict detection: %d facts produced %d conflicts (gateway=%s)",
            len(facts),
            len(conflicts),
            gateway_id,
        )
        return conflicts

    async def _classify(self, fact_a: str, fact_b: str) -> _ConflictClassification | None:
        if self._llm is None:
            return None
        try:
            result = await self._llm.complete_json(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=f"Fact A: {fact_a}\nFact B: {fact_b}",
                max_tokens=200,
                json_schema=_CONFLICT_SCHEMA,
            )
            return _ConflictClassification.model_validate(result)
        except Exception:
            logger.warning("LLM conflict classification failed", exc_info=True)
            return None

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.casefold().split())
