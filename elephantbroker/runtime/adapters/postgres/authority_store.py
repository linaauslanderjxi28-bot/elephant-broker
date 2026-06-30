"""PostgreSQL-backed persistence for configurable authority rules.

Mirrors the SQLite ``AuthorityRuleStore`` interface but stores rules
in the shared ``elephantbroker-structured-data`` PostgreSQL instance.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

AUTHORITY_DEFAULTS: dict[str, dict[str, Any]] = {
    "create_global_goal": {"min_authority_level": 90},
    "create_org_goal": {"min_authority_level": 70, "require_matching_org": True, "matching_exempt_level": 90},
    "create_team_goal": {"min_authority_level": 50, "require_matching_team": True, "matching_exempt_level": 70},
    "create_actor_goal": {"min_authority_level": 0, "require_self_ownership": True},
    "create_org": {"min_authority_level": 90},
    "create_team": {"min_authority_level": 70, "require_matching_org": True, "matching_exempt_level": 90},
    "add_team_member": {"min_authority_level": 50, "require_matching_team": True, "matching_exempt_level": 70},
    "remove_team_member": {"min_authority_level": 50, "require_matching_team": True, "matching_exempt_level": 70},
    "register_actor": {"min_authority_level": 70},
    "register_org_profile_override": {
        "min_authority_level": 70, "require_matching_org": True, "matching_exempt_level": 90,
    },
    "merge_actors": {"min_authority_level": 70},
    # -- Mutating route actions --
    "memory.store": {"min_authority_level": 0},
    "memory.update": {"min_authority_level": 30},
    "memory.delete": {"min_authority_level": 50},
    "claim.create": {"min_authority_level": 0},
    "claim.verify": {"min_authority_level": 50},
    "claim.reject": {"min_authority_level": 50},
    "procedure.activate": {"min_authority_level": 50},
    "procedure.complete_step": {"min_authority_level": 30},
    "consolidation.run": {"min_authority_level": 70},
    "consolidation.update_suggestion": {"min_authority_level": 70},
    "guard.approve": {"min_authority_level": 70},
}


class PostgresAuthorityRuleStore:
    """PostgreSQL persistence for system-wide authority rules."""

    def __init__(self, dsn: str = "") -> None:
        self._dsn = dsn
        self._pool = None

    async def init_db(self, pool=None) -> None:
        if pool is not None:
            self._pool = pool
            return
        if self._dsn:
            import asyncpg
            self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=4)
            logger.info("PostgresAuthorityRuleStore connected via DSN")
        else:
            logger.warning("No DSN provided — falling back to in-memory defaults only")

    async def get_rule(self, action: str) -> dict[str, Any]:
        if self._pool:
            try:
                async with self._pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT rule_json FROM authority_rules WHERE action = $1", action
                    )
                    if row is not None:
                        return json.loads(row["rule_json"])
            except Exception as exc:
                logger.warning("Authority rule DB lookup failed for %s: %s", action, exc)
        default = AUTHORITY_DEFAULTS.get(action)
        return dict(default) if default else {"min_authority_level": 90}

    async def set_rule(self, action: str, rule: dict[str, Any]) -> None:
        if not self._pool:
            raise RuntimeError("PostgresAuthorityRuleStore not initialised — call init_db() first")
        now = datetime.now(UTC)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO authority_rules (action, rule_json, updated_at)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (action) DO UPDATE SET
                       rule_json = EXCLUDED.rule_json,
                       updated_at = EXCLUDED.updated_at""",
                action, json.dumps(rule), now,
            )
        logger.info("Authority rule updated: %s → %s", action, rule)

    async def get_rules(self) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {
            action: dict(rule) for action, rule in AUTHORITY_DEFAULTS.items()
        }
        if self._pool:
            try:
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch("SELECT action, rule_json FROM authority_rules")
                    for row in rows:
                        merged[row["action"]] = json.loads(row["rule_json"])
            except Exception as exc:
                logger.warning("Authority rule list failed: %s", exc)
        return merged

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
