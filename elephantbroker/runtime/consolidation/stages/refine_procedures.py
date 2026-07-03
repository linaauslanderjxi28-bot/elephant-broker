"""Stage 7: Refine Procedures from Patterns — detect repeated tool sequences.

LLM calls bounded by max_patterns_per_run and context.llm_calls_cap.
Primary source: ClickHouse via OtelTraceQueryClient.
Fallback: ProcedureAuditStore (SQLite) for procedure-bound patterns only.
Never auto-activates — suggestions carry a parsed ProcedureDefinition draft and
are queued for human/supervisor review. On approval, an operator resolves the
suggestion and ``promote_suggestion_to_procedure()`` persists the draft as a
durable ProcedureDefinition (gap-5-4).
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from elephantbroker.runtime.observability import traced
from elephantbroker.schemas.procedure import (
    ProcedureDefinition,
    ProcedureStep,
    ProcedureSuggestion,
)

if TYPE_CHECKING:
    from elephantbroker.runtime.adapters.llm.client import LLMClient
    from elephantbroker.runtime.audit.procedure_audit import ProcedureAuditStore
    from elephantbroker.runtime.consolidation.otel_trace_query_client import OtelTraceQueryClient
    from elephantbroker.schemas.consolidation import ConsolidationConfig, ConsolidationContext

logger = logging.getLogger("elephantbroker.runtime.consolidation.stages.refine_procedures")

_PROCEDURE_PROMPT = """Based on the following repeated tool call pattern observed \
across {sessions} sessions, generate a procedure definition.

Pattern: {sequence}
Description: {description}

Return a JSON object with:
- name: short procedure name
- description: what this procedure accomplishes
- steps: array of {{instruction: str, order: int}}

Return ONLY valid JSON."""


class RefineProceduresStage:
    """Detect repeated multi-step patterns and generate procedure drafts.

    Algorithm:
    1. Query tool sequences from ClickHouse (or fallback to ProcedureAuditStore)
    2. Group by session, extract ordered tool sequences
    3. Find sequences of length >= min_steps appearing in >= threshold sessions
    4. For each detected pattern: LLM generates ProcedureDefinition draft
    5. Store as ProcedureSuggestion with approval_status="pending"
    """

    def __init__(
        self,
        llm_client: LLMClient | None,
        trace_query_client: OtelTraceQueryClient,
        procedure_audit_store: ProcedureAuditStore | None,
        config: ConsolidationConfig,
    ) -> None:
        self._llm = llm_client
        self._trace_client = trace_query_client
        self._audit_store = procedure_audit_store
        self._recurrence = config.pattern_recurrence_threshold
        self._min_steps = config.pattern_min_steps
        self._max_patterns = config.max_patterns_per_run

    @traced
    async def run(
        self, gateway_id: str, context: ConsolidationContext,
    ) -> list[ProcedureSuggestion]:
        # 1. Get tool sequences
        sequences = await self._load_sequences(gateway_id)
        if not sequences:
            return []

        # 2-3. Find recurring patterns
        patterns = self._find_patterns(sequences)
        if not patterns:
            return []

        # 4. Generate drafts (bounded by caps)
        suggestions: list[ProcedureSuggestion] = []
        for seq_tuple, session_count in patterns[: self._max_patterns]:
            if not self._llm or context.llm_calls_used >= context.llm_calls_cap:
                logger.warning("LLM cap reached — stopping pattern generation")
                break

            seq_list = list(seq_tuple)
            desc = f"Repeated sequence: {' → '.join(seq_list)} (seen in {session_count} sessions)"
            try:
                draft_text = await self._llm.complete(
                    system_prompt="You are a procedure definition generator.",
                    user_prompt=_PROCEDURE_PROMPT.format(
                        sessions=session_count,
                        sequence=" → ".join(seq_list),
                        description=desc,
                    ),
                    max_tokens=500,
                )
                context.llm_calls_used += 1
            except Exception:
                logger.warning("LLM draft generation failed", exc_info=True)
                continue

            # gap-5-4: parse the LLM draft into a real ProcedureDefinition and
            # attach it to the suggestion. Previously the draft text was discarded
            # (draft_procedure=None), so the operator's Approve had nothing to
            # promote and the whole review surface was a dead end. The draft now
            # persists (draft_procedure_json) and feeds
            # promote_suggestion_to_procedure() on approval.
            draft = self._parse_draft(draft_text, gateway_id, seq_list)

            suggestions.append(ProcedureSuggestion(
                id=uuid.uuid4(),
                pattern_description=desc,
                tool_sequence=seq_list,
                sessions_observed=session_count,
                draft_procedure=draft,
                confidence=min(0.9, 0.3 + 0.1 * session_count),
                approval_status="pending",
                created_at=datetime.now(UTC),
                gateway_id=gateway_id,
            ))

        logger.info(
            "Stage 7: %d patterns found, %d suggestions generated (gateway=%s)",
            len(patterns), len(suggestions), gateway_id,
        )
        return suggestions

    def _parse_draft(
        self, draft_text: str | None, gateway_id: str, tool_sequence: list[str],
    ) -> ProcedureDefinition | None:
        """Parse the LLM draft text into a ProcedureDefinition (gap-5-4).

        Tolerant of markdown code fences and surrounding prose. Falls back to
        synthesizing steps from the observed ``tool_sequence`` when the model
        omits them, so a detected pattern always yields a reviewable draft.
        Returns ``None`` only when nothing usable can be built.
        """
        obj = _extract_json_object(draft_text)
        name = ""
        description = ""
        steps: list[ProcedureStep] = []
        if isinstance(obj, dict):
            name = str(obj.get("name") or "").strip()
            description = str(obj.get("description") or "").strip()
            steps = _steps_from_raw(obj.get("steps"))

        if not steps:
            steps = [
                ProcedureStep(order=i, instruction=str(t))
                for i, t in enumerate(tool_sequence) if str(t).strip()
            ]
        if not steps:
            return None
        if not name:
            name = f"Procedure: {' → '.join(tool_sequence)}"[:200] or "Consolidated procedure"

        try:
            return ProcedureDefinition(
                name=name[:200],
                description=description,
                steps=steps,
                # Operator-review draft — never auto-fires; #1146 requires
                # activation_modes OR is_manual_only.
                is_manual_only=True,
                gateway_id=gateway_id,
            )
        except Exception:
            logger.warning("Stage 7: failed to build ProcedureDefinition from draft", exc_info=True)
            return None

    async def _load_sequences(self, gateway_id: str) -> list[list[str]]:
        """Load tool sequences from ClickHouse or fallback to audit store."""
        # Primary: ClickHouse
        if self._trace_client and self._trace_client.available:
            try:
                results = await self._trace_client.get_tool_sequences(gateway_id)
                if results:
                    return [r.get("tools", []) for r in results if r.get("tools")]
            except Exception:
                logger.warning("ClickHouse query failed — falling back", exc_info=True)

        # Fallback: ProcedureAuditStore
        if self._audit_store:
            logger.info("ClickHouse not available — using ProcedureAuditStore fallback")
            try:
                events = await self._audit_store.get_procedure_events("*")
                # Group by session_key, extract step sequences
                sessions: dict[str, list[str]] = {}
                for ev in events:
                    sk = ev.get("session_key", "")
                    step = ev.get("step_instruction") or ev.get("event_type", "")
                    sessions.setdefault(sk, []).append(step)
                return list(sessions.values())
            except Exception:
                logger.warning("ProcedureAuditStore fallback failed", exc_info=True)

        logger.warning("No data source for Stage 7 — no tool sequence analysis")
        return []

    def _find_patterns(
        self, sequences: list[list[str]],
    ) -> list[tuple[tuple[str, ...], int]]:
        """Find subsequences appearing in >= threshold distinct sessions."""
        pattern_counts: Counter[tuple[str, ...]] = Counter()

        for seq in sequences:
            if len(seq) < self._min_steps:
                continue
            # Extract all contiguous subsequences of length >= min_steps
            seen_in_session: set[tuple[str, ...]] = set()
            for start in range(len(seq)):
                for end in range(start + self._min_steps, len(seq) + 1):
                    subseq = tuple(seq[start:end])
                    seen_in_session.add(subseq)
            for subseq in seen_in_session:
                pattern_counts[subseq] += 1

        # Filter by recurrence threshold, sort by count descending
        recurring = [
            (pat, count) for pat, count in pattern_counts.items()
            if count >= self._recurrence
        ]
        recurring.sort(key=lambda x: (-x[1], -len(x[0])))
        return recurring


# ---------------------------------------------------------------------------
# Draft parsing + approval → promotion (gap-5-4)
# ---------------------------------------------------------------------------


def _extract_json_object(text: str | None) -> dict | None:
    """Parse a JSON object out of an LLM response, tolerating code fences and
    surrounding prose."""
    import json
    import re

    if not text or not text.strip():
        return None
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z0-9]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body).strip()
    try:
        obj = json.loads(body)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", body, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _steps_from_raw(steps_raw: object) -> list[ProcedureStep]:
    """Coerce a raw ``steps`` value (list of dicts/strings) into ProcedureSteps."""
    steps: list[ProcedureStep] = []
    if not isinstance(steps_raw, list):
        return steps
    for i, s in enumerate(steps_raw):
        if isinstance(s, dict):
            instruction = str(s.get("instruction") or "").strip()
            order = s.get("order", i)
        elif isinstance(s, str):
            instruction, order = s.strip(), i
        else:
            continue
        if not instruction:
            continue
        try:
            steps.append(ProcedureStep(order=int(order), instruction=instruction))
        except Exception:  # noqa: BLE001 — skip a malformed step
            continue
    return steps


async def promote_suggestion_to_procedure(
    suggestion: dict,
    *,
    dataset_name: str = "elephantbroker",
    gateway_id: str = "",
    source_actor_id: "uuid.UUID | None" = None,
) -> ProcedureDefinition | None:
    """Promote an APPROVED Stage 7 suggestion into a durable ProcedureDefinition
    (gap-5-4).

    ``suggestion`` is a stored suggestion row (as returned by
    ``ConsolidationReportStore.list_suggestions``). The LLM draft is read from
    ``draft_procedure_json`` when present; otherwise a manual-only procedure is
    synthesized from ``tool_sequence_json`` so an approval always yields a usable
    procedure. Persistence is Cognee-first — ``add_data_points([ProcedureDataPoint])``
    (the durable graph node) is required; the follow-up ``cognee.add`` rich-index
    call is best-effort.

    ``dataset_name`` should be the gateway-scoped dataset the caller already uses
    for procedures (mirrors ``ProcedureAndGuardEngine.store_procedure``).

    Returns the stored ProcedureDefinition, or ``None`` when no valid procedure
    could be built or the graph write failed.
    """
    import json

    gw = gateway_id or str(suggestion.get("gateway_id") or "")

    raw_draft = suggestion.get("draft_procedure_json")
    if raw_draft is None:
        raw_draft = suggestion.get("draft_procedure")
    draft: dict | None = None
    if isinstance(raw_draft, dict):
        draft = raw_draft
    elif isinstance(raw_draft, str) and raw_draft.strip():
        try:
            parsed = json.loads(raw_draft)
            draft = parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            draft = None

    name = ""
    description = ""
    steps: list[ProcedureStep] = []
    if draft:
        name = str(draft.get("name") or "").strip()
        description = str(draft.get("description") or "").strip()
        steps = _steps_from_raw(draft.get("steps"))

    if not steps:
        seq_raw = suggestion.get("tool_sequence_json")
        if seq_raw is None:
            seq_raw = suggestion.get("tool_sequence") or []
        if isinstance(seq_raw, str):
            try:
                seq = json.loads(seq_raw)
            except (json.JSONDecodeError, TypeError):
                seq = []
        else:
            seq = seq_raw
        steps = [
            ProcedureStep(order=i, instruction=str(t))
            for i, t in enumerate(seq if isinstance(seq, list) else []) if str(t).strip()
        ]
    if not steps:
        logger.warning("gap-5-4: cannot promote suggestion %s — no usable steps",
                       suggestion.get("id"))
        return None
    if not name:
        name = str(suggestion.get("pattern_description") or "Consolidated procedure")[:200]

    try:
        procedure = ProcedureDefinition(
            name=name[:200],
            description=description,
            steps=steps,
            is_manual_only=True,  # operator-approved runbook; no auto-trigger
            gateway_id=gw,
            source_actor_id=source_actor_id,
        )
    except Exception:
        logger.warning("gap-5-4: failed to build ProcedureDefinition from suggestion %s",
                       suggestion.get("id"), exc_info=True)
        return None

    try:
        from cognee.tasks.storage import add_data_points

        from elephantbroker.runtime.adapters.cognee.datapoints import ProcedureDataPoint

        dp = ProcedureDataPoint.from_schema(procedure)
        await add_data_points([dp])  # durable graph node — Cognee-first per CLAUDE.md
    except Exception:
        logger.warning("gap-5-4: failed to persist promoted procedure node for suggestion %s",
                       suggestion.get("id"), exc_info=True)
        return None

    try:
        import cognee

        proc_text = f"Procedure: {procedure.name}"
        if procedure.description:
            proc_text += f" — {procedure.description}"
        await cognee.add(proc_text, dataset_name=dataset_name)
    except Exception:
        # Node already persisted; rich indexing is best-effort.
        logger.warning("gap-5-4: cognee.add for promoted procedure failed (node persisted)",
                       exc_info=True)

    logger.info("gap-5-4: promoted suggestion %s → procedure %s (gateway=%s)",
                suggestion.get("id"), procedure.id, gw)
    return procedure
