"""Evidence and verification engine — claims, evidence, state transitions."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import cognee
from cognee.tasks.storage import add_data_points

from elephantbroker.runtime.adapters.cognee.datapoints import ClaimDataPoint, EvidenceDataPoint
from elephantbroker.runtime.adapters.cognee.graph import GraphAdapter
from elephantbroker.runtime.interfaces.evidence_engine import IEvidenceAndVerificationEngine
from elephantbroker.runtime.interfaces.trace_ledger import ITraceLedger
from elephantbroker.schemas.evidence import (
    ClaimRecord,
    ClaimStatus,
    EvidenceRef,
    VerificationState,
    VerificationSummary,
)
from elephantbroker.runtime.observability import GatewayLoggerAdapter
from elephantbroker.schemas.trace import TraceEvent, TraceEventType


logger = logging.getLogger(__name__)


class EvidenceAndVerificationEngine(IEvidenceAndVerificationEngine):

    def __init__(self, graph: GraphAdapter, trace_ledger: ITraceLedger,
                 dataset_name: str = "elephantbroker", gateway_id: str = "") -> None:
        self._graph = graph
        self._trace = trace_ledger
        self._claims: dict[uuid.UUID, ClaimRecord] = {}
        self._claim_sessions: dict[uuid.UUID, uuid.UUID] = {}  # claim_id -> session_id
        self._dataset_name = dataset_name
        self._gateway_id = gateway_id
        self._log = GatewayLoggerAdapter(logger, {"gateway_id": gateway_id})

    async def record_claim(self, claim: ClaimRecord, *,
                           session_id: uuid.UUID | None = None) -> ClaimRecord:
        claim.gateway_id = claim.gateway_id or self._gateway_id
        dp = ClaimDataPoint.from_schema(claim)
        await add_data_points([dp])  # CREATE
        await cognee.add(claim.claim_text, dataset_name=self._dataset_name)

        self._claims[claim.id] = claim
        if session_id is not None:
            self._claim_sessions[claim.id] = session_id
        await self._trace.append_event(
            TraceEvent(
                event_type=TraceEventType.CLAIM_MADE,
                claim_ids=[claim.id],
                payload={"action": "record_claim", "text": claim.claim_text[:100]},
            )
        )
        return claim

    # ------------------------------------------------------------------
    # Durable claim hydration (gap-4-4)
    # ------------------------------------------------------------------
    # Claims are persisted as ClaimDataPoints via add_data_points() (Cognee-first
    # per CLAUDE.md), but the engine previously read them only from a
    # process-local dict — so verify/reject/GET returned 404 after a restart
    # while the dashboard still rendered the rows. Reads and mutations now fall
    # back to reconstructing the claim (and its evidence) from the graph.

    async def _hydrate_claim(self, claim_id: uuid.UUID) -> ClaimRecord | None:
        """Return the claim from the in-memory cache, reconstructing it from the
        graph when the cache is cold (e.g. after a restart)."""
        claim = self._claims.get(claim_id)
        if claim is not None:
            return claim
        claim = await self._load_claim_from_graph(claim_id)
        if claim is not None:
            self._claims[claim_id] = claim  # warm the cache
        return claim

    async def _load_claim_from_graph(self, claim_id: uuid.UUID) -> ClaimRecord | None:
        """Reconstruct a ClaimRecord (with its supporting evidence) from the
        Cognee-persisted graph nodes/edges."""
        try:
            rows = await self._graph.query_cypher(
                "MATCH (c:ClaimDataPoint {eb_id: $cid, gateway_id: $gw}) "
                "OPTIONAL MATCH (e:EvidenceDataPoint)-[:SUPPORTS]->(c) "
                "RETURN properties(c) AS claim, collect(properties(e)) AS evidence",
                {"cid": str(claim_id), "gw": self._gateway_id},
            )
        except Exception as exc:
            self._log.warning("Failed to load claim %s from graph: %s", claim_id, exc)
            return None
        if not isinstance(rows, list) or not rows:
            return None
        first = rows[0]
        if not isinstance(first, dict):
            return None
        cp = first.get("claim")
        if not isinstance(cp, dict) or not cp:
            return None
        evidence_props = first.get("evidence")
        return self._claim_from_props(cp, evidence_props if isinstance(evidence_props, list) else [])

    def _claim_from_props(self, cp: dict, evidence_props: list) -> ClaimRecord:
        """Best-effort reconstruction of a ClaimRecord from Neo4j node props."""
        def _uuid_or_none(v):
            try:
                return uuid.UUID(v) if v else None
            except (ValueError, TypeError):
                return None

        evidence_refs: list[EvidenceRef] = []
        for ep in evidence_props:
            if not ep:
                continue
            try:
                evidence_refs.append(EvidenceRef(
                    id=_uuid_or_none(ep.get("eb_id")) or uuid.uuid4(),
                    type=ep.get("evidence_type") or "unknown",
                    ref_value=ep.get("ref_value") or "",
                    content_hash=ep.get("content_hash"),
                    created_by_actor_id=_uuid_or_none(ep.get("created_by_actor_id")),
                    gateway_id=ep.get("gateway_id") or "",
                ))
            except Exception:  # noqa: BLE001 — skip a corrupt evidence row
                continue

        try:
            status = ClaimStatus(cp.get("status", "unverified"))
        except ValueError:
            status = ClaimStatus.UNVERIFIED

        return ClaimRecord(
            id=_uuid_or_none(cp.get("eb_id")) or uuid.uuid4(),
            # ClaimDataPoint stores the text under `claim_text`; tolerate `text`.
            claim_text=cp.get("claim_text") or cp.get("text") or "(recovered claim)",
            claim_type=cp.get("claim_type") or "",
            status=status,
            evidence_refs=evidence_refs,
            procedure_id=_uuid_or_none(cp.get("procedure_id")),
            step_id=_uuid_or_none(cp.get("step_id")),
            goal_id=_uuid_or_none(cp.get("goal_id")),
            actor_id=_uuid_or_none(cp.get("actor_id")),
            # gap-4-9: first-class persisted field; None for legacy nodes written
            # before the field existed (trace-ledger fallback covers those).
            rejection_reason=cp.get("rejection_reason") or None,
            gateway_id=cp.get("gateway_id") or "",
        )

    async def _recover_rejection_reason(self, claim_id: uuid.UUID) -> str | None:
        """Recover a rejection reason from the trace ledger (gap-4-9 fallback).

        Used for claims rejected before ``rejection_reason`` became a first-class
        persisted field. Returns the most recent rejection reason, or ``None``.
        """
        try:
            events = await self._trace.get_evidence_chain(claim_id)
        except Exception:  # noqa: BLE001
            return None
        for ev in reversed(events or []):
            payload = getattr(ev, "payload", None) or {}
            if payload.get("action") == "rejected" and payload.get("reason"):
                return payload["reason"]
        return None

    async def attach_evidence(self, claim_id: uuid.UUID, evidence: EvidenceRef) -> ClaimRecord:
        claim = await self._hydrate_claim(claim_id)
        if claim is None:
            raise KeyError(f"Claim not found: {claim_id}")

        evidence.gateway_id = evidence.gateway_id or self._gateway_id
        ev_dp = EvidenceDataPoint.from_schema(evidence)
        await add_data_points([ev_dp])  # CREATE — evidence
        await cognee.add(evidence.ref_value, dataset_name=self._dataset_name)

        # (evidence)-[:SUPPORTS]->(claim) — the evidence node backs the claim.
        await self._graph.add_relation(str(evidence.id), str(claim_id), "SUPPORTS")

        # gap-4-1 / gap-4-4: when the evidence references a durable fact
        # (chunk_ref → FactDataPoint.eb_id), also link (fact)-[:SUPPORTS]->(claim)
        # so the dashboard Fact Detail "linked claims" panel — which reads
        # (fact)-[:SUPPORTS]->(claim) — populates. add_relation MERGEs by eb_id
        # and no-ops when the referenced node is not a stored FactDataPoint, so a
        # non-fact chunk_ref cannot create a spurious edge.
        if evidence.type == "chunk_ref" and evidence.ref_value:
            try:
                node = await self._graph.get_entity(evidence.ref_value, gateway_id=self._gateway_id)
                labels = node.get("_labels") if isinstance(node, dict) else None
                if labels and "FactDataPoint" in labels:
                    await self._graph.add_relation(evidence.ref_value, str(claim_id), "SUPPORTS")
            except Exception as exc:
                self._log.debug("Fact→claim SUPPORTS link skipped for %s: %s", evidence.ref_value, exc)

        claim.evidence_refs.append(evidence)
        claim.updated_at = datetime.now(UTC)

        # Auto-transition: unverified -> self_supported when evidence attached
        if claim.status == ClaimStatus.UNVERIFIED:
            claim.status = ClaimStatus.SELF_SUPPORTED

        # Update graph
        dp = ClaimDataPoint.from_schema(claim)
        await add_data_points([dp])  # UPDATE — claim status change, no cognee.add()

        await self._trace.append_event(
            TraceEvent(
                event_type=TraceEventType.CLAIM_VERIFIED,
                claim_ids=[claim_id],
                gateway_id=self._gateway_id,
                payload={
                    "action": "attach_evidence",
                    "evidence_type": evidence.type,
                    "new_status": claim.status.value,
                },
            )
        )
        return claim

    async def verify(self, claim_id: uuid.UUID) -> ClaimRecord:
        claim = await self._hydrate_claim(claim_id)
        if claim is None:
            raise KeyError(f"Claim not found: {claim_id}")

        # #1186 RESOLVED (TF-FN-019 G13): REJECTED is a terminal state.
        # Re-verifying a rejected claim would silently overwrite the
        # audit trail — the forensic record of "this claim was rejected"
        # gets replaced by "self_supported" or similar, losing the
        # reason and reviewer context. Protect the audit trail by
        # refusing the transition; callers who need to re-evaluate a
        # previously rejected claim must explicitly reset to DRAFT via
        # a separate (not yet built) admin path.
        if claim.status == ClaimStatus.REJECTED:
            raise ValueError(
                f"Cannot re-verify a rejected claim: {claim_id} — "
                f"REJECTED is a terminal state; rejecting a previously verified "
                f"claim requires explicit reset, not re-evaluation."
            )

        # State transition based on evidence types
        evidence_types = {e.type for e in claim.evidence_refs}
        if "supervisor_sign_off" in evidence_types:
            claim.status = ClaimStatus.SUPERVISOR_VERIFIED
        elif "tool_output" in evidence_types:
            claim.status = ClaimStatus.TOOL_SUPPORTED
        elif claim.evidence_refs:
            claim.status = ClaimStatus.SELF_SUPPORTED

        claim.updated_at = datetime.now(UTC)
        dp = ClaimDataPoint.from_schema(claim)
        await add_data_points([dp])  # UPDATE — no cognee.add()

        await self._trace.append_event(
            TraceEvent(
                event_type=TraceEventType.CLAIM_VERIFIED,
                claim_ids=[claim_id],
                payload={"action": "verify", "status": claim.status.value},
            )
        )
        return claim

    async def get_verification_state(self, session_id: uuid.UUID) -> VerificationSummary:
        # Filter claims by session_id when session association is available
        session_claims = [
            c for c in self._claims.values()
            if self._claim_sessions.get(c.id) == session_id
        ]
        # Fall back to all claims if no claims are session-tagged (backward compat)
        if not session_claims and self._claims:
            self._log.debug("No session-tagged claims for %s, returning all %d claims",
                           session_id, len(self._claims))
            session_claims = list(self._claims.values())

        total = len(session_claims)
        verified = sum(
            1 for c in session_claims
            if c.status in (ClaimStatus.TOOL_SUPPORTED, ClaimStatus.SUPERVISOR_VERIFIED)
        )
        pending = sum(
            1 for c in session_claims
            if c.status in (ClaimStatus.UNVERIFIED, ClaimStatus.SELF_SUPPORTED)
        )
        rejected = sum(1 for c in session_claims if c.status == ClaimStatus.REJECTED)
        return VerificationSummary(
            total_claims=total,
            verified=verified,
            pending=pending,
            disputed=0,
            retracted=rejected,
            coverage=verified / total if total > 0 else 0.0,
        )

    async def get_claim_verification(self, claim_id: uuid.UUID) -> VerificationState:
        claim = await self._hydrate_claim(claim_id)
        if claim is None:
            raise KeyError(f"Claim not found: {claim_id}")
        # gap-4-9: surface the rejection reason via the API. The durable
        # first-class field wins; fall back to recovering it from the trace
        # ledger only for claims rejected before the field existed.
        rejection_reason = claim.rejection_reason
        if not rejection_reason and claim.status == ClaimStatus.REJECTED:
            rejection_reason = await self._recover_rejection_reason(claim_id)
        return VerificationState(
            claim_id=claim.id,
            status=claim.status,
            evidence_refs=list(claim.evidence_refs),
            rejection_reason=rejection_reason,
        )

    async def get_claims_for_procedure(self, procedure_id: uuid.UUID) -> list[ClaimRecord]:
        """Return all claims for a given procedure.

        Merges the in-memory cache with claims persisted in the graph so
        procedure-completion checks survive a restart (gap-4-4). The in-memory
        copy wins on id collision because _claim_from_props is a best-effort
        reconstruction — it does not round-trip claim/evidence timestamps —
        while the in-memory record is exact. (step_id and rejection_reason are
        now durably persisted on ClaimDataPoint, so they survive either way.)
        """
        by_id: dict[uuid.UUID, ClaimRecord] = {}
        try:
            rows = await self._graph.query_cypher(
                "MATCH (c:ClaimDataPoint {gateway_id: $gw}) WHERE c.procedure_id = $pid "
                "OPTIONAL MATCH (e:EvidenceDataPoint)-[:SUPPORTS]->(c) "
                "RETURN properties(c) AS claim, collect(properties(e)) AS evidence",
                {"pid": str(procedure_id), "gw": self._gateway_id},
            )
            for row in (rows if isinstance(rows, list) else []):
                if not isinstance(row, dict):
                    continue
                cp = row.get("claim")
                if not isinstance(cp, dict) or not cp:
                    continue
                evidence_props = row.get("evidence")
                rec = self._claim_from_props(cp, evidence_props if isinstance(evidence_props, list) else [])
                by_id[rec.id] = rec
        except Exception as exc:  # noqa: BLE001
            self._log.warning("get_claims_for_procedure(%s): graph load failed: %s", procedure_id, exc)
        # In-memory copies override graph reconstructions.
        for c in self._claims.values():
            if c.procedure_id == procedure_id:
                by_id[c.id] = c
        results = list(by_id.values())
        self._log.debug("get_claims_for_procedure(%s): found %d claims", procedure_id, len(results))
        return results

    async def reject(self, claim_id: uuid.UUID, reason: str,
                     rejector_actor_id: uuid.UUID | None = None) -> ClaimRecord:
        """Explicitly reject a claim. Requires non-empty reason string."""
        if not reason or not reason.strip():
            raise ValueError("Rejection reason is required")

        claim = await self._hydrate_claim(claim_id)
        if claim is None:
            raise KeyError(f"Claim {claim_id} not found")

        claim.status = ClaimStatus.REJECTED
        claim.updated_at = datetime.now(UTC)

        # gap-4-9: rejection_reason is a first-class ClaimRecord/ClaimDataPoint
        # field, so the add_data_points() below persists it durably and
        # get_claim_verification can read it back after a restart. The trace
        # event below remains a secondary audit record and the legacy fallback
        # source for claims rejected before the field existed.
        claim.rejection_reason = reason

        dp = ClaimDataPoint.from_schema(claim)
        await add_data_points([dp])

        await self._trace.append_event(
            TraceEvent(
                event_type=TraceEventType.CLAIM_VERIFIED,
                claim_ids=[claim_id],
                payload={
                    "action": "rejected",
                    "reason": reason,
                    "rejector_actor_id": str(rejector_actor_id) if rejector_actor_id else None,
                    "claim_text": claim.claim_text[:200],
                },
            )
        )
        return claim

    async def check_completion_requirements(self, procedure_id: uuid.UUID) -> "CompletionCheckResult":
        """Check if all ProofRequirements for a procedure are satisfied by verified claims."""
        from elephantbroker.schemas.guards import CompletionCheckResult

        # gap-4-4: pull persisted claims from the graph too, so completion checks
        # work after a restart, not only within the process that recorded them.
        proc_claims = await self.get_claims_for_procedure(procedure_id)

        # Try to load procedure definition from graph
        proc_dict = None
        try:
            result = await self._graph.query_cypher(
                "MATCH (p:ProcedureDataPoint {eb_id: $pid, gateway_id: $gw}) RETURN p",
                {"pid": str(procedure_id), "gw": self._gateway_id},
            )
            if result:
                proc_dict = result[0].get("p") if isinstance(result[0], dict) else None
        except Exception as exc:
            self._log.warning("Failed to load procedure %s from graph: %s", procedure_id, exc)

        if proc_dict is None:
            # Fallback: if no graph data, check by claim existence
            complete = any(
                c.status in (ClaimStatus.TOOL_SUPPORTED, ClaimStatus.SUPERVISOR_VERIFIED)
                for c in proc_claims
            )
            return CompletionCheckResult(
                complete=complete,
                procedure_id=procedure_id,
            )

        # Parse steps from graph data
        steps_raw = proc_dict.get("steps_json") or proc_dict.get("steps", "[]")
        if isinstance(steps_raw, str):
            import json
            try:
                steps_data = json.loads(steps_raw)
            except (json.JSONDecodeError, TypeError):
                steps_data = []
        else:
            steps_data = steps_raw if isinstance(steps_raw, list) else []

        missing_evidence: list[str] = []
        unverified_claims: list[uuid.UUID] = []

        for step in steps_data:
            is_optional = step.get("is_optional", False)
            if is_optional:
                continue

            step_id_str = step.get("id") or step.get("step_id", "")
            required_evidence = step.get("required_evidence", [])

            # Fix A: If a non-optional step has no explicit proof requirements,
            # it still needs at least one verified claim referencing this step.
            if not required_evidence:
                has_step_claim = any(
                    c.step_id is not None
                    and str(c.step_id) == str(step_id_str)
                    and c.status not in (ClaimStatus.UNVERIFIED, ClaimStatus.REJECTED)
                    for c in proc_claims
                )
                if not has_step_claim:
                    instruction = step.get("instruction", "")[:80]
                    missing_evidence.append(
                        f"Step '{instruction}': no verified claim for this step"
                    )
                continue

            for proof_req in required_evidence:
                if not proof_req.get("required", True):
                    continue
                proof_type = proof_req.get("proof_type", "chunk_ref")
                description = proof_req.get("description", "")

                found = False
                for claim in proc_claims:
                    if claim.status == ClaimStatus.UNVERIFIED:
                        if claim.id not in unverified_claims:
                            unverified_claims.append(claim.id)
                        continue
                    if claim.status == ClaimStatus.REJECTED:
                        continue
                    # Fix B: Only match claims targeting this specific step.
                    # Claims with step_id=None are procedure-level and satisfy any step.
                    if claim.step_id is not None and step_id_str and str(claim.step_id) != str(step_id_str):
                        continue
                    for ev in claim.evidence_refs:
                        if ev.type == proof_type:
                            found = True
                            break
                    if found:
                        break

                if not found:
                    instruction = step.get("instruction", "")[:80]
                    missing_evidence.append(
                        f"Step '{instruction}': requires {proof_type} — {description}"
                    )

        # Check approval_requirements
        missing_approvals: list[str] = []
        approval_reqs = proc_dict.get("approval_requirements_json") or proc_dict.get("approval_requirements", [])
        if isinstance(approval_reqs, str):
            import json
            try:
                approval_reqs = json.loads(approval_reqs)
            except (json.JSONDecodeError, TypeError):
                approval_reqs = []

        for req in (approval_reqs or []):
            found = any(c.status == ClaimStatus.SUPERVISOR_VERIFIED for c in proc_claims)
            if not found:
                missing_approvals.append(req if isinstance(req, str) else str(req))

        complete = len(missing_evidence) == 0 and len(missing_approvals) == 0

        return CompletionCheckResult(
            complete=complete,
            procedure_id=procedure_id,
            missing_evidence=missing_evidence,
            missing_approvals=missing_approvals,
            unverified_claims=unverified_claims,
        )
