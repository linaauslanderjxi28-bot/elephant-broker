"""Unit tests for ``ActorRegistry.merge_actors`` — the branch-new
(actors-orgs-4) merge refactor.

Scope (deliberately narrow): the parts of ``merge_actors`` that are NOT
already exercised elsewhere —

  1. UNION + order-preserving dedup of the survivor's multi-valued identity
     (``handles`` / ``team_ids`` / ``tags``) with the duplicate's, and the
     fact that the unioned survivor is upserted via ``add_data_points`` and
     returned to the caller.
  2. The list actor-id PROPERTY rebuild (``target_actor_ids`` /
     ``owner_actor_ids`` / ``actor_ids``) issued as gateway-scoped Cypher
     that drops the duplicate id and appends the survivor id only when absent.

  3. The merge terminal steps: edge re-points carry edge properties through to
     ``add_relation``, the duplicate is soft-deactivated (``active=False``
     upsert via ``add_data_points`` — never ``delete_entity``), and the
     ``ACTOR_MERGED`` trace event is emitted BEFORE the deactivation so a
     mid-crash merge is always audited.

All I/O is mocked (GraphAdapter, Cognee ``add_data_points``, TraceLedger) in
the same AsyncMock style as the sibling tests in this directory
(``test_handle_resolution.py`` / ``test_registry_link_spam_guard.py``). No
live Neo4j means the *executed* list semantics of the rebuild Cypher cannot be
observed — those tests assert the exact query text + gateway-scoped params
that the source emits.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from elephantbroker.runtime.actors.registry import ActorRegistry
from elephantbroker.schemas.trace import TraceEventType

GW = "gw-a"


def _actor_entity(
    eb_id: str,
    *,
    handles=None,
    team_ids=None,
    tags=None,
    display_name="Someone",
    actor_type="human_coordinator",
    gateway_id: str = GW,
) -> dict:
    """Raw graph-entity dict shaped like ``GraphAdapter.get_entity()`` output,
    consumed by ``ActorDataPoint.from_entity_dict``."""
    return {
        "eb_id": eb_id,
        "actor_type": actor_type,
        "display_name": display_name,
        "authority_level": 0,
        "handles": list(handles or []),
        "team_ids": list(team_ids or []),
        "trust_level": 0.5,
        "tags": list(tags or []),
        "active": True,
        "gateway_id": gateway_id,
    }


def _make_registry(surv_entity: dict, dup_entity: dict):
    """Wire an ``ActorRegistry`` whose graph returns ``surv_entity`` for the
    survivor id and ``dup_entity`` for the duplicate id, with every edge query
    resolving to no edges (so the merge focuses on identity + property rebuild).
    Returns ``(registry, graph, trace, add_dp_mock)``."""
    surv_id = surv_entity["eb_id"]
    dup_id = dup_entity["eb_id"]

    graph = AsyncMock()

    async def _get_entity(node_id, *args, **kwargs):
        if node_id == surv_id:
            return surv_entity
        if node_id == dup_id:
            return dup_entity
        return None

    graph.get_entity = AsyncMock(side_effect=_get_entity)
    # Every Cypher (scalar re-point, array rebuild, edge discovery) returns [].
    graph.query_cypher = AsyncMock(return_value=[])
    graph.add_relation = AsyncMock()
    graph.delete_entity = AsyncMock()

    trace = AsyncMock()
    trace.append_event = AsyncMock()

    add_dp = AsyncMock()

    reg = ActorRegistry(graph=graph, trace_ledger=trace, dataset_name="t", gateway_id=GW)
    return reg, graph, trace, add_dp


def _array_rebuild_calls(graph, prop: str):
    """All ``query_cypher`` calls that are the list-property rebuild for
    ``prop`` (identified by the ``$dup IN n.<prop>`` guard unique to the
    array branch — the scalar branch uses ``n.<prop> = $dup``)."""
    out = []
    for c in graph.query_cypher.call_args_list:
        cypher = c[0][0]
        if f"$dup IN n.{prop}" in cypher:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# 1. UNION + order-preserving dedup of handles / team_ids / tags
# ---------------------------------------------------------------------------


class TestIdentityUnion:
    async def test_handles_unioned_and_deduped_order_preserving(self, monkeypatch):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        surv = _actor_entity(surv_id, handles=["telegram:s", "email:shared@x.com"])
        dup = _actor_entity(dup_id, handles=["email:shared@x.com", "slack:U9"])
        reg, graph, _trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        result = await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        # survivor first, then only the duplicate-unique handle; shared deduped.
        assert result.handles == ["telegram:s", "email:shared@x.com", "slack:U9"]

    async def test_tags_unioned_and_deduped_order_preserving(self, monkeypatch):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        surv = _actor_entity(surv_id, tags=["vip", "eng"])
        dup = _actor_entity(dup_id, tags=["eng", "oncall"])
        reg, graph, _trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        result = await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        assert result.tags == ["vip", "eng", "oncall"]

    async def test_team_ids_unioned_and_deduped(self, monkeypatch):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        t_shared, t_surv_only, t_dup_only = (
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
        )
        surv = _actor_entity(surv_id, team_ids=[t_surv_only, t_shared])
        dup = _actor_entity(dup_id, team_ids=[t_shared, t_dup_only])
        reg, graph, _trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        result = await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        got = [str(t) for t in result.team_ids]
        assert got == [t_surv_only, t_shared, t_dup_only]

    async def test_disjoint_union_keeps_all_no_loss(self, monkeypatch):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        surv = _actor_entity(surv_id, handles=["a"], tags=["x"])
        dup = _actor_entity(dup_id, handles=["b"], tags=["y"])
        reg, graph, _trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        result = await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        assert result.handles == ["a", "b"]
        assert result.tags == ["x", "y"]

    async def test_fully_overlapping_identity_yields_no_duplicates(self, monkeypatch):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        team = str(uuid.uuid4())
        surv = _actor_entity(surv_id, handles=["h1", "h2"], tags=["t"], team_ids=[team])
        dup = _actor_entity(dup_id, handles=["h1", "h2"], tags=["t"], team_ids=[team])
        reg, graph, _trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        result = await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        assert result.handles == ["h1", "h2"]
        assert result.tags == ["t"]
        assert [str(t) for t in result.team_ids] == [team]

    async def test_union_written_to_survivor_via_add_data_points(self, monkeypatch):
        """The unioned identity is upserted (survivor id) through
        ``add_data_points`` as an ``ActorDataPoint`` carrying the merged
        lists — not left only on the returned schema object."""
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        team_s, team_d = str(uuid.uuid4()), str(uuid.uuid4())
        surv = _actor_entity(surv_id, handles=["s"], tags=["ts"], team_ids=[team_s])
        dup = _actor_entity(dup_id, handles=["d"], tags=["td"], team_ids=[team_d])
        reg, graph, _trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        # Two upserts: survivor union first, duplicate soft-deactivation last.
        assert add_dp.await_count == 2
        dp = add_dp.call_args_list[0][0][0][0]
        # First upsert targets the SURVIVOR node, not the duplicate.
        assert dp.eb_id == surv_id
        assert dp.handles == ["s", "d"]
        assert dp.tags == ["ts", "td"]
        assert dp.team_ids == [team_s, team_d]
        assert dp.gateway_id == GW

    async def test_returned_actor_is_survivor_identity(self, monkeypatch):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        surv = _actor_entity(surv_id, display_name="Survivor", handles=["s"])
        dup = _actor_entity(dup_id, display_name="Dupe", handles=["d"])
        reg, graph, _trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        result = await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        # Survivor's own scalar attributes win; identity list is the union.
        assert str(result.id) == surv_id
        assert result.display_name == "Survivor"
        assert result.handles == ["s", "d"]

    async def test_merged_handles_recorded_in_trace(self, monkeypatch):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        surv = _actor_entity(surv_id, handles=["s"])
        dup = _actor_entity(dup_id, handles=["d"])
        reg, graph, trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        trace.append_event.assert_awaited()
        event = trace.append_event.call_args[0][0]
        assert event.event_type == TraceEventType.ACTOR_MERGED
        assert event.payload["action"] == "merge_actors"
        assert event.payload["survivor"] == surv_id
        assert event.payload["duplicate"] == dup_id
        assert event.payload["merged_handles"] == ["s", "d"]


# ---------------------------------------------------------------------------
# 2. Array actor-id PROPERTY rebuild (target/owner/actor_ids)
# ---------------------------------------------------------------------------


class TestArrayPropertyRebuild:
    @pytest.mark.parametrize(
        "prop", ["target_actor_ids", "owner_actor_ids", "actor_ids"]
    )
    async def test_each_array_prop_rebuilt_exactly_once(self, monkeypatch, prop):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        surv = _actor_entity(surv_id)
        dup = _actor_entity(dup_id)
        reg, graph, _trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        calls = _array_rebuild_calls(graph, prop)
        assert len(calls) == 1

    async def test_array_rebuild_covers_exactly_the_three_list_props(
        self, monkeypatch
    ):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        reg, graph, _trace, add_dp = _make_registry(
            _actor_entity(surv_id), _actor_entity(dup_id)
        )
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        rebuilt = {
            prop
            for c in graph.query_cypher.call_args_list
            for prop in ("target_actor_ids", "owner_actor_ids", "actor_ids")
            if f"$dup IN n.{prop}" in c[0][0]
        }
        assert rebuilt == {"target_actor_ids", "owner_actor_ids", "actor_ids"}

    async def test_array_rebuild_drops_dup_and_appends_survivor_conditionally(
        self, monkeypatch
    ):
        """The rebuild Cypher must (a) filter the duplicate id out and
        (b) append the survivor id only when it is not already present, so
        no duplicate entries are introduced."""
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        reg, graph, _trace, add_dp = _make_registry(
            _actor_entity(surv_id), _actor_entity(dup_id)
        )
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        for prop in ("target_actor_ids", "owner_actor_ids", "actor_ids"):
            (call,) = _array_rebuild_calls(graph, prop)
            cypher = call[0][0]
            # drop the duplicate id
            assert f"[x IN n.{prop} WHERE x <> $dup]" in cypher
            # append survivor only if absent (no-duplicate guard)
            assert f"CASE WHEN $surv IN n.{prop} THEN [] ELSE [$surv] END" in cypher

    async def test_array_rebuild_is_gateway_scoped_with_correct_params(
        self, monkeypatch
    ):
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        reg, graph, _trace, add_dp = _make_registry(
            _actor_entity(surv_id), _actor_entity(dup_id)
        )
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        for prop in ("target_actor_ids", "owner_actor_ids", "actor_ids"):
            (call,) = _array_rebuild_calls(graph, prop)
            cypher, params = call[0][0], call[0][1]
            assert "n.gateway_id = $gw" in cypher
            assert params == {"gw": GW, "dup": dup_id, "surv": surv_id}

    async def test_array_rebuild_only_matches_nodes_containing_dup(
        self, monkeypatch
    ):
        """The MATCH guard is ``$dup IN n.<prop>`` — a node whose list does
        not contain the duplicate id is never touched by the rebuild."""
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        reg, graph, _trace, add_dp = _make_registry(
            _actor_entity(surv_id), _actor_entity(dup_id)
        )
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        for prop in ("target_actor_ids", "owner_actor_ids", "actor_ids"):
            (call,) = _array_rebuild_calls(graph, prop)
            assert f"WHERE n.gateway_id = $gw AND $dup IN n.{prop}" in call[0][0]


# ---------------------------------------------------------------------------
# 3. Merge terminal steps: edge props, soft-deactivate, event ordering
# ---------------------------------------------------------------------------


class TestMergeTerminalSteps:
    async def test_duplicate_is_soft_deactivated_not_deleted(self, monkeypatch):
        """The duplicate is retired by an ``active=False`` upsert via
        ``add_data_points`` — its node, handles (audit record), and original
        edges survive; ``delete_entity`` is never called."""
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        surv = _actor_entity(surv_id, handles=["s"])
        dup = _actor_entity(dup_id, handles=["d"])
        reg, graph, _trace, add_dp = _make_registry(surv, dup)
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        graph.delete_entity.assert_not_called()
        # Last upsert is the duplicate, deactivated, with handles intact.
        assert add_dp.await_count == 2
        dp = add_dp.call_args_list[-1][0][0][0]
        assert dp.eb_id == dup_id
        assert dp.active is False
        assert dp.handles == ["d"]
        assert dp.gateway_id == GW

    async def test_edge_repoint_passes_edge_properties_through(self, monkeypatch):
        """Both edge-discovery probes RETURN ``properties(r)`` and the
        re-pointed ``add_relation`` calls carry them via the ``properties``
        kwarg (falling back to ``{}`` when the probe yields none)."""
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        out_other, in_other = str(uuid.uuid4()), str(uuid.uuid4())
        reg, graph, _trace, add_dp = _make_registry(
            _actor_entity(surv_id), _actor_entity(dup_id)
        )
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        async def _query_cypher(cypher, params=None):
            if "MATCH (d {eb_id: $dup})-[r]->(t)" in cypher:
                assert "properties(r) AS props" in cypher
                return [{"rel_type": "MEMBER_OF", "other": out_other,
                         "props": {"since": "2026-01-01"}}]
            if "MATCH (s)-[r]->(d {eb_id: $dup})" in cypher:
                assert "properties(r) AS props" in cypher
                return [{"rel_type": "SUPERVISES", "other": in_other, "props": None}]
            return []

        graph.query_cypher = AsyncMock(side_effect=_query_cypher)

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        graph.add_relation.assert_any_await(
            surv_id, out_other, "MEMBER_OF", properties={"since": "2026-01-01"}
        )
        # Absent probe props degrade to {} — never None.
        graph.add_relation.assert_any_await(
            in_other, surv_id, "SUPERVISES", properties={}
        )

    async def test_actor_merged_event_emitted_before_deactivation(self, monkeypatch):
        """Crash-safety ordering: the ``ACTOR_MERGED`` event precedes the
        terminal soft-deactivate upsert, so a merge can never complete
        unaudited (the sequence is idempotent / safe to retry)."""
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        reg, graph, trace, add_dp = _make_registry(
            _actor_entity(surv_id), _actor_entity(dup_id)
        )
        order: list[str] = []
        add_dp.side_effect = lambda dps: order.append(
            f"add_dp:{dps[0].eb_id}"
        )
        trace.append_event.side_effect = lambda event: order.append(
            f"event:{event.event_type.value}"
        )
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))

        assert order == [
            f"add_dp:{surv_id}",              # survivor union upsert
            "event:actor_merged",             # audit BEFORE deactivation
            f"add_dp:{dup_id}",               # duplicate soft-deactivate
        ]


# ---------------------------------------------------------------------------
# 4. Guard clauses (reject before any mutation)
# ---------------------------------------------------------------------------


class TestMergeGuardClauses:
    async def test_self_merge_rejected(self):
        """Merging an actor into itself raises before any I/O."""
        graph = AsyncMock()
        reg = ActorRegistry(
            graph=graph, trace_ledger=AsyncMock(), dataset_name="t", gateway_id=GW
        )
        same = uuid.uuid4()
        with pytest.raises(ValueError, match="into itself"):
            await reg.merge_actors(same, same)
        graph.get_entity.assert_not_called()

    async def test_missing_duplicate_raises(self, monkeypatch):
        """A duplicate id absent from the gateway raises and mutates nothing."""
        surv_id, dup_id = str(uuid.uuid4()), str(uuid.uuid4())
        surv = _actor_entity(surv_id)
        reg, graph, _trace, add_dp = _make_registry(surv, _actor_entity(dup_id))
        # Duplicate resolves to None (not found in this gateway).
        graph.get_entity = AsyncMock(
            side_effect=lambda node_id, *a, **k: surv if node_id == surv_id else None
        )
        monkeypatch.setattr(
            "elephantbroker.runtime.actors.registry.add_data_points", add_dp
        )

        with pytest.raises(ValueError, match="not found"):
            await reg.merge_actors(uuid.UUID(surv_id), uuid.UUID(dup_id))
        add_dp.assert_not_called()
        graph.delete_entity.assert_not_called()
