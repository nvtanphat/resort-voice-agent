"""Framework-neutral adapter for the governed agent control loop.

All verify/plan/execute semantics live in :mod:`loop_semantics`. This module is
only the dependency-free control-flow adapter used when LangGraph is unavailable
or during offline validation.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .loop_semantics import GovernedLoopSemantics
from .planner import ActionBatch

if TYPE_CHECKING:  # pragma: no cover
    from concierge_kiosk.agent.core.concierge import AgentToolRequest
    from .execution.models import AgentRun
    from .runtime import AutonomousConciergeRuntime, Planner


def run_governed_loop(
    runtime: "AutonomousConciergeRuntime",
    request: "AgentToolRequest",
    run: "AgentRun",
    planner: "Planner | None",
) -> "AgentRun":
    """Execute the shared governed semantics without a graph dependency."""
    started = time.monotonic()
    semantics = GovernedLoopSemantics(runtime)

    while True:
        verification, route = semantics.verify_goal(run, started)
        if route == "done":
            break

        action, verification, route = semantics.plan_next_action(run, verification, planner)
        if route == "done":
            break
        if route == "verify":
            continue
        if route != "execute" or action is None:
            raise RuntimeError("Governed loop produced an invalid control-flow route")

        if isinstance(action, ActionBatch):
            semantics.execute_batch(request, run, action)
        else:
            semantics.execute_tool(request, run, action)

    return semantics.finalize(run, started, backend="governed_python_loop")
