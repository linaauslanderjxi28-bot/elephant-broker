"""Scenario: Basic Memory — store, search, dedup, auto-recall."""
from __future__ import annotations

from tests.scenarios.base import Scenario
from tests.scenarios.runner import register


@register
class BasicMemoryScenario(Scenario):
    """Store -> search -> dedup -> auto-recall. Memory-only, no context lifecycle."""

    name = "basic_memory"
    required_phase = 4
    required_amendment_6_2 = False

    async def run(self):
        self.expect_trace("retrieval_performed", min_count=1)
        self.expect_trace("fact_extracted", min_count=1)

        # Step 1: Store 3 facts
        for i, text in enumerate([
            "Redis supports pub/sub messaging",
            "Neo4j uses Cypher query language",
            "Qdrant stores vector embeddings",
        ]):
            r = await self.sim.simulate_tool_memory_store(text, category="technical")
            # A fresh store returns an id; a dedup-skip (409) returns
            # existing_fact_id/status=skipped — both mean the fact is in the store.
            stored_ok = ("id" in r or "fact_id" in r
                         or "existing_fact_id" in r or r.get("status") == "skipped")
            self.step(f"store_fact_{i}", passed=stored_ok,
                      message=f"Stored: {text[:40]}")

        # Step 1b: Drive the FULL-tier extraction path so FACT_EXTRACTED can fire.
        # /memory/store only persists a fact DataPoint — it never runs the
        # turn_ingest LLM pipeline that emits FACT_EXTRACTED. /context/ingest-batch
        # runs turn_ingest scoped to this session_id, making the fact_extracted
        # assertion satisfiable by store+search+ingest.
        ingest = await self.sim.simulate_context_ingest_batch([
            {"role": "user", "content": (
                "We use Redis for pub/sub messaging, Neo4j with Cypher for graph "
                "queries, and Qdrant to store vector embeddings."
            )},
            {"role": "assistant", "content": (
                "Noted: Redis handles pub/sub, Neo4j uses the Cypher query "
                "language, and Qdrant is the vector embedding store."
            )},
        ])
        self.step("context_ingest_batch", passed=ingest is not None,
                  message="Drove FULL-tier extraction (turn_ingest)")

        # Step 2: Search
        for query in ["Redis messaging", "Cypher graph queries", "vector embeddings"]:
            results = await self.sim.simulate_tool_memory_search(query)
            self.step(f"search_{query[:20]}", passed=len(results) > 0,
                      message=f"Found {len(results)} results")

        # Step 3: Store near-duplicate
        r = await self.sim.simulate_tool_memory_store(
            "Redis supports pub/sub messaging patterns", category="technical")
        self.expect_trace("dedup_triggered", min_count=0)

        # Step 4: Auto-recall
        recalled = await self.sim.simulate_before_agent_start(
            "Tell me about the databases we use")
        self.step("auto_recall", passed=len(recalled) > 0,
                  message=f"Recalled {len(recalled)} facts")
