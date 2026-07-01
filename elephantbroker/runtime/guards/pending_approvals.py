"""Cross-session pending-approvals index (Phase 11 — TD-24).

A thin Redis SET wrapper that aggregates *open* HITL approval ``request_id``s for a
single gateway, so the dashboard can render one pending-approvals queue without
scanning per-session keys.

This complements :class:`~elephantbroker.runtime.guards.approval_queue.ApprovalQueue`,
which stores the full approval records under per-session keys. Here we only track
the *ids* of still-open requests in one aggregate set:

- ``add()`` (SADD) when an approval is created,
- ``remove()`` (SREM) when it is resolved (approved / rejected / cancelled / timed-out).

The set key is ``eb:{gateway_id}:pending_approvals``, produced by
``RedisKeyBuilder.pending_approvals()`` (owned by the Wire agent — referenced here
by name only). This helper never constructs the key string itself.
"""
from __future__ import annotations

import logging

from elephantbroker.runtime.observability import GatewayLoggerAdapter
from elephantbroker.runtime.redis_keys import RedisKeyBuilder

logger = logging.getLogger(__name__)


class PendingApprovalsIndex:
    """Redis SET of open approval request_ids, aggregated per gateway."""

    def __init__(
        self,
        redis,
        redis_keys: RedisKeyBuilder,
        *,
        gateway_id: str = "",
    ) -> None:
        self._redis = redis
        self._keys = redis_keys
        self._gateway_id = gateway_id
        self._log = GatewayLoggerAdapter(logger, {"gateway_id": gateway_id})

    async def add(self, request_id: str) -> None:
        """Record an approval id as pending (SADD). Idempotent."""
        if self._redis is None:
            return
        await self._redis.sadd(self._keys.pending_approvals(), str(request_id))
        self._log.info("Added pending approval %s", request_id)

    async def remove(self, request_id: str) -> None:
        """Drop a resolved approval id from the pending set (SREM). Idempotent."""
        if self._redis is None:
            return
        await self._redis.srem(self._keys.pending_approvals(), str(request_id))
        self._log.info("Removed pending approval %s", request_id)

    async def list_ids(self) -> list[str]:
        """Return all currently-pending approval ids, sorted for stable output."""
        if self._redis is None:
            return []
        members = await self._redis.smembers(self._keys.pending_approvals())
        return sorted(
            m.decode() if isinstance(m, bytes) else m for m in (members or [])
        )

    async def count(self) -> int:
        """Return the number of pending approvals (SCARD)."""
        if self._redis is None:
            return 0
        return int(await self._redis.scard(self._keys.pending_approvals()) or 0)

    async def contains(self, request_id: str) -> bool:
        """Return whether an approval id is currently in the pending set."""
        if self._redis is None:
            return False
        return bool(
            await self._redis.sismember(
                self._keys.pending_approvals(), str(request_id)
            )
        )
