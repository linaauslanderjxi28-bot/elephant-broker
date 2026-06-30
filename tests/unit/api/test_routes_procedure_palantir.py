from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from elephantbroker.schemas.guards import StepCheckResult
from elephantbroker.schemas.procedure import ProcedureDefinition, ProcedureExecution


class TestProcedureActionLineage:
    async def test_step_complete_records_action_lineage(self, client, container) -> None:
        engine = container.procedure_engine
        execution_id = uuid.uuid4()
        step_id = uuid.uuid4()
        proc_id = uuid.uuid4()
        actor_id = uuid.uuid4()
        session_id = uuid.uuid4()

        engine._executions[execution_id] = ProcedureExecution(
            execution_id=execution_id,
            procedure_id=proc_id,
            actor_id=actor_id,
            session_key="agent:main:main",
            session_id=session_id,
        )
        engine._definitions[proc_id] = ProcedureDefinition(
            id=proc_id,
            name="Deploy Checklist",
            is_manual_only=True,
        )
        engine.check_step = AsyncMock(return_value=StepCheckResult(
            step_id=str(step_id), complete=True, missing_evidence=[],
        ))
        audit = MagicMock()
        audit.record_event = AsyncMock()
        container.procedure_audit = audit

        response = await client.post(
            f"/procedures/{execution_id}/step/{step_id}/complete",
            json={"proof_value": "tests passed", "lineage_refs": ["commit:abc123"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["action_type"] == "procedure.complete_step"
        assert uuid.UUID(body["action_id"])
        assert body["lineage_refs"] == ["commit:abc123"]

        step_call = audit.record_event.await_args_list[0].kwargs
        assert step_call["action_id"] == body["action_id"]
        assert step_call["actor_id"] == str(actor_id)
        assert step_call["lineage_refs"] == ["commit:abc123"]

    async def test_step_complete_requires_approval_for_gated_procedure(self, client, container) -> None:
        engine = container.procedure_engine
        execution_id = uuid.uuid4()
        step_id = uuid.uuid4()
        proc_id = uuid.uuid4()
        actor_id = uuid.uuid4()
        session_id = uuid.uuid4()

        engine._executions[execution_id] = ProcedureExecution(
            execution_id=execution_id,
            procedure_id=proc_id,
            actor_id=actor_id,
            session_key="agent:main:main",
            session_id=session_id,
        )
        engine._definitions[proc_id] = ProcedureDefinition(
            id=proc_id,
            name="Production Deploy",
            is_manual_only=True,
            approval_requirements=["human release manager approval"],
            decision_domain="code_change",
        )
        engine.check_step = AsyncMock(return_value=StepCheckResult(
            step_id=str(step_id), complete=True, missing_evidence=[],
        ))
        approval_queue = MagicMock()
        approval_queue.create = AsyncMock(side_effect=lambda request, *_args, **_kwargs: request)
        container.approval_queue = approval_queue

        response = await client.post(f"/procedures/{execution_id}/step/{step_id}/complete", json={})

        assert response.status_code == 409
        body = response.json()
        assert body["status"] == "approval_required"
        assert uuid.UUID(body["approval_request_id"])
        assert body["approval_requirements"] == ["human release manager approval"]
        engine.check_step.assert_not_awaited()
        approval_queue.create.assert_awaited_once()

    async def test_action_lineage_events_are_queryable_by_action_id(self, client, container) -> None:
        audit = MagicMock()
        audit.get_events_by_action_id = AsyncMock(return_value=[
            {
                "event_type": "step_completed",
                "action_id": "action-1",
                "lineage_refs": ["commit:abc123"],
                "gateway_id": "tenant-1",
            },
        ])
        container.procedure_audit = audit

        response = await client.get(
            "/procedures/audit/action/action-1",
            headers={"X-EB-Gateway-ID": "tenant-1", "X-EB-Agent-Key": "tenant-1:agent-a"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "events": [
                {
                    "event_type": "step_completed",
                    "action_id": "action-1",
                    "lineage_refs": ["commit:abc123"],
                    "gateway_id": "tenant-1",
                },
            ],
        }
        audit.get_events_by_action_id.assert_awaited_once_with("action-1", gateway_id="tenant-1")

    async def test_action_lineage_events_are_queryable_by_lineage_ref(self, client, container) -> None:
        audit = MagicMock()
        audit.get_events_by_lineage_ref = AsyncMock(return_value=[
            {
                "event_type": "proof_submitted",
                "action_id": "action-2",
                "lineage_refs": ["artifact:test-report"],
                "gateway_id": "tenant-2",
            },
        ])
        container.procedure_audit = audit

        response = await client.get(
            "/procedures/audit/lineage",
            params={"lineage_ref": "artifact:test-report"},
            headers={"X-EB-Gateway-ID": "tenant-2", "X-EB-Agent-Key": "tenant-2:agent-a"},
        )

        assert response.status_code == 200
        assert response.json()["events"][0]["action_id"] == "action-2"
        audit.get_events_by_lineage_ref.assert_awaited_once_with(
            "artifact:test-report",
            gateway_id="tenant-2",
        )
