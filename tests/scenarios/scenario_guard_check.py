"""Scenario: Guard Check — exercise guard pipeline, near-miss or guard pass."""
from __future__ import annotations

from tests.scenarios.base import Scenario
from tests.scenarios.runner import register


@register
class GuardCheckScenario(Scenario):
    """Exercise guard pipeline: trigger a near-miss or guard pass."""

    name = "guard_check"
    required_phase = 7
    required_amendment_6_2 = False

    async def run(self):
        self.expect_trace("guard_passed", min_count=0)
        self.expect_trace("constraint_reinjected", min_count=0)

        await self.sim.simulate_tool_memory_store(
            "User wants to execute shell commands on the server", "technical")

        session_id = str(self.sim.session_id)

        # Guard state is keyed by session_id and must be bootstrapped before the
        # read endpoints return 200. /sessions/start does NOT load guard rules;
        # POST /guards/refresh/{session_id} does (otherwise 404 SESSION_NOT_FOUND).
        refresh = await self.sim.client.post(f"/guards/refresh/{session_id}", json={
            "profile_name": "coding",
            "session_key": self.sim.session_key,
        })
        self.step("guard_session_bootstrapped", passed=refresh.status_code == 200,
                  message=f"refresh -> {refresh.status_code}")

        # Real route is GET /guards/active/{session_id} (session_id is a PATH
        # param). The old GET /guards/status never existed and correctly 404s.
        r = await self.sim.client.get(f"/guards/active/{session_id}")
        self.step("guard_status_accessible", passed=r.status_code == 200)

        # Real route is GET /guards/rules/{session_id}; it returns an object
        # {rules_count, rules:[...]}, NOT a bare list — read ['rules_count'].
        r = await self.sim.client.get(f"/guards/rules/{session_id}")
        rules_count = r.json().get("rules_count") if r.status_code == 200 else None
        self.step("constraints_listed", passed=r.status_code == 200,
                  message=f"Rules loaded: {rules_count if rules_count is not None else 'N/A'}")
