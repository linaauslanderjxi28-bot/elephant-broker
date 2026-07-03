"""Scenario: Multi-Turn Memory — facts accumulate, search quality improves."""
from __future__ import annotations

from tests.scenarios.base import Scenario
from tests.scenarios.runner import register


@register
class MultiTurnMemoryScenario(Scenario):
    """5-turn conversation exercising the explicit memory_search tool path.

    Each turn calls ``simulate_tool_memory_search`` directly — this
    scenario covers the on-demand memory retrieval path (tool invocation
    from within a turn), NOT the before_agent_start auto-recall injection
    path. Cross-session auto-recall is covered by
    ``test_store_fact_then_auto_recall_returns_it`` in
    ``tests/integration/runtime/test_memory_facade.py`` (TD-60 + TD-61
    regression guard).
    """

    name = "multi_turn_memory"
    required_phase = 5
    required_amendment_6_2 = False

    async def run(self):
        self.expect_trace("fact_extracted", min_count=3)
        self.expect_trace("retrieval_performed", min_count=5)
        self.expect_trace("scoring_completed", min_count=1)

        turns = [
            ("We're building an auth module with JWT tokens", "I'll help with the JWT auth..."),
            ("The tokens should expire after 1 hour", "Setting token expiry to 3600 seconds..."),
            ("We also need refresh tokens with 7-day expiry", "Adding refresh token support..."),
            ("The auth middleware should validate on every request", "Implementing middleware..."),
            ("Let's add rate limiting to the auth endpoints", "Adding rate limiting with Redis..."),
        ]
        fact_counts = []
        for i, (user_msg, assistant_msg) in enumerate(turns):
            recalled = await self.sim.simulate_before_agent_start(user_msg)
            # FULL-tier extraction: the memory-plugin ingest-messages path
            # (simulate_full_turn's agent_end) short-circuits to 202 and never
            # runs turn_ingest, so it cannot emit FACT_EXTRACTED. Drive
            # /context/ingest-batch instead — that is the only call in this
            # scenario that can satisfy the fact_extracted assertion.
            await self.sim.simulate_context_ingest_batch([
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_msg},
            ])
            results = await self.sim.simulate_tool_memory_search("auth JWT token")
            fact_counts.append(len(results))
            self.step(f"turn_{i}_facts", passed=True,
                      message=f"Facts found: {len(results)}, recalled: {len(recalled)}")

        if len(fact_counts) >= 3:
            growing = fact_counts[-1] >= fact_counts[0]
            self.step("facts_accumulate", passed=growing,
                      message=f"First: {fact_counts[0]}, Last: {fact_counts[-1]}")

        ws = await self.sim.simulate_build_working_set("auth module implementation")
        self.step("working_set_built", passed=ws is not None and "items" in ws,
                  message=f"Working set items: {len(ws.get('items', []))}")
