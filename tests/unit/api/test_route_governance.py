from __future__ import annotations

import ast
from pathlib import Path


ROUTES_DIR = Path(__file__).parents[3] / "elephantbroker" / "api" / "routes"
MUTATING_METHODS = frozenset({"post", "put", "patch", "delete"})

GOVERNED_ROUTES = {
    "actors.py:post:/",
    "admin.py:put:/authority-rules/{action}",
    "admin.py:post:/organizations",
    "admin.py:put:/organizations/{org_id}",
    "admin.py:post:/teams",
    "admin.py:put:/teams/{team_id}",
    "admin.py:post:/teams/{team_id}/members",
    "admin.py:delete:/teams/{team_id}/members/{actor_id}",
    "admin.py:post:/actors",
    "admin.py:put:/actors/{actor_id}",
    "admin.py:post:/actors/{actor_id}/merge",
    "admin.py:post:/goals",
    "admin.py:put:/goals/{goal_id}",
    "admin.py:put:/profiles/overrides/{org_id}/{profile_id}",
    "admin.py:delete:/profiles/overrides/{org_id}/{profile_id}",
    "artifacts.py:post:/",
    "artifacts.py:post:/search",
    "artifacts.py:post:/session/search",
    "artifacts.py:post:/create",
    "claims.py:post:/",
    "claims.py:post:/{claim_id}/evidence",
    "claims.py:post:/{claim_id}/verify",
    "claims.py:post:/{claim_id}/reject",
    "consolidation.py:post:/run",
    "consolidation.py:patch:/suggestions/{suggestion_id}",
    "context.py:post:/bootstrap",
    "context.py:post:/ingest",
    "context.py:post:/ingest-batch",
    "context.py:post:/assemble",
    "context.py:post:/build-overlay",
    "context.py:post:/compact",
    "context.py:post:/after-turn",
    "context.py:post:/subagent/spawn",
    "context.py:post:/subagent/ended",
    "context.py:post:/subagent/rollback",
    "context.py:post:/dispose",
    "goals.py:post:/session",
    "goals.py:patch:/session/{goal_id}",
    "goals.py:post:/session/{goal_id}/blocker",
    "goals.py:post:/session/{goal_id}/progress",
    "goals.py:post:/",
    "goals.py:put:/{goal_id}",
    "guards.py:post:/check/{session_id}",
    "guards.py:post:/refresh/{session_id}",
    "guards.py:patch:/approvals/{request_id}",
    "guards.py:post:/approvals/sweep-timeouts",
    "memory.py:post:/store",
    "memory.py:post:/search",
    "memory.py:post:/sync",
    "memory.py:post:/promote",
    "memory.py:delete:/{fact_id}",
    "memory.py:patch:/{fact_id}",
    "memory.py:post:/ingest-messages",
    "memory.py:post:/ingest-turn",
    "memory.py:post:/ingest-artifact",
    "memory.py:post:/ingest-procedure",
    "procedures.py:post:/",
    "procedures.py:put:/{procedure_id}",
    "procedures.py:post:/{procedure_id}/activate",
    "procedures.py:post:/{execution_id}/step/{step_id}/complete",
    "rerank.py:post:/",
    "sessions.py:post:/start",
    "sessions.py:post:/context-window",
    "sessions.py:post:/token-usage",
    "sessions.py:post:/end",
    "trace.py:post:/query",
    "working_set.py:post:/build",
}


def _mutating_routes() -> set[str]:
    routes: set[str] = set()
    for path in ROUTES_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not isinstance(func, ast.Attribute) or func.attr not in MUTATING_METHODS:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                routes.add(f"{path.name}:{func.attr}:{decorator.args[0].value}")
    return routes


def test_all_mutating_routes_are_governance_classified():
    assert _mutating_routes() - GOVERNED_ROUTES == set()
    assert GOVERNED_ROUTES - _mutating_routes() == set()
