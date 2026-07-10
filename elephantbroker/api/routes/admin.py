"""Admin API routes — authority-gated management of orgs, teams, actors, goals, profiles.

All mutating endpoints call ``check_authority()`` before performing the operation.
The same API surface is used by the dashboard, ``ebrun`` CLI, and privileged agent tools.
"""
from __future__ import annotations

import logging
import uuid

from cognee.tasks.storage import add_data_points
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from elephantbroker.api.auth.identity import (
    _dashboard_handle,
    _persist_actor_mapping,
    get_identity,
)
from elephantbroker.api.routes._authority import check_authority
from elephantbroker.runtime.adapters.cognee.datapoints import (
    ActorDataPoint,
    OrganizationDataPoint,
    TeamDataPoint,
)
from elephantbroker.runtime.identity_utils import assert_same_gateway
from elephantbroker.schemas.base import Scope
from elephantbroker.schemas.goal import GoalState, GoalStatus
from elephantbroker.schemas.trace import TraceEvent, TraceEventType

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1)
    display_label: str = ""


class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=1)
    display_label: str = ""
    org_id: str


class AddMemberRequest(BaseModel):
    actor_id: str


class CreatePersistentGoalRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    scope: str = "actor"
    org_id: str | None = None
    team_id: str | None = None
    parent_goal_id: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    owner_actor_ids: list[str] = Field(default_factory=list)


class UpdateAuthorityRuleRequest(BaseModel):
    min_authority_level: int = Field(ge=0)
    require_matching_org: bool = False
    require_matching_team: bool = False
    require_self_ownership: bool = False
    matching_exempt_level: int | None = None


class SetProfileOverrideRequest(BaseModel):
    overrides: dict


class AddGoalBlockerRequest(BaseModel):
    blocker: str = Field(min_length=1)


class CreateSubgoalRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    owner_actor_ids: list[str] = Field(default_factory=list)


class SetActorStatusRequest(BaseModel):
    active: bool


class SetActorOrgRequest(BaseModel):
    # ``None`` / empty string clears the actor's organization membership.
    org_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_deps(request: Request):
    """Extract common dependencies (container + resolved calling actor id).

    RC-1 root-cause fix: the calling actor is taken from the identity resolved
    by ``AuthMiddleware`` (``request.state.identity``), which unifies ALL
    supported auth methods — SuperTokens session cookie (dashboard users),
    ``X-EB-API-Key`` (``ebrun`` CLI), and ``X-EB-Agent-Key`` gateway identity
    (TS plugins). This is the SAME principal the ``/dashboard/*`` read routes
    authorize against, so a browser session cookie now resolves to a real
    ``actor_id`` here too (previously ``/admin/*`` read only
    ``request.state.actor_id``, set solely for gateway-header / API-key callers,
    so every dashboard-session write 401'd).

    Security: ``actor_id`` is NEVER read from a browser-supplied header directly.
    ``resolve_identity`` derives it from the validated credential (session /
    API-key record / deterministic agent uuid). The raw ``X-EB-Actor-Id``
    fallback below only covers non-middleware/test paths, and even there the
    legacy header is the lowest-precedence method inside ``resolve_identity``
    — a resolved session always wins over any spoofed actor header.
    """
    container = request.app.state.container
    identity = get_identity(request)
    actor_id_str = str(identity.actor_id) if getattr(identity, "actor_id", None) else ""
    if not actor_id_str:
        # Non-middleware / test path: fall back to the raw header stamp so the
        # legacy Phase 8 trust boundary keeps working when AuthMiddleware is
        # not wired.
        actor_id_str = getattr(request.state, "actor_id", "") or request.headers.get("X-EB-Actor-Id", "")
    return container, actor_id_str


async def _auth(request: Request, action: str, target_org_id: str | None = None, target_team_id: str | None = None):
    """Authorize the resolved caller for ``action`` via the per-action rule store.

    Authorization stays on the per-action authority model (``check_authority`` +
    ``AuthorityRuleStore``) so the admin-configurable authority rules and
    org/team matching constraints are preserved. Only the *identity resolution*
    changed (RC-1): the caller now comes from the middleware-resolved
    ``AuthIdentity`` rather than a trusted header, so dashboard sessions,
    API keys, and gateway identity all authorize consistently.
    """
    container, actor_id_str = _get_deps(request)
    # Lazy bootstrap detection (avoids Neo4j connection during container init)
    bootstrap = False
    if hasattr(container, "check_bootstrap_mode"):
        bootstrap = await container.check_bootstrap_mode()
    elif hasattr(container, "_bootstrap_mode"):
        bootstrap = getattr(container, "_bootstrap_mode", False) or False
    if not actor_id_str and not bootstrap:
        raise HTTPException(status_code=401, detail="Authentication required for admin operations")
    aid = uuid.UUID(actor_id_str) if actor_id_str else uuid.uuid4()
    return await check_authority(
        container.actor_registry,
        container.authority_store,
        aid, action,
        target_org_id=target_org_id,
        target_team_id=target_team_id,
        bootstrap_mode=bootstrap,
        metrics=getattr(container, "metrics_ctx", None),
        trace_ledger=getattr(container, "trace_ledger", None),
    )


# ---------------------------------------------------------------------------
# Bootstrap status
# ---------------------------------------------------------------------------

@router.get("/bootstrap-status")
async def get_bootstrap_status(request: Request):
    container = request.app.state.container
    if hasattr(container, "check_bootstrap_mode"):
        mode = await container.check_bootstrap_mode()
    else:
        mode = getattr(container, "_bootstrap_mode", False) or False
    return {"bootstrap_mode": mode}


# ---------------------------------------------------------------------------
# Authority rules
# ---------------------------------------------------------------------------

@router.get("/authority-rules")
async def list_authority_rules(request: Request):
    container = request.app.state.container
    rules = await container.authority_store.get_rules()
    return rules


@router.put("/authority-rules/{action}")
async def update_authority_rule(action: str, body: UpdateAuthorityRuleRequest, request: Request):
    await _auth(request, "create_org")  # system admin required
    container = request.app.state.container
    rule_data = body.model_dump(exclude_none=True)
    await container.authority_store.set_rule(action, rule_data)
    return {"action": action, "rule": rule_data}


@router.delete("/authority-rules/{action}")
async def reset_authority_rule(action: str, request: Request):
    """TD-19: reset a custom authority rule back to its shipped default.

    Drops the SQLite override (via ``delete_rule`` when the store exposes it)
    so ``get_rule`` falls back to ``AUTHORITY_DEFAULTS``. When no native delete
    exists, the override is overwritten with the shipped default, which is
    behaviourally identical for known actions.
    """
    await _auth(request, "create_org")  # system admin required
    container = request.app.state.container
    store = container.authority_store
    from elephantbroker.runtime.profiles.authority_store import AUTHORITY_DEFAULTS
    default = AUTHORITY_DEFAULTS.get(action)
    deleter = getattr(store, "delete_rule", None)
    if deleter is not None:
        removed = await deleter(action)
        if not removed and default is None:
            raise HTTPException(status_code=404, detail=f"No authority rule for action: {action}")
    else:
        if default is None:
            raise HTTPException(
                status_code=404, detail=f"No default authority rule for action: {action}"
            )
        await store.set_rule(action, dict(default))
    reset = await store.get_rule(action)
    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("reset_authority_rule", "success")
    return {"action": action, "rule": reset, "status": "reset"}


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

@router.post("/organizations")
async def create_organization(body: CreateOrgRequest, request: Request):
    await _auth(request, "create_org")
    container = request.app.state.container
    org_id = str(uuid.uuid4())
    dp = OrganizationDataPoint(
        id=uuid.UUID(org_id), name=body.name,
        display_label=body.display_label or body.name[:20],
        eb_id=org_id,
    )
    try:
        await add_data_points([dp])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store organization: {exc}") from exc

    await container.trace_ledger.append_event(TraceEvent(
        event_type=TraceEventType.ORG_CREATED,
        payload={"org_id": org_id, "name": body.name},
    ))
    logger.info("Created organization: %s (%s)", body.name, org_id)
    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("create_org", "success")
    return {"org_id": org_id, "name": body.name, "display_label": dp.display_label}


@router.get("/organizations")
async def list_organizations(request: Request):
    await _auth(request, "register_actor")  # authority >= 70
    container = request.app.state.container
    records = await container.graph.query_cypher(
        "MATCH (o:OrganizationDataPoint) RETURN properties(o) AS props"
    )
    return [{"org_id": r["props"].get("eb_id"), "name": r["props"].get("name"),
             "display_label": r["props"].get("display_label", "")} for r in records]


@router.put("/organizations/{org_id}")
async def update_organization(org_id: str, body: CreateOrgRequest, request: Request):
    await _auth(request, "create_org")
    container = request.app.state.container
    entity = await container.graph.get_entity(org_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Organization not found")
    dp = OrganizationDataPoint(
        id=uuid.UUID(org_id), name=body.name,
        display_label=body.display_label or body.name[:20],
        eb_id=org_id,
    )
    await add_data_points([dp])
    return {"org_id": org_id, "name": body.name, "display_label": dp.display_label}


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

@router.post("/teams")
async def create_team(body: CreateTeamRequest, request: Request):
    await _auth(request, "create_team", target_org_id=body.org_id)
    container = request.app.state.container
    team_id = str(uuid.uuid4())
    dp = TeamDataPoint(
        id=uuid.UUID(team_id), name=body.name,
        display_label=body.display_label or body.name[:20],
        org_id=body.org_id, eb_id=team_id,
    )
    await add_data_points([dp])
    # BELONGS_TO edge: team → org. No assert_same_gateway — Org/TeamDataPoint
    # have no gateway_id (Phase 8: business entities span gateways). The _auth
    # call above provides access control.
    await container.graph.add_relation(team_id, body.org_id, "BELONGS_TO")

    await container.trace_ledger.append_event(TraceEvent(
        event_type=TraceEventType.TEAM_CREATED,
        payload={"team_id": team_id, "org_id": body.org_id, "name": body.name},
    ))
    logger.info("Created team: %s in org %s (%s)", body.name, body.org_id, team_id)
    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("create_team", "success")
        metrics.inc_org_team_edge("BELONGS_TO", "created")
    return {"team_id": team_id, "name": body.name, "org_id": body.org_id, "display_label": dp.display_label}


@router.get("/teams")
async def list_teams(request: Request, org_id: str | None = None):
    await _auth(request, "add_team_member", target_org_id=org_id)  # authority >= 50
    container = request.app.state.container
    if org_id:
        records = await container.graph.query_cypher(
            "MATCH (t:TeamDataPoint)-[:BELONGS_TO]->(o:OrganizationDataPoint {eb_id: $org_id}) "
            "RETURN properties(t) AS props",
            {"org_id": org_id},
        )
    else:
        records = await container.graph.query_cypher(
            "MATCH (t:TeamDataPoint) RETURN properties(t) AS props"
        )
    return [{"team_id": r["props"].get("eb_id"), "name": r["props"].get("name"),
             "org_id": r["props"].get("org_id"), "display_label": r["props"].get("display_label", "")}
            for r in records]


@router.put("/teams/{team_id}")
async def update_team(team_id: str, body: CreateTeamRequest, request: Request):
    await _auth(request, "create_team", target_org_id=body.org_id)
    container = request.app.state.container
    entity = await container.graph.get_entity(team_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Team not found")
    dp = TeamDataPoint(
        id=uuid.UUID(team_id), name=body.name,
        display_label=body.display_label or body.name[:20],
        org_id=body.org_id, eb_id=team_id,
    )
    await add_data_points([dp])
    return {"team_id": team_id, "name": body.name, "org_id": body.org_id}


# ---------------------------------------------------------------------------
# Team membership
# ---------------------------------------------------------------------------

@router.post("/teams/{team_id}/members")
async def add_team_member(team_id: str, body: AddMemberRequest, request: Request):
    await _auth(request, "add_team_member", target_team_id=team_id)
    container = request.app.state.container
    # R2-P7 / link-spam guard: validate the supplied actor_id (and team_id)
    # both belong to the caller's gateway. PermissionError → 403 via R2-P5
    # middleware. Closes the cross-gateway membership-injection surface
    # where a privileged caller in tenant A could attach an actor from
    # tenant B to one of A's teams (or vice versa).
    # Canonical None-guard pattern (see trace.py:25-39 + walker
    # rationale in test_gateway_id_usage_walker.py): the middleware
    # always stamps request.state.gateway_id to a string (possibly
    # ""), so `is None` only short-circuits when middleware isn't
    # wired (test paths). A truthy `or` would treat "" as "missing"
    # and silently fall back to container.gateway_id, which collides
    # with the middleware contract that empty-string is a valid
    # stamp.
    gw_id = getattr(request.state, "gateway_id", None)
    if gw_id is None:
        gw_id = container.gateway_id
    await assert_same_gateway(container.graph, body.actor_id, gw_id)
    await assert_same_gateway(container.graph, team_id, gw_id)
    await container.graph.add_relation(body.actor_id, team_id, "MEMBER_OF")
    # Sync team_ids node property (dual-write: edge + property).
    # Edge mutation already succeeded; property sync failure is non-fatal.
    try:
        actor_entity = await container.graph.get_entity(body.actor_id)
        if actor_entity:
            dp = ActorDataPoint.from_entity_dict(actor_entity)
            if team_id not in dp.team_ids:
                dp.team_ids = list(dp.team_ids) + [team_id]
                await add_data_points([dp])
    except Exception as exc:
        logger.warning(
            "team_ids dual-write failed for actor=%s team=%s: %s",
            body.actor_id, team_id, exc,
        )
        if container.trace_ledger:
            await container.trace_ledger.append_event(TraceEvent(
                event_type=TraceEventType.DEGRADED_OPERATION,
                payload={
                    "operation": "add_team_member_dual_write",
                    "actor_id": body.actor_id,
                    "team_id": team_id,
                    "error": str(exc),
                },
            ))
    await container.trace_ledger.append_event(TraceEvent(
        event_type=TraceEventType.MEMBER_ADDED,
        payload={"actor_id": body.actor_id, "team_id": team_id},
    ))
    logger.info("Added member %s to team %s", body.actor_id, team_id)
    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("add_member", "success")
        metrics.inc_org_team_edge("MEMBER_OF", "created")
    return {"actor_id": body.actor_id, "team_id": team_id, "status": "added"}


@router.delete("/teams/{team_id}/members/{actor_id}")
async def remove_team_member(team_id: str, actor_id: str, request: Request):
    await _auth(request, "remove_team_member", target_team_id=team_id)
    container = request.app.state.container
    # R2-P7 / link-spam guard: validate the supplied actor_id and team_id
    # both belong to the caller's gateway before deleting the edge. A
    # privileged caller in tenant A must not be able to delete tenant B's
    # MEMBER_OF edges via a guessed-id DELETE. PermissionError → 403 via
    # R2-P5 middleware.
    # Canonical None-guard pattern (see trace.py:25-39 + walker
    # rationale in test_gateway_id_usage_walker.py): the middleware
    # always stamps request.state.gateway_id to a string (possibly
    # ""), so `is None` only short-circuits when middleware isn't
    # wired (test paths). A truthy `or` would treat "" as "missing"
    # and silently fall back to container.gateway_id, which collides
    # with the middleware contract that empty-string is a valid
    # stamp.
    gw_id = getattr(request.state, "gateway_id", None)
    if gw_id is None:
        gw_id = container.gateway_id
    await assert_same_gateway(container.graph, actor_id, gw_id)
    await assert_same_gateway(container.graph, team_id, gw_id)
    if hasattr(container.graph, "delete_relation"):
        await container.graph.delete_relation(actor_id, team_id, "MEMBER_OF")
    else:
        # Fallback: use Cypher directly
        await container.graph.query_cypher(
            "MATCH (a {eb_id: $aid})-[r:MEMBER_OF]->(t {eb_id: $tid}) DELETE r",
            {"aid": actor_id, "tid": team_id},
        )
    # Sync team_ids node property (dual-write: edge + property).
    # Edge mutation already succeeded; property sync failure is non-fatal.
    try:
        actor_entity = await container.graph.get_entity(actor_id)
        if actor_entity:
            dp = ActorDataPoint.from_entity_dict(actor_entity)
            if team_id in dp.team_ids:
                dp.team_ids = [t for t in dp.team_ids if t != team_id]
                await add_data_points([dp])
    except Exception as exc:
        logger.warning(
            "team_ids dual-write failed for actor=%s team=%s: %s",
            actor_id, team_id, exc,
        )
        if container.trace_ledger:
            await container.trace_ledger.append_event(TraceEvent(
                event_type=TraceEventType.DEGRADED_OPERATION,
                payload={
                    "operation": "remove_team_member_dual_write",
                    "actor_id": actor_id,
                    "team_id": team_id,
                    "error": str(exc),
                },
            ))
    await container.trace_ledger.append_event(TraceEvent(
        event_type=TraceEventType.MEMBER_REMOVED,
        payload={"actor_id": actor_id, "team_id": team_id},
    ))
    logger.info("Removed member %s from team %s", actor_id, team_id)
    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("remove_member", "success")
        metrics.inc_org_team_edge("MEMBER_OF", "deleted")
    return {"actor_id": actor_id, "team_id": team_id, "status": "removed"}


@router.get("/teams/{team_id}/members")
async def list_team_members(
    team_id: str, request: Request, include_inactive: bool = Query(False)
):
    """List a team's member actors. Soft-deactivated actors are hidden by
    default; pass ``include_inactive=true`` to return everything."""
    await _auth(request, "add_team_member", target_team_id=team_id)
    container = request.app.state.container
    # ``active`` was introduced after early actors were stored, so NULL means
    # default-active.
    active_clause = "" if include_inactive else "WHERE (a.active = true OR a.active IS NULL) "
    records = await container.graph.query_cypher(
        "MATCH (a:ActorDataPoint)-[:MEMBER_OF]->(t:TeamDataPoint {eb_id: $team_id}) "
        + active_clause + "RETURN properties(a) AS props",
        {"team_id": team_id},
    )
    return [{"actor_id": r["props"].get("eb_id"), "display_name": r["props"].get("display_name"),
             "actor_type": r["props"].get("actor_type"), "authority_level": r["props"].get("authority_level", 0)}
            for r in records]


# ---------------------------------------------------------------------------
# Actors
# ---------------------------------------------------------------------------

@router.get("/actors")
async def list_actors(
    request: Request,
    org_id: str | None = None,
    include_inactive: bool = Query(False),
):
    """List gateway actors. Soft-deactivated actors (``active=false`` — merged
    duplicates, offboarded operators) are hidden by default; pass
    ``include_inactive=true`` to return everything."""
    await _auth(request, "register_actor")
    container = request.app.state.container
    # Post-Bucket-A: middleware default is "" not "local". See TD-41.
    gw_id = getattr(request.state, "gateway_id", "")
    # ``active`` was introduced after early actors were stored, so NULL means
    # default-active.
    active_clause = "" if include_inactive else "AND (a.active = true OR a.active IS NULL) "
    if org_id:
        records = await container.graph.query_cypher(
            "MATCH (a:ActorDataPoint) WHERE a.gateway_id = $gw AND a.org_id = $org "
            + active_clause + "RETURN properties(a) AS props",
            {"gw": gw_id, "org": org_id},
        )
    else:
        records = await container.graph.query_cypher(
            "MATCH (a:ActorDataPoint) WHERE a.gateway_id = $gw "
            + active_clause + "RETURN properties(a) AS props",
            {"gw": gw_id},
        )
    return [{"actor_id": r["props"].get("eb_id"), "display_name": r["props"].get("display_name"),
             "actor_type": r["props"].get("actor_type"), "authority_level": r["props"].get("authority_level", 0)}
            for r in records]


@router.get("/actors/resolve")
async def resolve_actor_by_handle(request: Request, handle: str = Query(...)):
    await _auth(request, "register_actor")
    container = request.app.state.container
    actor = await container.actor_registry.resolve_by_handle(handle)
    if actor is None:
        raise HTTPException(status_code=404, detail=f"No actor found for handle: {handle}")
    return actor.model_dump(mode="json")


@router.post("/actors")
async def register_actor(request: Request):
    body = await request.json()
    await _auth(request, "register_actor")
    if not str(body.get("display_name") or "").strip():
        raise HTTPException(status_code=422, detail="display_name is required and must be non-empty")
    container = request.app.state.container
    from elephantbroker.schemas.actor import ActorRef, ActorType
    actor = ActorRef(
        type=ActorType(body.get("type", "worker_agent")),
        display_name=body.get("display_name", ""),
        authority_level=body.get("authority_level", 0),
        handles=body.get("handles", []),
        org_id=uuid.UUID(body["org_id"]) if body.get("org_id") else None,
        team_ids=[uuid.UUID(t) for t in body.get("team_ids", [])],
        gateway_id=getattr(request.state, "gateway_id", ""),
    )
    result = await container.actor_registry.register_actor(actor)
    # Disable bootstrap mode after first actor creation
    if getattr(container, "_bootstrap_mode", False) or getattr(container, "_bootstrap_mode", None) is True:
        container._bootstrap_mode = False
        container._bootstrap_checked = True
        logger.info("Bootstrap mode disabled after first actor creation")
    return result.model_dump(mode="json")


@router.put("/actors/{actor_id}")
async def update_actor(actor_id: str, request: Request):
    body = await request.json()
    await _auth(request, "register_actor")
    container = request.app.state.container
    actor = await container.actor_registry.resolve_actor(uuid.UUID(actor_id))
    if not actor:
        raise HTTPException(status_code=404, detail="Actor not found")
    # Apply updates
    if "authority_level" in body:
        actor.authority_level = body["authority_level"]
    if "handles" in body:
        actor.handles = body["handles"]
    if "display_name" in body:
        actor.display_name = body["display_name"]
    # Re-store
    await container.actor_registry.register_actor(actor)
    return actor.model_dump(mode="json")


class CreateDashboardUserRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    actor_id: str = Field(min_length=1)


@router.post("/dashboard-users")
async def create_dashboard_user(request: Request):
    """Create a SuperTokens email/password login and bind it to an existing actor.

    Deterministic dashboard-admin provisioning: ``ebrun bootstrap`` calls this
    (authenticated as the just-created authority-90 admin actor) so the operator
    gets a ready-to-use dashboard login instead of a self-service signup that
    lands at authority 0 and needs manual elevation. The created SuperTokens user
    is linked to ``actor_id`` two ways — the ``eb_actor_id`` user-metadata mapping
    (the fast path in ``resolve_actor_from_st_user``) AND a stable
    ``dashboard:{st_user_id}`` handle on the actor — so the first (and every)
    dashboard login resolves straight to that actor and inherits its authority.

    Authority cap (privilege-escalation guard). The base gate is ``register_actor``
    (authority >= 70), but that alone would let a level-70 caller mint a login for
    a level-90 actor and then log in AS admin. So we additionally require the
    CALLER's authority >= the TARGET actor's authority (mirrors how
    ``create_api_key`` caps the granted level at the caller's own). The bootstrap
    admin linking to itself (90 -> 90) passes; a genuine 70 -> 90 attempt is 403.
    (The raw ``X-EB-Actor-Id`` legacy-header trust is a pre-existing property of
    the whole ``/admin`` surface — this cap protects against an *authenticated*
    lower-authority principal, e.g. a dashboard session, escalating.)
    """
    body = await request.json()
    await _auth(request, "register_actor")  # base floor: authority >= 70
    container, caller_id = _get_deps(request)

    payload = CreateDashboardUserRequest(**body)
    email = payload.email.strip().lower()
    target_actor_id = payload.actor_id.strip()

    # Target actor must exist.
    target = await container.actor_registry.resolve_actor(uuid.UUID(target_actor_id))
    if target is None:
        raise HTTPException(status_code=404, detail="Target actor not found")

    # Privilege-escalation guard: caller authority >= target authority.
    caller = None
    if caller_id:
        try:
            caller = await container.actor_registry.resolve_actor(uuid.UUID(caller_id))
        except Exception:  # noqa: BLE001 - malformed / absent caller id
            caller = None
    caller_authority = getattr(caller, "authority_level", 0) or 0
    target_authority = getattr(target, "authority_level", 0) or 0
    if caller_authority < target_authority:
        raise HTTPException(
            status_code=403,
            detail=(
                "Caller authority is below the target actor's authority; cannot "
                "create a dashboard login for a higher-authority actor."
            ),
        )

    # SuperTokens must be initialized to create an emailpassword user.
    from elephantbroker.api.auth import supertokens_config

    if not supertokens_config.is_initialized():
        raise HTTPException(
            status_code=503,
            detail="Dashboard auth (SuperTokens) is not enabled/initialized.",
        )

    # Heavy optional dependency — import lazily, only on this path.
    from supertokens_python.recipe.emailpassword.asyncio import sign_up

    try:
        result = await sign_up("public", email, payload.password)
    except Exception as exc:  # noqa: BLE001 - ST core down / network
        logger.warning("dashboard-user sign_up failed for %s: %s", email, exc)
        raise HTTPException(status_code=502, detail=f"SuperTokens sign_up error: {exc}")

    # Version-robust result handling: check ``.status`` rather than isinstance so
    # this survives SDK class-name drift across supertokens-python generations.
    status = getattr(result, "status", None)
    if status == "OK":
        st_user_id = result.user.id  # primary user id == session.get_user_id()
        created = True
    elif status == "EMAIL_ALREADY_EXISTS_ERROR":
        raise HTTPException(
            status_code=409,
            detail=(
                f"A dashboard account already exists for {email}. Log in with it, "
                "or elevate its actor manually via PUT /admin/actors/{id}."
            ),
        )
    else:
        raise HTTPException(
            status_code=502, detail=f"SuperTokens sign_up returned status {status!r}"
        )

    # Link ST user -> actor: metadata mapping (fast path) + stable dashboard handle.
    await _persist_actor_mapping(st_user_id, target_actor_id)
    handle = _dashboard_handle(st_user_id)
    handles = list(getattr(target, "handles", []) or [])
    if handle not in handles:
        handles.append(handle)
        target.handles = handles
        await container.actor_registry.register_actor(target)

    logger.info(
        "Created dashboard login %s (st_user=%s) linked to actor %s (authority %s)",
        email, st_user_id, target_actor_id, target_authority,
    )
    return {
        "st_user_id": st_user_id,
        "email": email,
        "linked_actor_id": target_actor_id,
        "created": created,
    }


@router.post("/actors/{actor_id}/merge")
async def merge_actors(actor_id: str, request: Request):
    """Merge a duplicate actor into a surviving actor.

    actors-orgs-4: a *correct* merge must re-point every inbound/outbound edge
    (MEMBER_OF, CREATED_BY, REPORTS_TO/SUPERVISES, ABOUT_ACTOR, …) AND remap the
    scalar/list actor-reference properties carried on Fact/Goal/Artifact/
    Evidence/Procedure DataPoints, then soft-deactivate the duplicate
    (preserving it for provenance) — a cross-node graph refactor that belongs on
    ``IActorRegistry.merge_actors`` (backed by graph refactor primitives), not
    an ad-hoc route handler. When the registry exposes that capability we
    delegate to it; otherwise we return a stable 501 the dashboard already
    detects to disable the action.

    The primary *source* of duplicate dashboard actors — non-idempotent
    first-login actor creation — is fixed at the root in
    ``api/auth/identity.resolve_actor_from_st_user`` (actors-orgs-2), so the
    merge tool is a rarely-needed cleanup rather than a routine necessity.
    """
    body = await request.json()
    await _auth(request, "merge_actors")
    container = request.app.state.container
    duplicate_id = body.get("duplicate_id")
    if not duplicate_id:
        raise HTTPException(status_code=400, detail="duplicate_id required")
    if hasattr(container.actor_registry, "merge_actors"):
        result = await container.actor_registry.merge_actors(
            uuid.UUID(actor_id), uuid.UUID(duplicate_id)
        )
        return result.model_dump(mode="json")
    raise HTTPException(
        status_code=501,
        detail="Actor merge is not supported by this server build.",
    )


async def _revoke_actor_sessions(actor) -> int:
    """Best-effort revocation of SuperTokens sessions for a dashboard actor.

    The dashboard maps a SuperTokens user to an actor whose handle is
    ``dashboard:{st_user_id}`` (see ``api/auth/identity.py``). ST is a HEAVY,
    OPTIONAL dependency, so it is lazy-imported and every failure degrades to a
    no-op — a deactivated actor with no dashboard handle (e.g. an agent) simply
    revokes nothing.
    """
    st_user_ids = [
        h.split("dashboard:", 1)[1]
        for h in (getattr(actor, "handles", None) or [])
        if isinstance(h, str) and h.startswith("dashboard:")
    ]
    if not st_user_ids:
        return 0
    try:
        from supertokens_python.recipe.session.asyncio import (
            revoke_all_sessions_for_user,
        )
    except Exception as exc:  # ST not installed / not configured
        logger.debug("SuperTokens session revocation unavailable: %s", exc)
        return 0
    revoked = 0
    for uid in st_user_ids:
        try:
            handles = await revoke_all_sessions_for_user(uid)
            revoked += len(handles) if handles else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to revoke sessions for ST user %s: %s", uid, exc)
    return revoked


@router.put("/actors/{actor_id}/status")
async def set_actor_status(actor_id: str, body: SetActorStatusRequest, request: Request):
    """TD-22 (Phase 11): soft-(de)activate an actor.

    Deactivation flips ``active=False`` (hiding the actor from active lists while
    preserving the node for provenance — actors are never DETACH DELETE'd) and
    revokes any live SuperTokens dashboard sessions bound to the actor so a
    deactivated operator cannot keep using the dashboard.
    """
    await _auth(request, "register_actor")
    container = request.app.state.container
    actor = await container.actor_registry.resolve_actor(uuid.UUID(actor_id))
    if not actor:
        raise HTTPException(status_code=404, detail="Actor not found")
    actor.active = body.active
    await container.actor_registry.register_actor(actor)

    revoked_sessions = 0
    if not body.active:
        revoked_sessions = await _revoke_actor_sessions(actor)

    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("set_actor_status", "success")
    logger.info(
        "Actor %s active=%s (revoked %d sessions)", actor_id, body.active, revoked_sessions
    )
    return {"actor_id": actor_id, "active": body.active, "revoked_sessions": revoked_sessions}


@router.put("/actors/{actor_id}/organization")
async def set_actor_organization(actor_id: str, body: SetActorOrgRequest, request: Request):
    """Set or clear an actor's organization membership (actors-orgs-10).

    Org membership is modeled as the ``org_id`` *property* on the actor node —
    every read path proves this: ``admin.list_actors(org_id=…)`` filters on
    ``a.org_id``, the dashboard org list counts actors via ``a.org_id = o.eb_id``,
    and the actor-detail endpoint returns ``org_id`` from the actor's props.
    Unlike team membership (which needs a ``MEMBER_OF`` edge because reads
    traverse it), org membership has no edge reader, so the property IS the
    model. Nothing previously *wrote* it, which is why the org 'Actors' card, the
    org actor count, and the actor-detail org section were permanently empty.

    Mirrors the team-membership handlers: same ``_auth`` gate (``register_actor``,
    the action that already governs every actor mutation) with the target org
    passed for org-matching rules, and the same ``assert_same_gateway`` link-spam
    guard on the actor. The write goes through ``ActorRegistry.register_actor``
    (the canonical actor persistence path, as ``update_actor`` /
    ``set_actor_status`` already do) so the ``org_id`` change upserts in place.
    """
    new_org = (body.org_id or "").strip() or None
    await _auth(request, "register_actor", target_org_id=new_org)
    container = request.app.state.container

    # Link-spam guard: the actor must belong to the caller's gateway. Orgs carry
    # no gateway_id (business entities span gateways), so assert_same_gateway on
    # the org is a no-op — instead we verify the org exists.
    # Canonical None-guard: middleware always stamps request.state.gateway_id to
    # a string (possibly ""); `is None` only short-circuits when middleware is
    # unwired (test paths). A truthy `or` would misread "" as missing.
    gw_id = getattr(request.state, "gateway_id", None)
    if gw_id is None:
        gw_id = container.gateway_id
    await assert_same_gateway(container.graph, actor_id, gw_id)
    if new_org:
        org_entity = await container.graph.get_entity(new_org)
        if not org_entity:
            raise HTTPException(status_code=404, detail="Organization not found")

    actor = await container.actor_registry.resolve_actor(uuid.UUID(actor_id))
    if not actor:
        raise HTTPException(status_code=404, detail="Actor not found")
    actor.org_id = uuid.UUID(new_org) if new_org else None
    await container.actor_registry.register_actor(actor)

    await container.trace_ledger.append_event(TraceEvent(
        # Reuse the generic membership events (as team membership does); the
        # payload discriminator distinguishes org from team membership.
        event_type=TraceEventType.MEMBER_ADDED if new_org else TraceEventType.MEMBER_REMOVED,
        actor_ids=[actor.id],
        payload={"actor_id": actor_id, "org_id": new_org or "", "membership": "organization"},
    ))
    logger.info(
        "Set actor %s organization -> %s", actor_id, new_org or "(cleared)"
    )
    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("set_actor_org", "success")
    return {"actor_id": actor_id, "org_id": new_org, "status": "set" if new_org else "cleared"}


# ---------------------------------------------------------------------------
# Persistent goals
# ---------------------------------------------------------------------------

SCOPE_ACTION_MAP = {
    "global": "create_global_goal",
    "organization": "create_org_goal",
    "team": "create_team_goal",
    "actor": "create_actor_goal",
}


@router.post("/goals")
async def create_persistent_goal(body: CreatePersistentGoalRequest, request: Request):
    action = SCOPE_ACTION_MAP.get(body.scope, "create_global_goal")
    await _auth(request, action, target_org_id=body.org_id, target_team_id=body.team_id)
    container = request.app.state.container
    gw_id = getattr(request.state, "gateway_id", "")

    goal = GoalState(
        title=body.title, description=body.description,
        scope=Scope(body.scope), status=GoalStatus.ACTIVE,
        parent_goal_id=uuid.UUID(body.parent_goal_id) if body.parent_goal_id else None,
        success_criteria=body.success_criteria,
        owner_actor_ids=[uuid.UUID(a) for a in body.owner_actor_ids],
        gateway_id=gw_id,
        org_id=uuid.UUID(body.org_id) if body.org_id else None,
        team_id=uuid.UUID(body.team_id) if body.team_id else None,
    )
    result = await container.goal_manager.set_goal(goal)

    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_goal_create()

    await container.trace_ledger.append_event(TraceEvent(
        event_type=TraceEventType.PERSISTENT_GOAL_CREATED,
        payload={"goal_id": str(result.id), "scope": body.scope, "org_id": body.org_id or ""},
    ))
    return result.model_dump(mode="json")


@router.get("/goals")
async def list_persistent_goals(
    request: Request,
    scope: str | None = None,
    org_id: str | None = None,
    status: str | None = None,
):
    """List persistent goals, gateway-scoped.

    goals-procedures-7 fix: previously hard-filtered ``status = 'active'``, so
    paused/completed/cancelled roots silently vanished from the UI. Now returns
    ALL statuses by default (each goal's ``status`` is included in the returned
    ``props`` so the UI can render/group by it), with an optional ``status``
    query filter for callers that only want one state.
    """
    container = request.app.state.container
    gw_id = getattr(request.state, "gateway_id", "")
    cypher = "MATCH (g:GoalDataPoint) WHERE g.gateway_id = $gw"
    params: dict = {"gw": gw_id}
    if status:
        cypher += " AND g.status = $status"
        params["status"] = status
    if scope:
        cypher += " AND g.scope = $scope"
        params["scope"] = scope
    if org_id:
        cypher += " AND g.org_id = $org_id"
        params["org_id"] = org_id
    cypher += " RETURN properties(g) AS props"
    records = await container.graph.query_cypher(cypher, params)
    return [r["props"] for r in records]


@router.get("/goals/{goal_id}")
async def get_persistent_goal(goal_id: str, request: Request):
    container = request.app.state.container
    entity = await container.graph.get_entity(goal_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Goal not found")
    return entity


@router.put("/goals/{goal_id}")
async def update_persistent_goal(goal_id: str, request: Request):
    body = await request.json()
    container = request.app.state.container
    entity = await container.graph.get_entity(goal_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Goal not found")
    if "status" in body:
        await container.goal_manager.update_goal_status(uuid.UUID(goal_id), GoalStatus(body["status"]))
    return {"goal_id": goal_id, "updated": True}


@router.post("/goals/{goal_id}/blocker")
async def add_persistent_goal_blocker(goal_id: str, body: AddGoalBlockerRequest, request: Request):
    """TD-19: append a blocker to a PERSISTENT goal (session-goal parity with
    ``routes/goals.add_session_goal_blocker``, but against the Cognee-backed
    goal store)."""
    await _auth(request, "create_global_goal")
    container = request.app.state.container
    gw_id = getattr(request.state, "gateway_id", "")
    entity = await container.graph.get_entity(goal_id, gateway_id=gw_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Goal not found")

    from elephantbroker.runtime.adapters.cognee.datapoints import GoalDataPoint
    from elephantbroker.runtime.graph_utils import clean_graph_props

    goal = GoalDataPoint(**clean_graph_props(entity)).to_schema()
    if body.blocker not in goal.blockers:
        goal.blockers = list(goal.blockers) + [body.blocker]
    goal.gateway_id = goal.gateway_id or gw_id
    await add_data_points([GoalDataPoint.from_schema(goal)])  # MERGE-by-id upsert

    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_goal_hint("blocker")
    await container.trace_ledger.append_event(TraceEvent(
        event_type=TraceEventType.SESSION_GOAL_BLOCKER_ADDED,
        goal_ids=[goal.id],
        payload={"blocker": body.blocker, "goal_id": goal_id},
    ))
    return goal.model_dump(mode="json")


@router.post("/goals/{goal_id}/subgoal")
async def add_persistent_subgoal(goal_id: str, body: CreateSubgoalRequest, request: Request):
    """TD-19: create a child of a PERSISTENT goal (session-goal parity with
    ``routes/goals.create_session_goal``'s parent linking). The subgoal inherits
    the parent's scope/org/team and is linked via the CHILD_OF edge that
    ``GoalManager.set_goal`` creates when ``parent_goal_id`` is set."""
    container = request.app.state.container
    gw_id = getattr(request.state, "gateway_id", "")
    entity = await container.graph.get_entity(goal_id, gateway_id=gw_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Parent goal not found")

    from elephantbroker.runtime.adapters.cognee.datapoints import GoalDataPoint
    from elephantbroker.runtime.graph_utils import clean_graph_props

    parent = GoalDataPoint(**clean_graph_props(entity)).to_schema()
    parent_scope = parent.scope.value if hasattr(parent.scope, "value") else str(parent.scope)
    action = SCOPE_ACTION_MAP.get(parent_scope, "create_global_goal")
    await _auth(
        request, action,
        target_org_id=str(parent.org_id) if parent.org_id else None,
        target_team_id=str(parent.team_id) if parent.team_id else None,
    )

    subgoal = GoalState(
        title=body.title, description=body.description,
        scope=parent.scope, status=GoalStatus.ACTIVE,
        parent_goal_id=parent.id,
        success_criteria=body.success_criteria,
        owner_actor_ids=[uuid.UUID(a) for a in body.owner_actor_ids],
        gateway_id=gw_id,
        org_id=parent.org_id,
        team_id=parent.team_id,
    )
    result = await container.goal_manager.set_goal(subgoal)

    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_goal_create()
    await container.trace_ledger.append_event(TraceEvent(
        event_type=TraceEventType.PERSISTENT_GOAL_CREATED,
        payload={"goal_id": str(result.id), "parent_goal_id": goal_id, "scope": parent_scope},
    ))
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Profile overrides
# ---------------------------------------------------------------------------

@router.get("/profiles/overrides/{org_id}")
async def list_profile_overrides(org_id: str, request: Request):
    await _auth(request, "register_org_profile_override", target_org_id=org_id)
    container = request.app.state.container
    if not container.profile_registry._org_store:
        return []
    return await container.profile_registry._org_store.list_overrides(org_id)


@router.put("/profiles/overrides/{org_id}/{profile_id}")
async def set_profile_override(org_id: str, profile_id: str, body: SetProfileOverrideRequest, request: Request):
    await _auth(request, "register_org_profile_override", target_org_id=org_id)
    container = request.app.state.container
    _, actor_id_str = _get_deps(request)
    await container.profile_registry.register_org_override(org_id, profile_id, body.overrides, actor_id=actor_id_str)
    return {"org_id": org_id, "profile_id": profile_id, "status": "set"}


@router.delete("/profiles/overrides/{org_id}/{profile_id}")
async def delete_profile_override(org_id: str, profile_id: str, request: Request):
    await _auth(request, "register_org_profile_override", target_org_id=org_id)
    container = request.app.state.container
    await container.profile_registry.delete_org_override(org_id, profile_id)
    return {"org_id": org_id, "profile_id": profile_id, "status": "deleted"}


# ---------------------------------------------------------------------------
# Fact indexes (Fix 5) — opt-in, per-index Neo4j index management
#
# DEFAULT OFF: no startup path creates these. Neo4j itself is the source of
# truth for what exists (SHOW INDEXES via fact_index_status) — no config flag
# that can drift. Indexes are database-global: enabling one affects every
# gateway sharing the Neo4j database, hence the config-class authority gate
# ("manage_indexes", level 90) on all four routes including the read.
# ---------------------------------------------------------------------------

def _get_memory_facade(request: Request):
    """Resolve the memory facade or 503 (context-only deployments have none)."""
    ms = request.app.state.container.memory_store
    if ms is None:
        raise HTTPException(
            status_code=503,
            detail="Memory store not available (context-only deployment)",
        )
    return ms


@router.get("/indexes")
async def list_fact_indexes(request: Request):
    await _auth(request, "manage_indexes")
    ms = _get_memory_facade(request)
    return {"indexes": await ms.fact_index_status()}


@router.post("/indexes/{index_name}")
async def create_fact_index(index_name: str, request: Request):
    await _auth(request, "manage_indexes")
    ms = _get_memory_facade(request)
    container = request.app.state.container
    try:
        await ms.ensure_fact_index(index_name)
    except ValueError as exc:
        # Unknown catalog name — the facade whitelist is the injection guard.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("create_fact_index", "success")
    return {"index": index_name, "status": "created"}


@router.delete("/indexes/{index_name}")
async def drop_fact_index(index_name: str, request: Request):
    await _auth(request, "manage_indexes")
    ms = _get_memory_facade(request)
    container = request.app.state.container
    try:
        await ms.drop_fact_index(index_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("drop_fact_index", "success")
    return {"index": index_name, "status": "dropped"}


@router.post("/indexes/{index_name}/rebuild")
async def rebuild_fact_index(index_name: str, request: Request):
    """Drop then re-create — Neo4j re-populates the index in the background."""
    await _auth(request, "manage_indexes")
    ms = _get_memory_facade(request)
    container = request.app.state.container
    try:
        await ms.drop_fact_index(index_name)
        await ms.ensure_fact_index(index_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    metrics = getattr(container, "metrics_ctx", None)
    if metrics:
        metrics.inc_admin_op("rebuild_fact_index", "success")
    return {"index": index_name, "status": "rebuilt"}
