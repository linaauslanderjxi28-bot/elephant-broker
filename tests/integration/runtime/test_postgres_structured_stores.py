from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

from elephantbroker.runtime.adapters.postgres.structured_stores import (
    PostgresConsolidationReportStore,
    PostgresOrgOverrideStore,
    PostgresProcedureAuditStore,
    PostgresScoringLedgerStore,
    PostgresSessionGoalAuditStore,
    PostgresTuningDeltaStore,
)
from elephantbroker.runtime.adapters.postgres.authority_store import PostgresAuthorityRuleStore
from elephantbroker.schemas.consolidation import ConsolidationReport


@pytest.mark.asyncio
async def test_postgres_structured_stores_round_trip() -> None:
    dsn = os.environ.get("EB_POSTGRES_DSN")
    if not dsn:
        pytest.skip("EB_POSTGRES_DSN is required for live Postgres structured-store coverage")

    run_id = uuid.uuid4().hex[:8]
    proc = PostgresProcedureAuditStore(dsn=dsn)
    goal = PostgresSessionGoalAuditStore(dsn=dsn)
    org = PostgresOrgOverrideStore(dsn=dsn)
    report = PostgresConsolidationReportStore(dsn=dsn)
    tuning = PostgresTuningDeltaStore(dsn=dsn)
    ledger = PostgresScoringLedgerStore(dsn=dsn)
    authority = PostgresAuthorityRuleStore(dsn=dsn)
    stores = [proc, goal, org, report, tuning, ledger, authority]

    try:
        for store in stores:
            await store.init_db()
        for store in stores:
            await store.init_db()

        await proc.record_event(f"sk-{run_id}", f"sid-{run_id}", f"proc-{run_id}", "Proc", "qualified")
        assert len(await proc.get_session_events(f"sk-{run_id}", f"sid-{run_id}")) == 1
        assert len(await proc.get_procedure_events(f"proc-{run_id}")) == 1

        await goal.record_event(f"sk-{run_id}", f"sid-{run_id}", f"goal-{run_id}", "Goal", "created")
        assert len(await goal.get_session_events(f"sk-{run_id}", f"sid-{run_id}")) == 1

        await org.set_override(
            f"org-{run_id}",
            "coding",
            {"session_data_ttl_seconds": 7200},
            actor_id="tester",
        )
        assert await org.get_override(f"org-{run_id}", "coding") == {"session_data_ttl_seconds": 7200}
        assert len(await org.list_overrides(f"org-{run_id}")) == 1
        await org.delete_override(f"org-{run_id}", "coding")
        assert await org.get_override(f"org-{run_id}", "coding") is None

        await authority.set_rule(f"action-{run_id}", {"min_authority_level": 42})
        assert await authority.get_rule(f"action-{run_id}") == {"min_authority_level": 42}
        rules = await authority.get_rules()
        assert rules[f"action-{run_id}"] == {"min_authority_level": 42}

        await report.save_report(
            ConsolidationReport(id=f"report-{run_id}", org_id=f"org-{run_id}", gateway_id=f"gw-{run_id}"),
        )
        loaded_report = await report.get_report(f"report-{run_id}")
        assert loaded_report is not None
        assert loaded_report.id == f"report-{run_id}"
        assert len(await report.list_reports(f"gw-{run_id}", limit=5)) >= 1
        await report.save_suggestion(
            {"id": f"sugg-{run_id}", "gateway_id": f"gw-{run_id}", "pattern_description": "pattern"},
        )
        assert len(await report.list_suggestions(f"gw-{run_id}", "pending")) == 1
        assert await report.update_suggestion_status(f"sugg-{run_id}", "approved") is True

        await tuning.upsert_delta("coding", f"org-{run_id}", f"gw-{run_id}", "recency", 0.2, 0.3)
        assert (await tuning.get_deltas("coding", f"org-{run_id}", f"gw-{run_id}"))["recency"] == 0.2
        assert await tuning.clear_gateway(f"org-{run_id}", f"gw-{run_id}") == 1

        await ledger.write_batch([
            {
                "fact_id": f"fact-{run_id}",
                "session_id": f"sid-{run_id}",
                "session_key": f"sk-{run_id}",
                "gateway_id": f"gw-{run_id}",
                "profile_id": "coding",
                "dim_scores_json": {"recency": 0.5},
                "was_selected": True,
                "created_at": datetime.now(UTC).isoformat(),
            },
        ])
        rows = await ledger.query_for_correlation(f"gw-{run_id}", cutoff_hours=1)
        assert any(
            row["fact_id"] == f"fact-{run_id}" and row["dim_scores"]["recency"] == 0.5
            for row in rows
        )
    finally:
        for store in stores:
            await store.close()
