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


def _row_to_actor(row: dict) -> ActorRef:
    """Convert a PG row dict to an ActorRef."""
    return ActorRef(
        id=row["id"],
        display_name=row["display_name"],
        actor_type=ActorType(row.get("actor_type", "worker_agent")),
        authority_level=row.get("authority_level", 0),
        handles=list(row.get("handles") or []),
        org_id=row.get("org_id"),
        team_ids=[uuid.UUID(t) for t in (row.get("team_ids") or [])],
        trust_level=row.get("trust_level", 0.5),
        tags=list(row.get("tags") or []),
        gateway_id=row.get("gateway_id", ""),
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
