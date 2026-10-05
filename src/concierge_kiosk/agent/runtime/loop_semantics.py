"""Shared semantics for the governed agent control loop.

This module is the single implementation of verify -> plan -> execute -> observe.
Execution adapters (LangGraph or the dependency-free Python loop) own only
control-flow mechanics. Keeping business/agent semantics here prevents the two
backends from drifting when policy, budgets or world-state updates change.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from .planner import ActionBatch, ActionPlan, NextAction, PlannedStep, deterministic_next_action
from .verifier import Verification, verify
from .world import AgentFailure, AgentUnknown, facts_from_observation

if TYPE_CHECKING:  # pragma: no cover
    from concierge_kiosk.agent.core.concierge import AgentToolRequest
    from .execution.models import AgentRun
    from .runtime import AutonomousConciergeRuntime, Planner


LOGGER = logging.getLogger(__name__)


class GovernedLoopSemantics:
    """Authoritative state-transition semantics shared by all loop adapters."""

    def __init__(self, runtime: "AutonomousConciergeRuntime") -> None:
        self.runtime = runtime

    @staticmethod
    def _fallback(state, verification: Verification) -> NextAction:
        state.fallback_decisions += 1
        return deterministic_next_action(
            state,
            extra_requirement=verification.missing_capability,
            requirement_id=verification.missing_requirement,
        )

    def verify_goal(self, run: "AgentRun", started: float) -> tuple[Verification, str]:
        """Verify current world state and decide whether to plan or terminate."""
        state = run.state
        verification = verify(state)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if state.status != "running":
            return verification, "done"

        # A tool may finish with usable evidence just after the wall-clock budget.
        # Completion takes precedence over the wall-time bound, just as it does
        # over the step bound below.
        if verification.ready_to_respond:
            state.status = "completed" if verification.goal_complete else "partial"
            state.termination_reason = verification.reason
            return verification, "done"

        if elapsed_ms >= self.runtime._budget.max_wall_time_ms:
            state.status = "bounded"
            state.budget_exhausted = "wall_time"
            state.termination_reason = "agent_wall_time_budget_reached"
            return verification, "done"

        if state.iteration >= self.runtime._budget.max_steps:
            state.status = "bounded"
            state.budget_exhausted = "steps"
            state.termination_reason = "step_budget_reached"
            return verification, "done"

        return verification, "plan"

    def plan_next_action(
        self,
        run: "AgentRun",
        verification: Verification,
        planner: "Planner | None",
    ) -> tuple[NextAction | ActionBatch | None, Verification, str]:
        """Choose and validate the next action or a ready read-only batch.

        Returns ``(action, verification, route)`` where route is one of
        ``execute``, ``verify`` or ``done``.
        """
        state = run.state
        exploratory = sum(1 for item in state.observations if item.get("requirement_id") is None)
        has_unfinished = bool(verification.unresolved)
        planner_budget_ok = run.planner_calls < self.runtime._budget.max_planner_calls
        use_model = planner is not None and planner_budget_ok and not (has_unfinished and exploratory >= 2)
        action: NextAction | ActionPlan | None = None
        planner_failed = False
        if run.pending_plan is not None:
            ready_steps = run.pending_plan.ready(run.completed_plan_steps)
            if ready_steps:
                # Reads at one DAG frontier may run concurrently. A write is
                # always isolated and ordered, even if a malformed plan puts a
                # write beside reads at the same frontier.
                if any(not step.read_only for step in ready_steps):
                    ready_steps = (next(step for step in ready_steps if not step.read_only),)
                remaining_reads = max(0, self.runtime._budget.max_read_calls - run.read_calls)
                if all(step.read_only for step in ready_steps) and remaining_reads == 0:
                    state.status = 'bounded'
                    state.budget_exhausted = 'read_calls'
                    state.termination_reason = 'agent_read_budget_reached'
                    return None, verification, 'done'
                ready_steps = tuple(ready_steps[:remaining_reads]
                                    if all(step.read_only for step in ready_steps)
                                    else ready_steps[:1])
                if ready_steps:
                    batch = ActionBatch(ready_steps)
                    for step in ready_steps:
                        run.decisions.append({'plan_step_id': step.id, **step.action.public()})
                        state.attempted_signatures.add(step.action.signature())
                    return batch, verification, 'execute'
            # A stale/invalid remainder must trigger at most one replanning
            # pass. The deterministic fallback remains available afterwards.
            run.pending_plan = None
            if run.plan_replans < 1:
                run.plan_replans += 1
                state.replans += 1
            else:
                state.status = 'partial'
                state.termination_reason = 'action_plan_exhausted_without_progress'
                return None, verification, 'done'
        if use_model:
            run.planner_calls += 1
            try:
                action = planner(state)
            except Exception as exc:
                planner_failed = True
                state.planner_failures += 1
                LOGGER.exception('agent_planner_failed error_type=%s', type(exc).__name__)
        if isinstance(action, ActionPlan):
            if action.validate(state):
                run.pending_plan = action
                ready_steps = action.ready(run.completed_plan_steps)
                if any(not step.read_only for step in ready_steps):
                    ready_steps = (next(step for step in ready_steps if not step.read_only),)
                remaining_reads = max(0, self.runtime._budget.max_read_calls - run.read_calls)
                if all(step.read_only for step in ready_steps) and remaining_reads == 0:
                    state.status = 'bounded'
                    state.budget_exhausted = 'read_calls'
                    state.termination_reason = 'agent_read_budget_reached'
                    return None, verification, 'done'
                ready_steps = tuple(ready_steps[:remaining_reads]
                                    if all(step.read_only for step in ready_steps)
                                    else ready_steps[:1])
                if ready_steps:
                    state.model_decisions += 1
                    for step in ready_steps:
                        run.decisions.append({'plan_step_id': step.id, **step.action.public()})
                        state.attempted_signatures.add(step.action.signature())
                    return ActionBatch(ready_steps), verification, 'execute'
            action = None
            run.pending_plan = None
            planner_failed = True
            state.planner_failures += 1
        if action is not None:
            state.model_decisions += 1
        else:
            if planner is not None and use_model and not planner_failed:
                state.planner_failures += 1
            action = self._fallback(state, verification)

        if action.type == "finish":
            run.decisions.append(action.public())
            check = verify(state)
            if check.ready_to_respond:
                return None, check, "verify"
            state.replans += 1
            action = self._fallback(state, check)
            verification = check
        elif action.type == "ask_user":
            run.decisions.append(action.public())
            if self.runtime._clarification_allowed(state, action, verification):
                state.pending_question = {
                    "field": action.field,
                    "reason_code": action.reason_code or "missing_required_field",
                    "question_goal": (action.question_goal or action.field or "")[:80],
                }
                state.clarification_count += 1
                if not any(item.field == action.field for item in state.unknowns):
                    state.unknowns.append(AgentUnknown(
                        action.field or "unknown",
                        action.reason_code or "missing_required_field",
                        action.question_goal or "current goal",
                    ))
                state.status = "partial"
                state.termination_reason = "agent_clarification_required"
                return None, verification, "done"
            state.replans += 1
            action = self._fallback(state, verification)

        if action.requirement_id:
            requirement = state.requirement(action.requirement_id)
            if requirement is None or not state.requirement_ready(requirement):
                state.replans += 1
                action = self._fallback(state, verification)
        if action.objective_id:
            objective = state.objective(action.objective_id)
            if objective is None or not state.objective_ready(objective):
                state.replans += 1
                action = self._fallback(state, verification)

        if action.capability != "service_action" and run.read_calls >= self.runtime._budget.max_read_calls:
            state.status = "bounded"
            state.budget_exhausted = "read_calls"
            state.termination_reason = "agent_read_budget_reached"
            return None, verification, "done"

        signature = action.signature()
        if signature in state.attempted_signatures:
            state.replans += 1
            action = self._fallback(state, verification)
            signature = action.signature()
            if signature in state.attempted_signatures and action.type == "tool":
                state.status = "bounded"
                state.termination_reason = "planner_repeated_action_without_progress"
                return None, verification, "done"

        run.decisions.append(action.public())
        if action.type != "tool":
            state.status = "partial"
            state.termination_reason = "agent_waiting_for_guest_input"
            return None, verification, "done"

        state.attempted_signatures.add(signature)
        return action, verification, "execute"

    def execute_tool(
        self,
        request: "AgentToolRequest",
        run: "AgentRun",
        action: NextAction,
    ) -> str:
        """Execute one validated tool action and update the bounded world state."""
        if action.type != "tool":
            raise RuntimeError("Agent loop reached execute_tool without a tool action")

        state = run.state
        step_id = f"A{state.iteration + 1}"
        meta, raw = self.runtime._execute(request, state, action, step_id)
        return self._record_tool_result(run, action, meta, raw)

    def _record_tool_result(
        self, run: "AgentRun", action: NextAction, meta: dict, raw: dict,
    ) -> str:
        """Commit one tool result to the world model in deterministic order."""
        state = run.state
        step_id = str(meta.get('step_id') or f'A{state.iteration + 1}')
        state.iteration += 1
        if action.capability != "service_action":
            run.read_calls += 1
        run.observations.append(meta)
        run.raw_results.append(raw)
        state.observations.append(meta)

        existing_keys = {fact.key for fact in state.verified_facts}
        for fact in facts_from_observation(meta, raw, ttl_seconds=900):
            if fact.key not in existing_keys:
                state.verified_facts.append(fact)
                existing_keys.add(fact.key)

        failure_class = meta.get("failure_class")
        if isinstance(failure_class, str):
            state.failures.append(AgentFailure(
                str(meta.get("capability") or ""),
                failure_class,
                failure_class in {"alternative_read_may_exist", "external_dependency_unavailable"},
            ))
        for missing in (meta.get("missing_slots") or []):
            field_name = str(missing)[:40]
            if field_name and not any(item.field == field_name for item in state.unknowns):
                state.unknowns.append(AgentUnknown(
                    field_name,
                    "missing_required_field",
                    str(meta.get("requirement_id") or "service"),
                ))
        if action.objective_id and self.runtime._addressed(meta):
            state.completed_objectives.add(action.objective_id)
        if run.pending_plan is not None:
            for plan_step in run.pending_plan.steps:
                if plan_step.action.signature() == action.signature() and plan_step.id not in run.completed_plan_steps:
                    run.completed_plan_steps.add(plan_step.id)
                    break
        state.replans += 1
        return "verify"

    def execute_batch(
        self,
        request: "AgentToolRequest",
        run: "AgentRun",
        batch: ActionBatch,
    ) -> str:
        """Run independent reads concurrently, then commit observations in plan order."""
        if not batch.steps:
            raise RuntimeError('Agent loop reached execute_batch without steps')
        if len(batch.steps) == 1:
            return self.execute_tool(request, run, batch.steps[0].action)
        if any(not step.read_only for step in batch.steps):
            raise RuntimeError('Business writes cannot be executed in parallel')
        base_iteration = run.state.iteration
        with ThreadPoolExecutor(max_workers=len(batch.steps), thread_name_prefix='agent-read') as pool:
            futures = [pool.submit(
                self.runtime._execute, request, run.state, step.action,
                f'A{base_iteration + index + 1}')
                for index, step in enumerate(batch.steps)]
            results = [future.result() for future in futures]
        for step, (meta, raw) in zip(batch.steps, results):
            self._record_tool_result(run, step.action, meta, raw)
        return 'verify'

    @staticmethod
    def finalize(run: "AgentRun", started: float, *, backend: str) -> "AgentRun":
        """Apply one common terminal projection for every execution backend."""
        state = run.state
        if state.status == "running":
            final = verify(state)
            state.status = "completed" if final.goal_complete else "partial"
            state.termination_reason = final.reason or "loop_finished"
        run.verification = verify(state)
        run.elapsed_ms = int((time.monotonic() - started) * 1000)
        run.execution_backend = backend
        return run
