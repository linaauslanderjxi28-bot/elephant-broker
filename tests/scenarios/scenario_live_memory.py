"""Scenario: Live Memory — drive real gateway traffic, assert EB traces (PT-4).

Unlike the simulation-mode scenarios (which POST directly to the EB API via
``OpenClawGatewaySimulator``), this scenario sends user messages through a real
running OpenClaw gateway using ``LiveScenario`` / ``OpenClawClient``. The full
TS plugin -> EB runtime lifecycle fires, and we then verify that EB actually
produced ``fact_extracted`` and ``retrieval_performed`` trace events for the
gateway-routed session.

Requires a running OpenClaw gateway + EB runtime + infrastructure. Because its
constructor needs live gateway credentials (which the plain ``run_all_scenarios``
path cannot supply), this scenario is intentionally NOT registered in the global
``SCENARIOS`` registry — run it directly:

    python -m tests.scenarios.scenario_live_memory \\
        --gateway-url ws://localhost:18789 --gateway-token <token>
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

from tests.scenarios.live_adapter import LiveScenario


class LiveMemoryScenario(LiveScenario):
    """Send messages through a real OpenClaw gateway; assert EB memory traces.

    Drives a short memory turn (store two facts, then ask a recall question) and
    checks the EB trace ledger — queried by the live ``session_key`` — for the
    ``fact_extracted`` and ``retrieval_performed`` events the plugin lifecycle
    should emit.
    """

    name = "live_memory"
    required_phase = 4

    # Seconds to wait after the last turn for EB's async ingest/extraction to
    # land trace events before we query the ledger.
    _TRACE_SETTLE_SECONDS = 5.0

    async def run(self) -> None:
        # Drive a memory turn through the real gateway.
        messages = [
            "Remember that our primary datastore is PostgreSQL.",
            "Also note that we deploy the runtime on Kubernetes in production.",
            "What datastore do we use?",
        ]
        for i, msg in enumerate(messages):
            resp = await self.send_user_message(msg)
            self.step(
                f"gateway_turn_{i}",
                passed=bool(resp),
                message=f"Sent through gateway: {msg[:48]}",
            )

        # Give EB's async extraction/retrieval a moment to record trace events.
        await asyncio.sleep(self._TRACE_SETTLE_SECONDS)

        # Verify the expected trace events landed for the live session_key.
        counts = await self._trace_event_counts(
            ["fact_extracted", "retrieval_performed"]
        )
        self.step(
            "fact_extracted",
            passed=counts.get("fact_extracted", 0) >= 1,
            message=f"fact_extracted={counts.get('fact_extracted', 0)}",
        )
        self.step(
            "retrieval_performed",
            passed=counts.get("retrieval_performed", 0) >= 1,
            message=f"retrieval_performed={counts.get('retrieval_performed', 0)}",
        )

    async def _trace_event_counts(self, event_types: list[str]) -> dict[str, int]:
        """Query EB /trace/query by the live session_key; tally event types.

        Uses a dedicated gateway-scoped httpx client (the base ``self.sim``
        client tracks a different, simulated session) so the query matches the
        traces produced by the real gateway-routed session.
        """
        counts: dict[str, int] = {et: 0 for et in event_types}
        if not self._session_key:
            return counts

        headers = {"X-EB-Gateway-ID": self.gateway_id}
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=10.0, headers=headers
        ) as client:
            resp = await client.post(
                "/trace/query",
                json={
                    "session_key": self._session_key,
                    "event_types": event_types,
                    "limit": 10000,
                },
            )
            resp.raise_for_status()
            events = resp.json()

        for ev in events:
            et = ev.get("event_type")
            if et in counts:
                counts[et] += 1
        return counts


async def _amain(args: argparse.Namespace) -> int:
    scenario = LiveMemoryScenario(
        gateway_url=args.gateway_url,
        gateway_token=args.gateway_token,
        eb_runtime_url=args.base_url,
        agent_id=args.agent_id,
        gateway_id=args.gateway_id,
    )
    result = await scenario.execute()

    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.name}  (reward={result.reward_score:.2f})")
    for s in result.steps:
        icon = "+" if s.passed else "-"
        msg = f" — {s.message}" if s.message else ""
        print(f"  [{icon}] {s.name}{msg}")
    for err in result.errors:
        print(f"  ! {err}")

    return 0 if result.passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live gateway-routed memory scenario (PT-4)",
    )
    parser.add_argument(
        "--gateway-url", type=str,
        default=os.environ.get("EB_GATEWAY_WS_URL", "ws://localhost:18789"),
        help="OpenClaw gateway WebSocket URL",
    )
    parser.add_argument(
        "--gateway-token", type=str,
        default=os.environ.get("EB_GATEWAY_TOKEN", ""),
        help="OpenClaw gateway auth token",
    )
    parser.add_argument(
        "--base-url", type=str,
        default=os.environ.get("EB_BASE_URL", "http://localhost:8420"),
        help="Base URL of the ElephantBroker API",
    )
    parser.add_argument(
        "--agent-id", type=str,
        default=os.environ.get("EB_LIVE_AGENT_ID", "main"),
        help="Agent ID for the live session",
    )
    parser.add_argument(
        "--gateway-id", type=str,
        default=os.environ.get("EB_GATEWAY_ID", "local"),
        help="Gateway ID used to scope trace queries",
    )
    args = parser.parse_args()

    if not args.gateway_token:
        print(
            "ERROR: --gateway-token (or EB_GATEWAY_TOKEN) is required for live mode.",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
