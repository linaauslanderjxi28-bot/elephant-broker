"""PostgreSQL-backed runtime structured stores."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from pydantic import BaseModel, ValidationError

from elephantbroker.schemas.consolidation import ConsolidationReport
from elephantbroker.runtime.consolidation.report_store import _row_to_report
from elephantbroker.schemas.profile import ProfilePolicy

logger = logging.getLogger(__name__)


def _deleted_count(command_tag: str) -> int:
    try:
        return int(command_tag.rsplit(" ", 1)[-1])
    except (IndexError, ValueError):
        return 0


async def _pool_from_dsn(pool: asyncpg.Pool | None, dsn: str) -> asyncpg.Pool | None:
    if pool is not None:
        return pool
    if not dsn:
        return None
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)


class _PoolBackedStore:
    def __init__(self, pool: asyncpg.Pool | None = None, dsn: str = "") -> None:
        self._pool = pool
        self._dsn = dsn
        self._owns_pool = pool is None

    async def close(self) -> None:
        if self._pool and self._owns_pool:
            await self._pool.close()
        self._pool = None


class PostgresProcedureAuditStore(_PoolBackedStore):
    def __init__(self, pool: asyncpg.Pool | None = None, dsn: str = "", enabled: bool = True) -> None:
        super().__init__(pool=pool, dsn=dsn)
        self._enabled = enabled

    async def init_db(self) -> None:
        if not self._enabled:
            return
        self._pool = await _pool_from_dsn(self._pool, self._dsn)
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS procedure_events (
                    event_id TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    procedure_id TEXT NOT NULL,
                    procedure_name TEXT NOT NULL,
                    execution_id TEXT,
                    event_type TEXT NOT NULL,
                    step_id TEXT,
                    step_instruction TEXT,
                    proof_type TEXT,
                    proof_value TEXT,
                    action_id TEXT,
                    actor_id TEXT,
                    approval_request_id TEXT,
                    lineage_refs TEXT,
                    timestamp TEXT NOT NULL,
                    gateway_id TEXT NOT NULL DEFAULT ''
                )
            """)
            for column in ("action_id", "actor_id", "approval_request_id", "lineage_refs", "gateway_id"):
                await conn.execute(f"ALTER TABLE procedure_events ADD COLUMN IF NOT EXISTS {column} TEXT")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_procedure_events_session "
                "ON procedure_events (session_key, session_id, timestamp)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_procedure_events_procedure "
                "ON procedure_events (procedure_id, timestamp)"
            )

    async def record_event(
        self,
        session_key: str,
        session_id: str,
        procedure_id: str,
        procedure_name: str,
        event_type: str,
        *,
        execution_id: str | None = None,
        step_id: str | None = None,
        step_instruction: str | None = None,
        proof_type: str | None = None,
        proof_value: str | None = None,
        action_id: str | None = None,
        actor_id: str | None = None,
        approval_request_id: str | None = None,
        lineage_refs: list[str] | None = None,
        gateway_id: str = "",
    ) -> None:
        if not self._enabled or not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO procedure_events
                   (event_id, session_key, session_id, procedure_id, procedure_name,
                    execution_id, event_type, step_id, step_instruction, proof_type,
                    proof_value, action_id, actor_id, approval_request_id, lineage_refs,
                    timestamp, gateway_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)""",
                str(uuid.uuid4()), session_key, session_id, procedure_id, procedure_name,
                execution_id, event_type, step_id, step_instruction, proof_type,
                proof_value, action_id, actor_id, approval_request_id,
                json.dumps(lineage_refs or []), datetime.now(UTC).isoformat(), gateway_id,
            )

    async def get_session_events(self, session_key: str, session_id: str) -> list[dict[str, Any]]:
        if not self._enabled or not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM procedure_events WHERE session_key=$1 AND session_id=$2 ORDER BY timestamp",
                session_key, session_id,
            )
        return [self._row_to_event(row) for row in rows]

    async def get_procedure_events(self, procedure_id: str) -> list[dict[str, Any]]:
        if not self._enabled or not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM procedure_events WHERE procedure_id=$1 ORDER BY timestamp",
                procedure_id,
            )
        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: asyncpg.Record) -> dict[str, Any]:
        event = dict(row)
        event["lineage_refs"] = json.loads(event.get("lineage_refs") or "[]")
        return event

    async def cleanup_old(self, retention_days: int = 90) -> int:
        if not self._enabled or not self._pool:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        async with self._pool.acquire() as conn:
            tag = await conn.execute("DELETE FROM procedure_events WHERE timestamp < $1", cutoff)
        return _deleted_count(tag)


class PostgresSessionGoalAuditStore(_PoolBackedStore):
    def __init__(self, pool: asyncpg.Pool | None = None, dsn: str = "", enabled: bool = True) -> None:
        super().__init__(pool=pool, dsn=dsn)
        self._enabled = enabled

    async def init_db(self) -> None:
        if not self._enabled:
            return
        self._pool = await _pool_from_dsn(self._pool, self._dsn)
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS goal_events (
                    event_id TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    goal_title TEXT NOT NULL,
                    parent_goal_id TEXT,
                    event_type TEXT NOT NULL,
                    evidence TEXT,
                    timestamp TEXT NOT NULL,
                    gateway_id TEXT NOT NULL DEFAULT ''
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_goal_events_session "
                "ON goal_events (session_key, session_id, timestamp)"
            )

    async def record_event(
        self,
        session_key: str,
        session_id: str,
        goal_id: str,
        goal_title: str,
        event_type: str,
        *,
        parent_goal_id: str | None = None,
        evidence: str | None = None,
        gateway_id: str = "",
    ) -> None:
        if not self._enabled or not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO goal_events
                   (event_id, session_key, session_id, goal_id, goal_title,
                    parent_goal_id, event_type, evidence, timestamp, gateway_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                str(uuid.uuid4()), session_key, session_id, goal_id, goal_title,
                parent_goal_id, event_type, evidence, datetime.now(UTC).isoformat(), gateway_id,
            )

    async def get_session_events(self, session_key: str, session_id: str) -> list[dict[str, Any]]:
        if not self._enabled or not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM goal_events WHERE session_key=$1 AND session_id=$2 ORDER BY timestamp",
                session_key, session_id,
            )
        return [dict(row) for row in rows]

    async def cleanup_old(self, retention_days: int = 90) -> int:
        if not self._enabled or not self._pool:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        async with self._pool.acquire() as conn:
            tag = await conn.execute("DELETE FROM goal_events WHERE timestamp < $1", cutoff)
        return _deleted_count(tag)


class PostgresOrgOverrideStore(_PoolBackedStore):
    async def init_db(self) -> None:
        self._pool = await _pool_from_dsn(self._pool, self._dsn)
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS org_profile_overrides (
                    org_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    overrides_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by_actor_id TEXT,
                    PRIMARY KEY (org_id, profile_id)
                )
            """)

    async def get_override(self, org_id: str, profile_id: str) -> dict[str, Any] | None:
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT overrides_json FROM org_profile_overrides WHERE org_id=$1 AND profile_id=$2",
                org_id, profile_id,
            )
        return json.loads(row["overrides_json"]) if row else None

    async def set_override(
        self,
        org_id: str,
        profile_id: str,
        overrides: dict[str, Any],
        actor_id: str | None = None,
    ) -> None:
        if not self._pool:
            raise RuntimeError("PostgresOrgOverrideStore not initialized — call init_db() first")
        for key in overrides:
            if key not in ProfilePolicy.model_fields:
                raise ValueError(f"Unknown override key: {key!r} (not a ProfilePolicy field)")
        for key, value in overrides.items():
            if isinstance(value, dict):
                field_type = ProfilePolicy.model_fields[key].annotation
                if isinstance(field_type, type) and issubclass(field_type, BaseModel):
                    for nested_key in value:
                        if nested_key not in field_type.model_fields:
                            raise ValueError(f"Unknown nested override key: {key}.{nested_key!r}")
                    try:
                        field_type.model_validate(field_type().model_dump() | value)
                    except ValidationError as exc:
                        raise ValueError(f"Invalid override value for {key}: {exc}") from exc
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO org_profile_overrides
                   (org_id, profile_id, overrides_json, updated_at, updated_by_actor_id)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (org_id, profile_id) DO UPDATE SET
                       overrides_json = EXCLUDED.overrides_json,
                       updated_at = EXCLUDED.updated_at,
                       updated_by_actor_id = EXCLUDED.updated_by_actor_id""",
                org_id, profile_id, json.dumps(overrides), datetime.now(UTC).isoformat(), actor_id,
            )

    async def delete_override(self, org_id: str, profile_id: str) -> None:
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM org_profile_overrides WHERE org_id=$1 AND profile_id=$2",
                org_id, profile_id,
            )

    async def list_overrides(self, org_id: str) -> list[dict[str, Any]]:
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT profile_id, overrides_json, updated_at, updated_by_actor_id
                   FROM org_profile_overrides WHERE org_id=$1 ORDER BY profile_id""",
                org_id,
            )
        return [
            {
                "profile_id": row["profile_id"],
                "overrides": json.loads(row["overrides_json"]),
                "updated_at": row["updated_at"],
                "updated_by_actor_id": row["updated_by_actor_id"],
            }
            for row in rows
        ]


class PostgresConsolidationReportStore(_PoolBackedStore):
    async def init_db(self) -> None:
        self._pool = await _pool_from_dsn(self._pool, self._dsn)
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS consolidation_reports (
                    report_id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    gateway_id TEXT NOT NULL,
                    profile_id TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    summary_json TEXT,
                    stages_json TEXT,
                    error TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS procedure_suggestions (
                    id TEXT PRIMARY KEY,
                    report_id TEXT,
                    gateway_id TEXT NOT NULL,
                    pattern_description TEXT NOT NULL,
                    tool_sequence_json TEXT NOT NULL,
                    sessions_observed INTEGER NOT NULL DEFAULT 0,
                    draft_procedure_json TEXT,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    approval_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                )
            """)

    async def save_report(self, report: ConsolidationReport) -> None:
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO consolidation_reports
                   (report_id, org_id, gateway_id, profile_id, started_at,
                    completed_at, status, summary_json, stages_json, error)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   ON CONFLICT (report_id) DO UPDATE SET
                       org_id = EXCLUDED.org_id,
                       gateway_id = EXCLUDED.gateway_id,
                       profile_id = EXCLUDED.profile_id,
                       started_at = EXCLUDED.started_at,
                       completed_at = EXCLUDED.completed_at,
                       status = EXCLUDED.status,
                       summary_json = EXCLUDED.summary_json,
                       stages_json = EXCLUDED.stages_json,
                       error = EXCLUDED.error""",
                report.id, report.org_id, report.gateway_id, report.profile_id,
                report.started_at.isoformat(),
                report.completed_at.isoformat() if report.completed_at else None,
                report.status,
                report.summary.model_dump_json() if report.summary else None,
                json.dumps([stage.model_dump(mode="json") for stage in report.stage_results]),
                report.error,
            )

    async def get_report(self, report_id: str) -> ConsolidationReport | None:
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM consolidation_reports WHERE report_id=$1", report_id)
        return _row_to_report(dict(row)) if row else None

    async def list_reports(self, gateway_id: str, limit: int = 10) -> list[ConsolidationReport]:
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM consolidation_reports WHERE gateway_id=$1 ORDER BY started_at DESC LIMIT $2",
                gateway_id, limit,
            )
        return [_row_to_report(dict(row)) for row in rows]

    async def save_suggestion(self, suggestion_dict: dict[str, Any]) -> None:
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO procedure_suggestions
                   (id, report_id, gateway_id, pattern_description, tool_sequence_json,
                    sessions_observed, draft_procedure_json, confidence, approval_status, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   ON CONFLICT (id) DO UPDATE SET
                       report_id = EXCLUDED.report_id,
                       gateway_id = EXCLUDED.gateway_id,
                       pattern_description = EXCLUDED.pattern_description,
                       tool_sequence_json = EXCLUDED.tool_sequence_json,
                       sessions_observed = EXCLUDED.sessions_observed,
                       draft_procedure_json = EXCLUDED.draft_procedure_json,
                       confidence = EXCLUDED.confidence,
                       approval_status = EXCLUDED.approval_status,
                       created_at = EXCLUDED.created_at""",
                str(suggestion_dict.get("id", "")),
                suggestion_dict.get("report_id", ""),
                suggestion_dict.get("gateway_id", ""),
                suggestion_dict.get("pattern_description", ""),
                json.dumps(suggestion_dict.get("tool_sequence", [])),
                suggestion_dict.get("sessions_observed", 0),
                json.dumps(suggestion_dict.get("draft_procedure")) if suggestion_dict.get("draft_procedure") else None,
                suggestion_dict.get("confidence", 0.5),
                suggestion_dict.get("approval_status", "pending"),
                suggestion_dict.get("created_at", datetime.now(UTC).isoformat()),
            )

    async def list_suggestions(self, gateway_id: str, approval_status: str | None = None) -> list[dict[str, Any]]:
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            if approval_status:
                rows = await conn.fetch(
                    """SELECT * FROM procedure_suggestions
                       WHERE gateway_id=$1 AND approval_status=$2 ORDER BY created_at DESC""",
                    gateway_id, approval_status,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM procedure_suggestions WHERE gateway_id=$1 ORDER BY created_at DESC",
                    gateway_id,
                )
        return [dict(row) for row in rows]

    async def update_suggestion_status(self, suggestion_id: str, status: str) -> bool:
        if not self._pool:
            return False
        async with self._pool.acquire() as conn:
            tag = await conn.execute(
                "UPDATE procedure_suggestions SET approval_status=$1 WHERE id=$2",
                status, suggestion_id,
            )
        return _deleted_count(tag) > 0

    async def cleanup_old(self, retention_days: int = 90) -> int:
        if not self._pool:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        async with self._pool.acquire() as conn:
            reports = await conn.execute("DELETE FROM consolidation_reports WHERE started_at < $1", cutoff)
            suggestions = await conn.execute("DELETE FROM procedure_suggestions WHERE created_at < $1", cutoff)
        return _deleted_count(reports) + _deleted_count(suggestions)


class PostgresTuningDeltaStore(_PoolBackedStore):
    async def init_db(self) -> None:
        self._pool = await _pool_from_dsn(self._pool, self._dsn)
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tuning_deltas (
                    id BIGSERIAL PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    gateway_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    accumulated_delta DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    last_raw_delta DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    cycle_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(profile_id, org_id, gateway_id, dimension)
                )
            """)
            await conn.execute("ALTER TABLE tuning_deltas ALTER COLUMN accumulated_delta TYPE DOUBLE PRECISION")
            await conn.execute("ALTER TABLE tuning_deltas ALTER COLUMN last_raw_delta TYPE DOUBLE PRECISION")

    async def get_deltas(self, profile_id: str, org_id: str, gateway_id: str) -> dict[str, float]:
        if not self._pool:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT dimension, accumulated_delta FROM tuning_deltas
                   WHERE profile_id=$1 AND org_id=$2 AND gateway_id=$3""",
                profile_id, org_id, gateway_id,
            )
        return {row["dimension"]: row["accumulated_delta"] for row in rows}

    async def upsert_delta(
        self,
        profile_id: str,
        org_id: str,
        gateway_id: str,
        dimension: str,
        smoothed_delta: float,
        raw_delta: float,
    ) -> None:
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO tuning_deltas
                   (profile_id, org_id, gateway_id, dimension,
                    accumulated_delta, last_raw_delta, cycle_count, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, 1, $7)
                   ON CONFLICT(profile_id, org_id, gateway_id, dimension)
                   DO UPDATE SET
                       accumulated_delta = EXCLUDED.accumulated_delta,
                       last_raw_delta = EXCLUDED.last_raw_delta,
                       cycle_count = tuning_deltas.cycle_count + 1,
                       updated_at = EXCLUDED.updated_at""",
                profile_id, org_id, gateway_id, dimension,
                smoothed_delta, raw_delta, datetime.now(UTC).isoformat(),
            )

    async def clear_gateway(self, org_id: str, gateway_id: str) -> int:
        if not self._pool:
            return 0
        async with self._pool.acquire() as conn:
            tag = await conn.execute(
                "DELETE FROM tuning_deltas WHERE org_id=$1 AND gateway_id=$2",
                org_id, gateway_id,
            )
        return _deleted_count(tag)


class PostgresScoringLedgerStore(_PoolBackedStore):
    async def init_db(self) -> None:
        self._pool = await _pool_from_dsn(self._pool, self._dsn)
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS scoring_ledger (
                    id BIGSERIAL PRIMARY KEY,
                    fact_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    gateway_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    dim_scores_json TEXT NOT NULL,
                    was_selected BOOLEAN NOT NULL,
                    successful_use_count_at_scoring INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scoring_ledger_gw ON scoring_ledger (gateway_id, created_at)"
            )

    async def write_batch(self, entries: list[dict[str, Any]]) -> None:
        if not self._pool or not entries:
            return
        now = datetime.now(UTC).isoformat()
        rows = [
            (
                entry["fact_id"], entry["session_id"], entry["session_key"],
                entry["gateway_id"], entry["profile_id"],
                entry["dim_scores_json"] if isinstance(entry["dim_scores_json"], str)
                else json.dumps(entry["dim_scores_json"]),
                bool(entry["was_selected"]),
                entry.get("successful_use_count_at_scoring", 0),
                entry.get("created_at", now),
            )
            for entry in entries
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO scoring_ledger
                   (fact_id, session_id, session_key, gateway_id, profile_id,
                    dim_scores_json, was_selected, successful_use_count_at_scoring, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                rows,
            )

    async def query_for_correlation(self, gateway_id: str, cutoff_hours: int = 48) -> list[dict[str, Any]]:
        if not self._pool:
            return []
        cutoff = (datetime.now(UTC) - timedelta(hours=cutoff_hours)).isoformat()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM scoring_ledger WHERE gateway_id=$1 AND created_at > $2 ORDER BY created_at",
                gateway_id, cutoff,
            )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["dim_scores"] = json.loads(item.get("dim_scores_json") or "{}")
            except json.JSONDecodeError:
                item["dim_scores"] = {}
            result.append(item)
        return result

    async def cleanup_old(self, retention_seconds: int = 172800) -> int:
        if not self._pool:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(seconds=retention_seconds)).isoformat()
        async with self._pool.acquire() as conn:
            tag = await conn.execute("DELETE FROM scoring_ledger WHERE created_at < $1", cutoff)
        return _deleted_count(tag)
