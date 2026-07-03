"""Consolidation management API routes."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("elephantbroker.api.routes.consolidation")

router = APIRouter()


class RunConsolidationRequest(BaseModel):
    profile_id: str | None = None


@router.post("/run")
async def run_consolidation(body: RunConsolidationRequest, request: Request):
    """Trigger a consolidation run for this gateway."""
    from elephantbroker.runtime.consolidation.engine import ConsolidationAlreadyRunningError

    container = request.app.state.container
    engine = getattr(container, "consolidation", None)
    if engine is None:
        raise HTTPException(status_code=501, detail="Consolidation engine not available")

    gateway_id = getattr(request.state, "gateway_id", "")
    config = getattr(container, "config", None)
    org_id = ""
    if config and hasattr(config, "gateway"):
        org_id = config.gateway.org_id or ""

    try:
        report = await engine.run_consolidation(org_id, gateway_id, body.profile_id)
        return report.model_dump(mode="json")
    except ConsolidationAlreadyRunningError:
        raise HTTPException(status_code=409, detail=f"Consolidation already running for gateway {gateway_id}")


@router.get("/reports")
async def list_reports(request: Request, limit: int = 10):
    """List recent consolidation reports."""
    container = request.app.state.container
    store = getattr(container, "consolidation_report_store", None)
    if store is None:
        return []
    gateway_id = getattr(request.state, "gateway_id", "")
    reports = await store.list_reports(gateway_id, limit=limit)
    return [r.model_dump(mode="json") for r in reports]


@router.get("/reports/{report_id}")
async def get_report(report_id: str, request: Request):
    """Get a specific consolidation report."""
    container = request.app.state.container
    store = getattr(container, "consolidation_report_store", None)
    if store is None:
        raise HTTPException(status_code=501, detail="Report store not available")
    report = await store.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report.model_dump(mode="json")


@router.get("/status")
async def get_status(request: Request):
    """Check if consolidation is currently running."""
    container = request.app.state.container
    redis = getattr(container, "redis", None)
    keys = getattr(container, "redis_keys", None)
    if not redis or not keys:
        return {"running": False}
    try:
        status_raw = await redis.get(keys.consolidation_status())
        if status_raw:
            return json.loads(status_raw)
    except Exception:
        pass
    return {"running": False}


@router.get("/suggestions")
async def list_suggestions(request: Request, approval_status: str | None = None):
    """List Stage 7 procedure suggestions."""
    container = request.app.state.container
    store = getattr(container, "consolidation_report_store", None)
    if store is None:
        return []
    gateway_id = getattr(request.state, "gateway_id", "")
    return await store.list_suggestions(gateway_id, approval_status=approval_status)


class UpdateSuggestionRequest(BaseModel):
    approval_status: str  # "approved" or "rejected"


def _resolve_procedure_dataset(container) -> str:
    """Resolve the gateway-scoped Cognee dataset procedures are stored in.

    Mirrors the value the ProcedureEngine / ConsolidationEngine were wired with
    (``{gateway_id}__{default_dataset}``) so a promoted procedure lands in the
    same dataset as engine-stored ones. Falls back to the Cognee default.
    """
    for attr in ("procedure_engine", "consolidation"):
        dataset = getattr(getattr(container, attr, None), "_dataset_name", None)
        if dataset:
            return dataset
    return "elephantbroker"


def _parse_actor_id(raw):
    """Coerce the X-EB-Actor-Id request-state value into a UUID (or None)."""
    import uuid

    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


@router.patch("/suggestions/{suggestion_id}")
async def update_suggestion(suggestion_id: str, body: UpdateSuggestionRequest, request: Request):
    """Approve or reject a procedure suggestion.

    Approval is not a decorative status flag (gap-5-4): it promotes the stored
    Stage 7 draft into a durable ProcedureDefinition (Cognee-first, per
    CLAUDE.md) via ``promote_suggestion_to_procedure`` and returns the created
    ``procedure_id``. The status is only marked ``approved`` once promotion
    succeeds — a failed promotion leaves the suggestion pending so the operator
    can retry rather than silently losing the draft. Rejection just records the
    status.
    """
    container = request.app.state.container
    store = getattr(container, "consolidation_report_store", None)
    if store is None:
        raise HTTPException(status_code=501, detail="Report store not available")
    if body.approval_status not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="approval_status must be 'approved' or 'rejected'")

    gateway_id = getattr(request.state, "gateway_id", "")
    suggestion = await store.get_suggestion(suggestion_id, gateway_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    metrics = getattr(container, "metrics_ctx", None)

    if body.approval_status == "rejected":
        await store.update_suggestion_status(suggestion_id, "rejected")
        if metrics:
            try:
                metrics.inc_consolidation_suggestion("rejected")
            except Exception:
                pass
        return {"id": suggestion_id, "approval_status": "rejected", "procedure_id": None}

    # approved → promote the stored draft into a durable ProcedureDefinition.
    from elephantbroker.runtime.consolidation.stages.refine_procedures import (
        promote_suggestion_to_procedure,
    )

    dataset_name = _resolve_procedure_dataset(container)
    source_actor_id = _parse_actor_id(getattr(request.state, "actor_id", ""))

    try:
        procedure = await promote_suggestion_to_procedure(
            suggestion,
            dataset_name=dataset_name,
            gateway_id=gateway_id,
            source_actor_id=source_actor_id,
        )
    except Exception:
        logger.exception("gap-5-4: promotion failed for suggestion %s", suggestion_id)
        procedure = None

    if procedure is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to promote approved suggestion into a procedure",
        )

    await store.update_suggestion_status(suggestion_id, "approved")
    if metrics:
        try:
            metrics.inc_consolidation_suggestion("approved")
        except Exception:
            pass

    return {
        "id": suggestion_id,
        "approval_status": "approved",
        "procedure_id": str(procedure.id),
    }
