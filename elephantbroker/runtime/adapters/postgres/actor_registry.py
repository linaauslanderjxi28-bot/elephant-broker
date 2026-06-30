"""PostgreSQL-backed actor registry — replaces Neo4j for authority/identity data.

Keeps the same ``IActorRegistry`` interface but stores actor, org, team
data in the shared ``elephantbroker-structured-data`` PostgreSQL instance
instead of Neo4j.

Actor-to-actor relationships (REPORTS_TO, SUPERVISES, etc.) are stored
in a separate ``actor_relationships`` table rather than Neo4j edges.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TypeAlias

import asyncpg

from elephantbroker.runtime.adapters.cognee.datapoints import ActorDataPoint
from elephantbroker.runtime.interfaces.actor_registry import IActorRegistry
from elephantbroker.schemas.actor import (
    ActorRef,
    ActorRelationship,
    ActorType,
    RelationshipType,
)

logger = logging.getLogger(__name__)

ActorRowValue: TypeAlias = uuid.UUID | str | int | float | list[str] | None


def _required_uuid(row: dict[str, ActorRowValue], key: str) -> uuid.UUID:
    value = row[key]
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        return uuid.UUID(value)
    raise TypeError(f"{key} must be a UUID")


def _required_str(row: dict[str, ActorRowValue], key: str) -> str:
    value = row[key]
    if isinstance(value, str):
        return value
    raise TypeError(f"{key} must be a string")


def _optional_uuid(row: dict[str, ActorRowValue], key: str) -> uuid.UUID | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        return uuid.UUID(value)
    raise TypeError(f"{key} must be a UUID or None")


def _str_list(row: dict[str, ActorRowValue], key: str) -> list[str]:
    value = row.get(key)
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise TypeError(f"{key} must be a list of strings")


def _int_value(row: dict[str, ActorRowValue], key: str, default: int) -> int:
    value = row.get(key, default)
    if isinstance(value, int):
        return value
    raise TypeError(f"{key} must be an integer")


def _float_value(row: dict[str, ActorRowValue], key: str, default: float) -> float:
    value = row.get(key, default)
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"{key} must be a number")


def _row_to_actor(row: dict[str, ActorRowValue]) -> ActorRef:
    """Convert a PG row dict to an ActorRef."""
    return ActorRef(
        id=_required_uuid(row, "id"),
        display_name=_required_str(row, "display_name"),
        type=ActorType(row.get("actor_type") or "worker_agent"),
        authority_level=_int_value(row, "authority_level", 0),
        handles=_str_list(row, "handles"),
        org_id=_optional_uuid(row, "org_id"),
        team_ids=[uuid.UUID(team_id) for team_id in _str_list(row, "team_ids")],
        trust_level=_float_value(row, "trust_level", 0.5),
        tags=_str_list(row, "tags"),
        gateway_id=_required_str(row, "gateway_id"),
    )


class PostgresActorRegistry(IActorRegistry):

    def __init__(self, pool: asyncpg.Pool | None = None, dsn: str = "",
                 dataset_name: str = "elephantbroker", gateway_id: str = "") -> None:
        self._pool = pool
        self._dsn = dsn
        self._dataset_name = dataset_name
        self._gateway_id = gateway_id

    async def init_db(self) -> None:
        """Initialise the connection pool if not already provided."""
        if self._pool is not None:
            return
        if self._dsn:
            self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=4)
            logger.info("PostgresActorRegistry connected via DSN")
        else:
            logger.warning("No DSN or pool for PostgresActorRegistry")

    async def register_actor(self, actor: ActorRef) -> ActorRef:
        """Register a new actor in PostgreSQL."""
        if not self._pool:
            raise RuntimeError("PostgresActorRegistry not initialised")
        actor.gateway_id = actor.gateway_id or self._gateway_id
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO actors (id, display_name, actor_type, authority_level,
                   handles, org_id, team_ids, trust_level, tags, gateway_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   ON CONFLICT (id) DO UPDATE SET
                       display_name = EXCLUDED.display_name,
                       authority_level = EXCLUDED.authority_level,
                       updated_at = NOW()""",
                actor.id, actor.display_name, actor.type.value,
                actor.authority_level, list(actor.handles),
                actor.org_id, [str(t) for t in actor.team_ids],
                actor.trust_level, list(actor.tags), actor.gateway_id,
            )
            # Insert MEMBER_OF relationships for each team
            for team_id in actor.team_ids:
                await conn.execute(
                    """INSERT INTO actor_relationships (source_actor_id, target_actor_id, relationship_type)
                       VALUES ($1, $2, 'member_of')
                       ON CONFLICT DO NOTHING""",
                    actor.id, team_id,
                )
        return actor

    async def resolve_actor(self, actor_id: uuid.UUID) -> ActorRef | None:
        """Resolve an actor by ID."""
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM actors WHERE id = $1", actor_id
            )
        return _row_to_actor(row) if row else None

    async def resolve_by_handle(self, handle: str) -> ActorRef | None:
        """Look up an actor by platform-qualified handle."""
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM actors WHERE $1 = ANY(handles) AND gateway_id = $2 LIMIT 1",
                handle, self._gateway_id,
            )
        return _row_to_actor(row) if row else None

    async def resolve_by_casdoor_id(self, casdoor_user_id: str) -> ActorRef | None:
        """Look up an actor by Casdoor user ID."""
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM actors WHERE casdoor_user_id = $1", casdoor_user_id
            )
        return _row_to_actor(row) if row else None

    async def get_authority_chain(self, actor_id: uuid.UUID) -> list[ActorRef]:
        """Get supervisors upward via REPORTS_TO edges."""
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """WITH RECURSIVE chain AS (
                       SELECT target_actor_id, 1 AS depth
                       FROM actor_relationships
                       WHERE source_actor_id = $1 AND relationship_type IN ('reports_to', 'supervises')
                   UNION ALL
                       SELECT ar.target_actor_id, c.depth + 1
                       FROM actor_relationships ar
                       JOIN chain c ON ar.source_actor_id = c.target_actor_id
                       WHERE ar.relationship_type IN ('reports_to', 'supervises')
                         AND c.depth < 20
                   )
                   SELECT a.* FROM actors a
                   JOIN chain c ON a.id = c.target_actor_id
                   ORDER BY c.depth""",
                actor_id,
            )
        return [_row_to_actor(row) for row in rows]

    async def get_relationships(self, actor_id: uuid.UUID) -> list[ActorRelationship]:
        """Get all relationships involving this actor."""
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT source_actor_id, target_actor_id, relationship_type
                   FROM actor_relationships
                   WHERE source_actor_id = $1 OR target_actor_id = $1""",
                actor_id,
            )
        results: list[ActorRelationship] = []
        for row in rows:
            try:
                rel_type = RelationshipType(row["relationship_type"])
            except ValueError:
                continue
            results.append(ActorRelationship(
                source_actor_id=row["source_actor_id"],
                target_actor_id=row["target_actor_id"],
                relationship_type=rel_type,
            ))
        return results

    async def add_relationship(
        self, source_id: uuid.UUID, target_id: uuid.UUID, rel_type: RelationshipType,
    ) -> None:
        """Add a relationship between two actors."""
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO actor_relationships (source_actor_id, target_actor_id, relationship_type)
                   VALUES ($1, $2, $3)
                   ON CONFLICT DO NOTHING""",
                source_id, target_id, rel_type.value,
            )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
