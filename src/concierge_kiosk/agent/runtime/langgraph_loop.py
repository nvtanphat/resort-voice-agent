"""LangGraph adapter for the governed concierge agent.

The graph owns only per-turn control flow and is the only turn loop.
Authoritative verify/plan/execute semantics live in ``loop_semantics.py``.

The graph is intentionally in-memory for a guest turn. Durable service-request
authority remains in the separate SQLite-backed workflow graph and business DB.
"""
from __future__ import annotations
from concierge_kiosk.runtime.observability import observed

import time
from typing import Callable, TYPE_CHECKING, TypedDict

from concierge_kiosk.agent.core.concierge import AgentToolRequest
from .execution.models import AgentRun
from .loop_semantics import GovernedLoopSemantics
from .planner import ActionBatch, ActionPlan, NextAction
from .state import AgentState
from .verifier import Verification, verify

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import AutonomousConciergeRuntime


Planner = Callable[[AgentState], NextAction | ActionPlan | None]


class AgentLoopState(TypedDict, total=False):
    request: AgentToolRequest
    run: AgentRun
    verification: Verification
    started: float
    action: NextAction | ActionBatch | None
    planner: Planner | None
    route: str


class GovernedAgentGraph:
    """Compile once and reuse the per-turn LangGraph control-flow adapter."""

    def __init__(self, runtime: "AutonomousConciergeRuntime") -> None:
        # Import lazily so offline/property-swap validation can import this module
        # without installing the optional execution backend into that environment.
        from langgraph.graph import END, START, StateGraph

        self.runtime = runtime
        self.semantics = GovernedLoopSemantics(runtime)
        graph = StateGraph(AgentLoopState)
        graph.add_node("verify_goal", self._verify_goal)
        graph.add_node("plan_next_action", self._plan_next_action)
        graph.add_node("execute_tool", self._execute_tool)
        graph.add_edge(START, "verify_goal")
        graph.add_conditional_edges(
            "verify_goal", self._route,
            {"plan": "plan_next_action", "done": END},
        )
        graph.add_conditional_edges(
            "plan_next_action", self._route,
            {"execute": "execute_tool", "verify": "verify_goal", "done": END},
        )
        graph.add_edge("execute_tool", "verify_goal")
        # Compilation is independent from a per-turn planner. The planner is
        # carried in invocation state, so this compiled graph is reusable.
        self.graph = graph.compile()

    @staticmethod
    def _route(state: AgentLoopState) -> str:
        return state.get("route", "done")

    @observed('verify_goal', project=lambda result: {'route': result['route']})
    def _verify_goal(self, graph_state: AgentLoopState) -> dict:
        verification, route = self.semantics.verify_goal(
            graph_state["run"], graph_state["started"])
        return {"verification": verification, "route": route}

    @observed('plan_next_action', project=lambda result: {'route': result['route']})
    def _plan_next_action(self, graph_state: AgentLoopState) -> dict:
        action, verification, route = self.semantics.plan_next_action(
            graph_state["run"],
            graph_state["verification"],
            graph_state.get("planner"),
        )
        return {"verification": verification, "action": action, "route": route}

    @observed('execute_tool', project=lambda result: {'route': result['route']})
    def _execute_tool(self, graph_state: AgentLoopState) -> dict:
        action = graph_state.get("action")
        if action is None:
            raise RuntimeError("Agent graph reached execute_tool without a tool action")
        if isinstance(action, ActionBatch):
            route = self.semantics.execute_batch(
                graph_state["request"], graph_state["run"], action)
        else:
            route = self.semantics.execute_tool(
                graph_state["request"], graph_state["run"], action)
        return {"action": None, "route": route}

    @observed('langgraph_execution')
    def invoke(
        self,
        request: AgentToolRequest,
        run: AgentRun,
        planner: "Planner | None" = None,
    ) -> AgentRun:
        started = time.monotonic()
        result = self.graph.invoke(
            {
                "request": request,
                "run": run,
                "verification": verify(run.state),
                "started": started,
                "action": None,
                "planner": planner,
                "route": "verify",
            },
            config={"recursion_limit": max(32, self.runtime._budget.max_steps * 5 + 12)},
        )
        return self.semantics.finalize(
            result["run"], started, backend="langgraph_stategraph")
