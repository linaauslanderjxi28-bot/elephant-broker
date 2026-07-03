"""Scenario: Procedure Execution — create, activate, step through, complete."""
from __future__ import annotations

from tests.scenarios.base import Scenario
from tests.scenarios.runner import register


@register
class ProcedureExecutionScenario(Scenario):
    """Procedure create -> activate -> step through -> complete."""

    name = "procedure_execution"
    required_phase = 7
    required_amendment_6_2 = False

    async def run(self):
        self.expect_trace("procedure_step_passed", min_count=1)

        # ProcedureStep schema requires {order:int, instruction:str}, not
        # {name, description} — the old payload made POST /procedures/ 422.
        proc = await self.sim.simulate_procedure_create("deploy_service", steps=[
            {"order": 0, "instruction": "Run test suite"},
            {"order": 1, "instruction": "Deploy to staging"},
        ])
        proc_id = proc.get("id") or proc.get("procedure_id")
        self.step("procedure_created", passed=proc_id is not None)

        if proc_id:
            activation = await self.sim.simulate_procedure_activate(proc_id)
            exec_id = activation.get("execution_id")
            self.step("procedure_activated", passed=exec_id is not None)

            # Step ids live on the ProcedureDefinition (create response), NOT on the
            # ProcedureExecution (activate response, which only has execution_id/
            # current_step_index/completed_steps). Complete the first (order-0) step
            # so engine.check_step runs and emits PROCEDURE_STEP_PASSED.
            def_steps = sorted(proc.get("steps", []), key=lambda s: s.get("order", 0))
            if exec_id and def_steps:
                step_id = def_steps[0].get("step_id") or def_steps[0].get("id")
                if step_id:
                    res = await self.sim.simulate_procedure_complete_step(
                        exec_id, step_id, proof_value="Tests passed")
                    self.step("step_0_completed",
                              passed=bool(res.get("completed") or res.get("passed")),
                              message=f"check_step -> {res}")

        status = await self.sim.simulate_procedure_status()
        self.step("procedure_status_available", passed=status is not None)
