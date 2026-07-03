"""Actor registry — CRUD + authority chain traversal via Neo4j."""
from __future__ import annotations

import uuid

import cognee
from cognee.tasks.storage import add_data_points

from elephantbroker.runtime.adapters.cognee.datapoints import ActorDataPoint
from elephantbroker.runtime.adapters.cognee.graph import GraphAdapter
from elephantbroker.runtime.identity_utils import assert_same_gateway
from elephantbroker.runtime.interfaces.actor_registry import IActorRegistry
from elephantbroker.runtime.interfaces.trace_ledger import ITraceLedger
from elephantbroker.schemas.actor import ActorRef, ActorRelationship, RelationshipType
from elephantbroker.schemas.trace import TraceEvent, TraceEventType


class ActorRegistry(IActorRegistry):

    def __init__(self, graph: GraphAdapter, trace_ledger: ITraceLedger,
                 dataset_name: str = "elephantbroker", gateway_id: str = "") -> None:
        self._graph = graph
        self._trace = trace_ledger
        self._dataset_name = dataset_name
        self._gateway_id = gateway_id

    async def register_actor(self, actor: ActorRef) -> ActorRef:
        actor.gateway_id = actor.gateway_id or self._gateway_id
        dp = ActorDataPoint.from_schema(actor)
        await add_data_points([dp])

        # Phase 8: Create MEMBER_OF edges for each team
        for team_id in actor.team_ids:
            try:
                # R2-P7 / link-spam guard: validate team belongs to the
                # caller's gateway. PermissionError surfaces as 403 via
                # R2-P5 middleware; runtime errors stay best-effort
                # (silent skip per pre-existing contract).
                await assert_same_gateway(self._graph, str(team_id), self._gateway_id)
                await self._graph.add_relation(str(actor.id), str(team_id), "MEMBER_OF")
            except PermissionError:
                await self._trace.append_event(TraceEvent(
                    event_type=TraceEventType.AUTHORITY_CHECK_FAILED,
                    payload={"action": "register_actor", "target": str(team_id), "gateway_id": self._gateway_id},
                ))
                raise
            except Exception:
                pass  # Edge creation is best-effort

        actor_text = f"Actor: {actor.display_name} (type: {actor.type.value})"
        if actor.handles:
            actor_text += f" handles: {', '.join(actor.handles)}"
        await cognee.add(actor_text, dataset_name=self._dataset_name)

        await self._trace.append_event(
            TraceEvent(
                event_type=TraceEventType.INPUT_RECEIVED,
                actor_ids=[actor.id],
                payload={"action": "register_actor", "display_name": actor.display_name},
            )
        )
        return actor

    async def resolve_by_handle(self, handle: str) -> ActorRef | None:
        """Look up an actor by platform-qualified handle (e.g. 'telegram:user_tg').

        Inactive actors are excluded: a merged-away duplicate keeps its handles
        as an audit record (soft-deactivate, see ``merge_actors``), so without
        the ``active`` filter the LIMIT 1 lookup could return the dead node and
        break login mapping / dedup.
        """
        cypher = (
            "MATCH (a:ActorDataPoint) "
            "WHERE $handle IN a.handles AND a.gateway_id = $gateway_id "
            "AND (a.active = true OR a.active IS NULL) "
            "RETURN properties(a) AS props LIMIT 1"
        )
        records = await self._graph.query_cypher(
            cypher, {"handle": handle, "gateway_id": self._gateway_id}
        )
        if not records:
            await self._trace.append_event(
                TraceEvent(
                    event_type=TraceEventType.HANDLE_RESOLVED,
                    payload={"handle": handle, "result": "not_found"},
                )
            )
            return None
        entity = records[0].get("props", {})
        await self._trace.append_event(
            TraceEvent(
                event_type=TraceEventType.HANDLE_RESOLVED,
                actor_ids=[uuid.UUID(entity.get("eb_id", ""))],
                payload={"handle": handle, "result": "found", "actor": entity.get("display_name", "")},
            )
        )
        return ActorDataPoint.from_entity_dict(entity).to_schema()

    async def resolve_actor(self, actor_id: uuid.UUID) -> ActorRef | None:
        entity = await self._graph.get_entity(str(actor_id))
        if entity is None:
            return None
        return ActorDataPoint.from_entity_dict(entity).to_schema()

    async def get_authority_chain(self, actor_id: uuid.UUID) -> list[ActorRef]:
        # Traverse SUPERVISES/REPORTS_TO edges upward
        cypher = (
            "MATCH path = (start {eb_id: $actor_id})-[:REPORTS_TO|SUPERVISES*1..10]->(supervisor) "
            "WHERE start.gateway_id = $gateway_id "
            "RETURN properties(supervisor) AS props "
            "ORDER BY length(path)"
        )
        records = await self._graph.query_cypher(cypher, {"actor_id": str(actor_id), "gateway_id": self._gateway_id})
        return [
            ActorDataPoint.from_entity_dict(rec["props"]).to_schema()
            for rec in records
        ]

    async def get_relationships(self, actor_id: uuid.UUID) -> list[ActorRelationship]:
        cypher = (
            "MATCH (a {eb_id: $actor_id})-[r]->(b) "
            "WHERE a.gateway_id = $gateway_id "
            "RETURN a.eb_id AS source, b.eb_id AS target, type(r) AS rel_type, properties(r) AS props "
            "UNION "
            "MATCH (b)-[r]->(a {eb_id: $actor_id}) "
            "WHERE a.gateway_id = $gateway_id "
            "RETURN b.eb_id AS source, a.eb_id AS target, type(r) AS rel_type, properties(r) AS props"
        )
        records = await self._graph.query_cypher(cypher, {"actor_id": str(actor_id), "gateway_id": self._gateway_id})
        relationships: list[ActorRelationship] = []
        for rec in records:
            try:
                rel_type = RelationshipType(rec["rel_type"].lower())
            except ValueError:
                continue
            relationships.append(ActorRelationship(
                source_actor_id=uuid.UUID(rec["source"]),
                target_actor_id=uuid.UUID(rec["target"]),
                relationship_type=rel_type,
            ))
        return relationships

    # ------------------------------------------------------------------
    # actors-orgs-4: merge a duplicate actor into a survivor
    # ------------------------------------------------------------------
    #
    # A correct merge is a cross-node graph refactor, not a stub. Duplicate
    # dashboard actors reference the same human across the whole graph in two
    # distinct ways, and BOTH must be re-pointed or the merge leaves dangling
    # references (a symptom-hiding bandaid):
    #   1. Typed EDGES to/from the actor node (MEMBER_OF, REPORTS_TO,
    #      SUPERVISES, CREATED_BY, ABOUT_ACTOR, SERVES_GOAL, ...).
    #   2. Scalar / list actor-id PROPERTIES carried on Fact / Goal / Artifact /
    #      Evidence / Procedure / Trace DataPoints (source_actor_id,
    #      created_by_actor_id, actor_id, verifier_actor_id; target_actor_ids,
    #      owner_actor_ids, actor_ids). These are stored under their plain names
    #      (only the node's own id uses the ``eb_`` prefix).
    # Everything is strictly gateway-scoped. The survivor's own attributes win;
    # only multi-valued identity (handles / team_ids / tags) is unioned so the
    # merged actor keeps every way the human was addressable.

    #: Single-valued actor-reference properties carried on other DataPoints.
    _SCALAR_ACTOR_REF_PROPS = (
        "source_actor_id",
        "created_by_actor_id",
        "actor_id",
        "verifier_actor_id",
    )
    #: List-valued actor-reference properties carried on other DataPoints.
    _ARRAY_ACTOR_REF_PROPS = (
        "target_actor_ids",
        "owner_actor_ids",
        "actor_ids",
    )

    async def merge_actors(
        self, survivor_id: uuid.UUID, duplicate_id: uuid.UUID
    ) -> ActorRef:
        gw = self._gateway_id
        surv, dup = str(survivor_id), str(duplicate_id)
        if surv == dup:
            raise ValueError("Cannot merge an actor into itself")

        surv_entity = await self._graph.get_entity(surv, gateway_id=gw)
        dup_entity = await self._graph.get_entity(dup, gateway_id=gw)
        if surv_entity is None:
            raise ValueError(f"Survivor actor {surv} not found in gateway {gw!r}")
        if dup_entity is None:
            raise ValueError(f"Duplicate actor {dup} not found in gateway {gw!r}")

        survivor = ActorDataPoint.from_entity_dict(surv_entity).to_schema()
        duplicate = ActorDataPoint.from_entity_dict(dup_entity).to_schema()

        # 1. Union multi-valued identity onto the survivor (order-preserving dedup).
        def _union(a: list, b: list) -> list:
            return list(dict.fromkeys([*a, *b]))

        survivor.handles = _union(survivor.handles, duplicate.handles)
        survivor.team_ids = _union(survivor.team_ids, duplicate.team_ids)
        survivor.tags = _union(survivor.tags, duplicate.tags)
        await add_data_points([ActorDataPoint.from_schema(survivor)])

        # 2. Re-point scalar actor-id properties across the gateway.
        for prop in self._SCALAR_ACTOR_REF_PROPS:
            await self._graph.query_cypher(
                f"MATCH (n) WHERE n.gateway_id = $gw AND n.{prop} = $dup "
                f"SET n.{prop} = $surv",
                {"gw": gw, "dup": dup, "surv": surv},
            )

        # 3. Re-point list actor-id properties (drop the duplicate id, add the
        #    survivor id only if not already present — no duplicate entries).
        for prop in self._ARRAY_ACTOR_REF_PROPS:
            await self._graph.query_cypher(
                f"MATCH (n) WHERE n.gateway_id = $gw AND $dup IN n.{prop} "
                f"SET n.{prop} = [x IN n.{prop} WHERE x <> $dup] + "
                f"CASE WHEN $surv IN n.{prop} THEN [] ELSE [$surv] END",
                {"gw": gw, "dup": dup, "surv": surv},
            )

        # 4. Re-point typed edges onto the survivor (add_relation MERGEs, so no
        #    duplicate edges are created), carrying each edge's properties along.
        #    Skip edges to/from the survivor itself to avoid self-loops.
        out_edges = await self._graph.query_cypher(
            "MATCH (d {eb_id: $dup})-[r]->(t) WHERE d.gateway_id = $gw "
            "RETURN type(r) AS rel_type, t.eb_id AS other, properties(r) AS props",
            {"dup": dup, "gw": gw},
        )
        for rec in out_edges:
            other = rec.get("other")
            if other and other != surv:
                await self._graph.add_relation(
                    surv, other, rec["rel_type"], properties=rec.get("props") or {}
                )

        in_edges = await self._graph.query_cypher(
            "MATCH (s)-[r]->(d {eb_id: $dup}) WHERE d.gateway_id = $gw "
            "RETURN type(r) AS rel_type, s.eb_id AS other, properties(r) AS props",
            {"dup": dup, "gw": gw},
        )
        for rec in in_edges:
            other = rec.get("other")
            if other and other != surv:
                await self._graph.add_relation(
                    other, surv, rec["rel_type"], properties=rec.get("props") or {}
                )

        # 5. Provenance — emitted BEFORE the terminal soft-deactivate step so a
        #    mid-crash merge is always audited (every prior step is an
        #    idempotent MERGE/SET, so a retry after the event is safe).
        await self._trace.append_event(
            TraceEvent(
                event_type=TraceEventType.ACTOR_MERGED,
                actor_ids=[survivor.id],
                payload={
                    "action": "merge_actors",
                    "survivor": surv,
                    "duplicate": dup,
                    "merged_handles": survivor.handles,
                },
            )
        )

        # 6. Retire the duplicate by soft-deactivation (Cognee-first upsert, no
        #    DETACH DELETE): the node, its handles, and ALL its original edges
        #    stay intact as an audit/provenance record (TD-70 / TF-ER-008).
        #    Inactive actors are hidden from listings, handle resolution, and
        #    authorization by the ``active`` filters on those paths.
        duplicate.active = False
        await add_data_points([ActorDataPoint.from_schema(duplicate)])

        return survivor
