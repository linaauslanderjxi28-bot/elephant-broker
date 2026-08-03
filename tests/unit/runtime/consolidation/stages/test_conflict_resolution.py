from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

from elephantbroker.runtime.consolidation.stages.conflict_resolution import ConflictResolutionStage
from elephantbroker.schemas.consolidation import ConsolidationConfig, ConsolidationContext
from tests.fixtures.factories import make_fact_assertion


def _make_context(
    *,
    llm_calls_used: int = 0,
    llm_calls_cap: int = 50,
) -> ConsolidationContext:
    return ConsolidationContext(
        org_id="org-1",
        gateway_id="gw-1",
        llm_calls_used=llm_calls_used,
        llm_calls_cap=llm_calls_cap,
    )


def _make_stage(
    *,
    threshold: float = 0.8,
    llm_result: dict[str, bool | str] | None = None,
) -> tuple[ConflictResolutionStage, AsyncMock, AsyncMock]:
    embeddings = AsyncMock()
    llm = AsyncMock()
    llm.complete_json.return_value = llm_result or {
        "is_conflict": True,
        "description": "The two facts assert incompatible preferred editors.",
        "conflict_type": "semantic",
    }
    config = ConsolidationConfig(conflict_similarity_threshold=threshold)
    return ConflictResolutionStage(embeddings, llm, config), embeddings, llm


class TestConflictResolutionStage:
    async def test_returns_empty_without_enough_facts_or_llm(self) -> None:
        stage, embeddings, _ = _make_stage()
        fact = make_fact_assertion(text="Uses Vim")

        assert await stage.run([fact], "gw-1", _make_context()) == []

        no_llm_stage = ConflictResolutionStage(
            embeddings,
            None,
            ConsolidationConfig(conflict_similarity_threshold=0.8),
        )
        assert await no_llm_stage.run([fact, make_fact_assertion(text="Uses Emacs")], "gw-1", _make_context()) == []
        embeddings.embed_batch.assert_not_awaited()

    async def test_classifies_recalled_pair_and_returns_conflict(self) -> None:
        stage, embeddings, llm = _make_stage()
        fact_a = make_fact_assertion(text="Alex prefers Vim")
        fact_b = make_fact_assertion(text="Alex prefers Emacs")
        embeddings.embed_batch.return_value = [[1.0, 0.0], [0.9, 0.1]]
        context = _make_context()

        conflicts = await stage.run([fact_a, fact_b], "gw-1", context)

        assert len(conflicts) == 1
        assert conflicts[0].fact_a_id == fact_a.id
        assert conflicts[0].fact_b_id == fact_b.id
        assert conflicts[0].conflict_type == "semantic"
        assert context.llm_calls_used == 1
        schema = llm.complete_json.await_args.kwargs["json_schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"is_conflict", "description", "conflict_type"}

    async def test_embedding_recall_skips_dissimilar_and_normalized_identical_pairs(self) -> None:
        stage, embeddings, llm = _make_stage(threshold=0.9)
        facts = [
            make_fact_assertion(text="  Uses   Vim "),
            make_fact_assertion(text="uses vim"),
            make_fact_assertion(text="Lives in Lisbon"),
        ]
        embeddings.embed_batch.return_value = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

        conflicts = await stage.run(facts, "gw-1", _make_context())

        assert conflicts == []
        llm.complete_json.assert_not_awaited()

    async def test_rejects_non_conflict_and_invalid_conflict_payloads(self) -> None:
        invalid_results = [
            {"is_conflict": False, "description": "not incompatible", "conflict_type": "semantic"},
            {"is_conflict": True, "description": "   ", "conflict_type": "semantic"},
            {"is_conflict": True, "description": "incompatible", "conflict_type": "invented"},
        ]
        facts = [make_fact_assertion(text="Prefers Vim"), make_fact_assertion(text="Prefers Emacs")]

        for llm_result in invalid_results:
            stage, embeddings, _ = _make_stage(llm_result=llm_result)
            embeddings.embed_batch.return_value = [[1.0, 0.0], [1.0, 0.0]]
            assert await stage.run(facts, "gw-1", _make_context()) == []

    async def test_stops_at_global_budget_and_counts_failed_logical_calls(self) -> None:
        stage, embeddings, llm = _make_stage(threshold=0.5)
        facts = [
            make_fact_assertion(text="Fact A"),
            make_fact_assertion(text="Fact B"),
            make_fact_assertion(text="Fact C"),
        ]
        embeddings.embed_batch.return_value = [[1.0, 0.0], [0.9, 0.1], [0.8, 0.2]]
        llm.complete_json.side_effect = RuntimeError("LLM unavailable")
        context = _make_context(llm_calls_used=1, llm_calls_cap=2)

        conflicts = await stage.run(facts, "gw-1", context)

        assert conflicts == []
        assert context.llm_calls_used == 2
        assert llm.complete_json.await_count == 1

    async def test_gracefully_handles_exhausted_budget_and_embedding_failures(self) -> None:
        facts = [make_fact_assertion(text="Fact A"), make_fact_assertion(text="Fact B")]
        stage, embeddings, llm = _make_stage()

        assert await stage.run(facts, "gw-1", _make_context(llm_calls_used=2, llm_calls_cap=2)) == []
        embeddings.embed_batch.assert_not_awaited()
        llm.complete_json.assert_not_awaited()

        embeddings.embed_batch.side_effect = RuntimeError("embedding unavailable")
        assert await stage.run(facts, "gw-1", _make_context()) == []

        embeddings.embed_batch.side_effect = None
        embeddings.embed_batch.return_value = [[1.0, 0.0]]
        assert await stage.run(facts, "gw-1", _make_context()) == []

    async def test_never_mutates_input_facts(self) -> None:
        stage, embeddings, _ = _make_stage()
        facts = [make_fact_assertion(text="Prefers Vim"), make_fact_assertion(text="Prefers Emacs")]
        original = deepcopy(facts)
        embeddings.embed_batch.return_value = [[1.0, 0.0], [1.0, 0.0]]

        _ = await stage.run(facts, "gw-1", _make_context())

        assert facts == original
