"""ProcedureAuditStore — SQLite audit trail for procedure compliance."""
from __future__ import annotations

import logging
import json
import sqlite3
import uuid
from datetime import UTC, datetime

logger = logging.getLogger("elephantbroker.runtime.audit.procedure_audit")


class ProcedureAuditStore:
    """Append-only SQLite audit for procedure lifecycle events."""

    def __init__(self, db_path: str = "data/procedure_audit.db", enabled: bool = True) -> None:
        self._db_path = db_path
        self._enabled = enabled
        self._conn: sqlite3.Connection | None = None

    async def init_db(self) -> None:
        if not self._enabled:
            return
        import os
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute('''
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
        ''')
        for column in ("action_id", "actor_id", "approval_request_id", "lineage_refs", "gateway_id"):
            try:
                self._conn.execute(f"ALTER TABLE procedure_events ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        self._conn.commit()

    async def record_event(
        self, session_key: str, session_id: str,
        procedure_id: str, procedure_name: str,
        event_type: str, *,
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
        if not self._enabled or not self._conn:
            return
        try:
            self._conn.execute(
                '''INSERT INTO procedure_events
                   (event_id, session_key, session_id, procedure_id, procedure_name,
                    execution_id, event_type, step_id, step_instruction, proof_type,
                    proof_value, action_id, actor_id, approval_request_id, lineage_refs,
                    timestamp, gateway_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    str(uuid.uuid4()), session_key, session_id,
                    procedure_id, procedure_name, execution_id,
                    event_type, step_id, step_instruction,
                    proof_type, proof_value, action_id, actor_id,
                    approval_request_id, json.dumps(lineage_refs or []),
                    datetime.now(UTC).isoformat(), gateway_id,
                ),
            )
            self._conn.commit()
        except Exception as exc:
            logger.warning("Failed to record procedure audit event: %s", exc)

    async def get_session_events(self, session_key: str, session_id: str) -> list[dict[str, object]]:
        if not self._enabled or not self._conn:
            return []
        cursor = self._conn.execute(
            'SELECT * FROM procedure_events WHERE session_key=? AND session_id=? ORDER BY timestamp',
            (session_key, session_id),
        )
        cols = [d[0] for d in cursor.description]
        return [self._row_to_event(cols, row) for row in cursor.fetchall()]

    async def get_procedure_events(self, procedure_id: str) -> list[dict[str, object]]:
        if not self._enabled or not self._conn:
            return []
        cursor = self._conn.execute(
            'SELECT * FROM procedure_events WHERE procedure_id=? ORDER BY timestamp',
            (procedure_id,),
        )
        cols = [d[0] for d in cursor.description]
        return [self._row_to_event(cols, row) for row in cursor.fetchall()]

    def _row_to_event(self, cols: list[str], row: tuple[object, ...]) -> dict[str, object]:
        event: dict[str, object] = dict(zip(cols, row))
        if "lineage_refs" in event:
            raw_lineage_refs = event["lineage_refs"]
            event["lineage_refs"] = json.loads(raw_lineage_refs or "[]") if isinstance(raw_lineage_refs, str) else []
        return event

    async def cleanup_old(self, retention_days: int = 90) -> int:
        """Delete events older than retention_days. Returns deleted count."""
        if not self._enabled or not self._conn:
            return 0
        from datetime import timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        try:
            cursor = self._conn.execute(
                "DELETE FROM procedure_events WHERE timestamp < ?", (cutoff,),
            )
            self._conn.commit()
            return cursor.rowcount
        except Exception as exc:
            logger.warning("Failed to cleanup old procedure events: %s", exc)
            return 0

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
