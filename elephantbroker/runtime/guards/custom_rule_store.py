"""SQLite-backed persistence for operator-defined guard rules (Phase 11 — TD-23).

Follows the same pattern as ``OrgOverrideStore`` and the Phase 5 audit stores:
a synchronous ``sqlite3.Connection`` held on ``self._conn``, ``async def`` method
wrappers around the sync sqlite calls, ``init_db()`` for table creation, and
``ON CONFLICT DO UPDATE`` for upsert.

Rows deserialize to the existing :class:`StaticRule` schema. Stored rules always
carry ``source="custom"`` so the :class:`~elephantbroker.runtime.guards.rules.StaticRuleRegistry`
can merge them alongside builtin, profile, and procedure rules. Every method is
gateway-scoped — the composite primary key is ``(gateway_id, rule_id)``.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime

from pydantic import ValidationError

from elephantbroker.schemas.guards import StaticRule

logger = logging.getLogger(__name__)

# Fields on ``StaticRule`` an operator may mutate via ``update_rule()``.
# ``id``, ``source``, and ``org_id`` are intentionally excluded — the rule id is
# the PK and source is forced to "custom".
_UPDATABLE_FIELDS = frozenset(
    {
        "pattern",
        "pattern_type",
        "outcome",
        "description",
        "enabled",
        "min_approval_authority",
    }
)


class CustomRuleStore:
    """SQLite persistence for operator-defined guard rules (Phase 11 — TD-23).

    Loaded alongside builtin + policy rules by ``StaticRuleRegistry.load_rules()``
    (``runtime/guards/rules.py``) — pass ``list_rules(gateway_id=...)`` results as
    an additional rule source there.

    Table schema::

        custom_guard_rules (
            gateway_id TEXT,
            rule_id TEXT,
            rule_json TEXT,      -- StaticRule.model_dump_json()
            updated_at TEXT,
            PRIMARY KEY (gateway_id, rule_id)
        )

        custom_guard_rules_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),  -- single row
            version INTEGER NOT NULL                -- monotonic change counter
        )

    ``custom_guard_rules_meta.version`` is bumped in the same transaction as
    every create/update/delete (FIX-4) so readers can detect ANY change —
    including deletes, which ``MAX(updated_at)`` over the rules table would
    miss — with a single-row probe via :meth:`get_rules_version`.
    """

    def __init__(self, db_path: str = "data/custom_guard_rules.db") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    async def init_db(self) -> None:
        """Create the tables if they don't exist."""
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS custom_guard_rules (
                gateway_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                rule_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (gateway_id, rule_id)
            )"""
        )
        # FIX-4: single-row version counter for cheap change detection. The
        # INSERT OR IGNORE seed migrates pre-existing DBs transparently — they
        # start at version 0 and bump on the next write.
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS custom_guard_rules_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL
            )"""
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO custom_guard_rules_meta (id, version) VALUES (1, 0)"
        )
        self._conn.commit()

    def _bump_version(self) -> None:
        """Increment the rules version counter (FIX-4).

        Must be called on the open connection BEFORE ``commit()`` so the bump
        lands in the same transaction as the rule write it accompanies.
        """
        self._conn.execute(
            "UPDATE custom_guard_rules_meta SET version = version + 1 WHERE id = 1"
        )

    async def get_rules_version(self) -> int:
        """Return the monotonic rules version (FIX-4).

        Single-row read — cheap enough to probe on the guard hot path. Returns
        ``0`` when the store is uninitialized or the meta row is unseeded.
        """
        if not self._conn:
            return 0
        cursor = self._conn.execute(
            "SELECT version FROM custom_guard_rules_meta WHERE id = 1"
        )
        row = cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def create_rule(self, *, gateway_id: str, rule: StaticRule) -> StaticRule:
        """Insert (or upsert) a custom rule.

        ``rule.id`` is the primary key within a gateway; ``rule.source`` is forced
        to ``"custom"`` regardless of the caller-supplied value.
        """
        if not self._conn:
            raise RuntimeError("CustomRuleStore not initialized — call init_db() first")

        stored = rule.model_copy(update={"source": "custom"})
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO custom_guard_rules (gateway_id, rule_id, rule_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (gateway_id, rule_id) DO UPDATE SET
                   rule_json = excluded.rule_json,
                   updated_at = excluded.updated_at""",
            (gateway_id, stored.id, stored.model_dump_json(), now),
        )
        self._bump_version()
        self._conn.commit()
        logger.info("Created custom guard rule id=%s gateway=%s", stored.id, gateway_id)
        return stored

    async def get_rule(self, *, gateway_id: str, rule_id: str) -> StaticRule | None:
        """Load a single custom rule. Returns ``None`` if not found."""
        if not self._conn:
            return None
        cursor = self._conn.execute(
            "SELECT rule_json FROM custom_guard_rules WHERE gateway_id = ? AND rule_id = ?",
            (gateway_id, rule_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._deserialize(row[0])

    async def list_rules(
        self, *, gateway_id: str, enabled_only: bool = False
    ) -> list[StaticRule]:
        """List all custom rules for a gateway, ordered by rule id.

        When ``enabled_only`` is true, disabled rules are filtered out.
        """
        if not self._conn:
            return []
        cursor = self._conn.execute(
            "SELECT rule_json FROM custom_guard_rules WHERE gateway_id = ? ORDER BY rule_id",
            (gateway_id,),
        )
        rules: list[StaticRule] = []
        for row in cursor.fetchall():
            rule = self._deserialize(row[0])
            if rule is None:
                continue
            if enabled_only and not rule.enabled:
                continue
            rules.append(rule)
        return rules

    async def update_rule(
        self, *, gateway_id: str, rule_id: str, updates: dict
    ) -> StaticRule | None:
        """Apply a whitelisted partial update to an existing rule.

        Only :data:`_UPDATABLE_FIELDS` may be changed; unknown keys are ignored.
        Returns the updated rule, or ``None`` if the rule does not exist. Raises
        ``ValueError`` if the resulting rule fails validation.
        """
        if not self._conn:
            return None
        existing = await self.get_rule(gateway_id=gateway_id, rule_id=rule_id)
        if existing is None:
            return None

        clean = {k: v for k, v in updates.items() if k in _UPDATABLE_FIELDS}
        if not clean:
            return existing

        try:
            updated = existing.model_copy(update=clean)
            # Re-validate through the model to reject bad types/values.
            updated = StaticRule.model_validate(updated.model_dump())
        except ValidationError as exc:
            raise ValueError(f"Invalid rule update: {exc}") from exc

        # Preserve invariants: id and source are immutable.
        updated = updated.model_copy(update={"id": rule_id, "source": "custom"})

        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "UPDATE custom_guard_rules SET rule_json = ?, updated_at = ? "
            "WHERE gateway_id = ? AND rule_id = ?",
            (updated.model_dump_json(), now, gateway_id, rule_id),
        )
        self._bump_version()
        self._conn.commit()
        logger.info("Updated custom guard rule id=%s gateway=%s", rule_id, gateway_id)
        return updated

    async def delete_rule(self, *, gateway_id: str, rule_id: str) -> bool:
        """Delete a custom rule. Returns ``True`` if a row was removed."""
        if not self._conn:
            return False
        cursor = self._conn.execute(
            "DELETE FROM custom_guard_rules WHERE gateway_id = ? AND rule_id = ?",
            (gateway_id, rule_id),
        )
        deleted = cursor.rowcount > 0
        # Deletes MUST bump the version (FIX-4) — a MAX(updated_at) probe over
        # the rules table would never see a removed row. No-op deletes don't.
        if deleted:
            self._bump_version()
        self._conn.commit()
        if deleted:
            logger.info("Deleted custom guard rule id=%s gateway=%s", rule_id, gateway_id)
        return deleted

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _deserialize(rule_json: str) -> StaticRule | None:
        """Parse a stored ``rule_json`` blob into a :class:`StaticRule`.

        Returns ``None`` (and logs) on corrupt rows so a single bad record cannot
        break a whole listing.
        """
        try:
            return StaticRule.model_validate_json(rule_json)
        except ValidationError as exc:
            logger.warning("Skipping corrupt custom guard rule row: %s", exc)
            return None
