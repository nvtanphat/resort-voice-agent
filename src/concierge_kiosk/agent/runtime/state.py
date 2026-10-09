"""Normalized working state for Concierge Agent .

keeps the goal contract separate from execution and adds a typed, non-authoritative world model.  The router is
still allowed to protect hard boundaries (emergency/language/write-candidate
recognition), but it no longer injects a read-tool execution plan.  The agent
receives desired outcomes, constraints, server-owned write candidates and bounded
observations, then chooses the next capability inside the policy sandbox.

The state never stores hidden chain-of-thought.  Public/persisted projections are
bounded structured facts and requirement status only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from concierge_kiosk.agent.tools.service_slots import extract_slots
from concierge_kiosk.agent.understanding.routing import RouteDecision
from concierge_kiosk.domain.service_registry import (accepted_slots, route_branch_for_request_kind,
                                                     service_definition, service_tool)
from .catalog import service_risk_tier
from .world import VerifiedFact, AgentUnknown, AgentFailure
from concierge_kiosk.agent.understanding.commands import Command
from concierge_kiosk.core.domain_profile import preference_policy


AVAILABILITY_GATE = 'availability_confirmed'


def availability_gate_topic(candidate_id: str) -> str:
    """Topic binding one conditional service candidate to its own availability read."""
    return f'conditional availability {candidate_id}'


@dataclass(frozen=True)
class ServiceCandidate:
    id: str
    service_code: str
    request_kind: str
    guest_text: str
    slot_source_query: str
    risk_tier: int
    conditional: bool = False
    # Server-owned continuation state. Never exposed to or authored by the model.
    existing_slots: dict[str, str | int] = field(default_factory=dict, repr=False, compare=False)

    def public(self) -> dict:
        return {
            'id': self.id,
            'service_code': self.service_code,
            'request_kind': self.request_kind,
            'risk_tier': self.risk_tier,
            'conditional': self.conditional,
            # Deliberately bounded. This is current-turn state, not durable memory.
            'summary': self.guest_text[:180],
        }


@dataclass(frozen=True)
class GoalConstraint:
    id: str
    kind: str
    value: str
    hard: bool = True

    def public(self) -> dict:
        return {'id': self.id, 'kind': self.kind, 'value': self.value, 'hard': self.hard}


@dataclass(frozen=True)
class GoalRequirement:
    """What must be true for the guest goal to be considered satisfied.

    ``preferred_capabilities`` are the server-owned affordances for this requirement,
    not a precomputed plan. The model chooses among those capabilities and must name
    this requirement explicitly. Write requirements remain tied to one server-owned
    candidate.
    """

    id: str
    outcome: str
    preferred_capabilities: tuple[str, ...]
    topic: str = ''
    service_candidate_id: str | None = None
    required: bool = True
    depends_on: tuple[str, ...] = ()

    def public(self) -> dict:
        data = {
            'id': self.id,
            'outcome': self.outcome,
            'preferred_capabilities': list(self.preferred_capabilities),
            'required': self.required,
            'depends_on': list(self.depends_on),
        }
        if self.topic:
            data['topic'] = self.topic
        if self.service_candidate_id:
            data['service_candidate_id'] = self.service_candidate_id
        return data


@dataclass(frozen=True)
class GoalContract:
    summary: str
    desired_outcomes: tuple[str, ...]
    constraints: tuple[GoalConstraint, ...]
    requirements: tuple[GoalRequirement, ...]

    def public(self) -> dict:
        return {
            'summary': self.summary,
            'desired_outcomes': list(self.desired_outcomes),
            'constraints': [item.public() for item in self.constraints],
            'requirements': [item.public() for item in self.requirements],
        }


@dataclass(frozen=True)
class GoalObjective:
    """Backward-compatible projection for clients/tests.

    uses :class:`GoalRequirement` for completion.  ``GoalObjective`` remains
    only as a compact compatibility view and as the dependency anchor for
    server-recognized write candidates.
    """

    id: str
    capability: str
    service_candidate_id: str | None = None
    required: bool = True
    depends_on: tuple[str, ...] = ()

    def public(self) -> dict:
        data = {'id': self.id, 'capability': self.capability, 'required': self.required,
                'depends_on': list(self.depends_on)}
        if self.service_candidate_id:
            data['service_candidate_id'] = self.service_candidate_id
        return data


@dataclass
class AgentState:
    goal: str
    language: str
    route_hint: str
    original_query: str
    objectives: list[GoalObjective]
    service_candidates: list[ServiceCandidate]
    # Validated understanding commands are consumed by loop_semantics; they
    # are not an execution plan and never grant write authority.
    commands: tuple[Command, ...] = ()
    goal_contract: GoalContract | None = None
    constraints: list[str] = field(default_factory=list)  # compatibility trace projection
    preferences: dict[str, str | int] = field(default_factory=dict)
    observations: list[dict] = field(default_factory=list)
    prior_facts: list[dict] = field(default_factory=list)
    verified_facts: list[VerifiedFact] = field(default_factory=list)
    unknowns: list[AgentUnknown] = field(default_factory=list)
    failures: list[AgentFailure] = field(default_factory=list)
    pending_question: dict | None = None
    clarification_count: int = 0
    clarification_response: str = ''
    clarification_field: str = ''
    completed_objectives: set[str] = field(default_factory=set)
    satisfied_requirements: set[str] = field(default_factory=set)
    attempted_signatures: set[str] = field(default_factory=set)
    planner_failures: int = 0
    model_decisions: int = 0
    fallback_decisions: int = 0
    replans: int = 0
    iteration: int = 0
    status: str = 'running'
    termination_reason: str = ''
    resumed: bool = False
    budget_exhausted: str = ''
    command_index: int = 0
    command_satisfied_requirements: set[str] = field(default_factory=set)
    tool_attempts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.goal_contract is None:
            requirements = []
            for index, obj in enumerate(self.objectives):
                outcome = ('service:review' if obj.capability == 'service_action'
                           else 'evidence_backed_itinerary' if obj.capability == 'planning'
                           else 'verified_route_guidance' if obj.capability == 'navigation'
                           else 'authoritative_request_status' if obj.capability == 'request_status'
                           else 'verified_answer')
                requirements.append(GoalRequirement(
                    f'R{index+1}', outcome, (obj.capability,),
                    service_candidate_id=obj.service_candidate_id,
                    depends_on=tuple(f'R{int(dep[1:])}' for dep in obj.depends_on if dep.startswith('O'))))
            self.goal_contract = GoalContract(
                summary=f'{self.route_hint}:derived',
                desired_outcomes=tuple(r.outcome for r in requirements),
                constraints=tuple(), requirements=tuple(requirements))

    def candidate(self, candidate_id: str) -> ServiceCandidate | None:
        return next((item for item in self.service_candidates if item.id == candidate_id), None)

    def objective(self, objective_id: str) -> GoalObjective | None:
        return next((item for item in self.objectives if item.id == objective_id), None)

    def requirement(self, requirement_id: str) -> GoalRequirement | None:
        return next((item for item in self.goal_contract.requirements if item.id == requirement_id), None)

    def objective_ready(self, objective: GoalObjective) -> bool:
        return set(objective.depends_on).issubset(self.completed_objectives)

    def requirement_ready(self, requirement: GoalRequirement) -> bool:
        return set(requirement.depends_on).issubset(self.satisfied_requirements)

    def public(self) -> dict:
        return {
            'goal': self.goal,
            'route_hint': self.route_hint,
            'goal_contract': self.goal_contract.public(),
            'constraints': list(self.constraints),
            'session_preferences': dict(self.preferences),
            'objectives': [item.public() for item in self.objectives],
            'completed_objectives': sorted(self.completed_objectives),
            'satisfied_requirements': sorted(self.satisfied_requirements),
            'prior_facts': [
                {k: v for k, v in item.items() if k in {
                    'capability', 'status', 'evidence_status', 'citation_count',
                    'map_status', 'request_count', 'requirement_id', 'plan_topics'
                }}
                for item in self.prior_facts[-6:]
            ],
            'verified_facts': [fact.public() for fact in self.verified_facts[-8:]],
            'unknowns': [item.public() for item in self.unknowns[-6:]],
            'failures': [item.public() for item in self.failures[-6:]],
            'pending_question': dict(self.pending_question) if isinstance(self.pending_question, dict) else None,
            'clarification_response': self.clarification_response[:120],
            'clarification_field': self.clarification_field[:40],
            'observations': [
                {k: v for k, v in item.items() if k in {
                    'step_id', 'objective_id', 'requirement_id', 'capability', 'status',
                    'verified', 'service_candidate_id', 'summary', 'missing_slots',
                    'authority_outcome', 'missing_topics', 'facts', 'failure_class',
                    'query_hint', 'ok', 'error', 'hint', 'attempt'
                }}
                for item in self.observations[-8:]
            ],
            'iteration': self.iteration,
            'replans': self.replans,
            'resumed': self.resumed,
            'command_types': [item.type for item in self.commands],
            'command_index': self.command_index,
        }



def _goal_requirements(*, query: str, language: str, decision: RouteDecision,
                       candidates: list[ServiceCandidate], service_deps: list[tuple[str, ...]],
                       commands: tuple[Command, ...] | None = None) -> list[GoalRequirement]:
    reqs: list[GoalRequirement] = []

    def add(outcome: str, preferred: tuple[str, ...], *, topic: str = '',
            candidate_id: str | None = None, depends_on: tuple[str, ...] = ()) -> None:
        key = (outcome, candidate_id, topic)
        if any((r.outcome, r.service_candidate_id, r.topic) == key for r in reqs):
            return
        reqs.append(GoalRequirement(
            id=f'R{len(reqs)+1}', outcome=outcome,
            preferred_capabilities=preferred, topic=topic,
            service_candidate_id=candidate_id, depends_on=depends_on))

    # Write requirements are canonical server-recognized guest intents.  They are
    # the only requirements that can ever lead to a business side effect.
    for index, candidate in enumerate(candidates):
        # The validated StartGoal order is the source of semantic intent;
        # service dependencies are never reconstructed from connector words.
        dep = list(service_deps[index] if index < len(service_deps) else ())
        tool = service_tool(candidate.service_code)
        if tool is None:
            continue
        if candidate.conditional:
            # "Book it if available": the proposal waits on a read that
            # reports availability, not merely on a read having run.
            gate_topic = availability_gate_topic(candidate.id)
            add(AVAILABILITY_GATE, ('check_schedule',), topic=gate_topic)
            dep.insert(0, next(req.id for req in reqs
                               if req.outcome == AVAILABILITY_GATE and req.topic == gate_topic))
        add(f'service:{candidate.service_code}', (tool,),
            candidate_id=candidate.id, depends_on=tuple(dep))

    if commands is not None:
        # Each command remains an auditable semantic requirement.  StartGoal,
        # slot corrections and confirmation bind to the server-owned candidate
        # above; the other verbs receive their own closed capability boundary.
        for command in commands:
            if command.type in {'StartGoal', 'SetSlot', 'CorrectSlot', 'Confirm'}:
                if candidates:
                    continue
                add(f'command:{command.type}', ('command_noop',))
            elif command.type == 'AskInfo':
                add('verified_answer', ('knowledge',), topic=command.query or query[:80])
            elif command.type == 'CheckAvailability':
                add('availability_checked', ('check_schedule',), topic=command.goal or 'schedule')
            elif command.type == 'Navigate':
                add('verified_route_guidance', ('navigation', 'knowledge'),
                    topic=command.query or query[:80])
            elif command.type in {'Cancel', 'Modify'}:
                add(f'command:{command.type}', ('manage_request',))
            elif command.type == 'Handoff':
                add('command:Handoff', ('handoff_staff',), topic=command.reason or '')
            elif command.type == 'ChitChat':
                add('command:ChitChat', ('command_noop',))
            elif command.type == 'Plan':
                add('command:Plan', ('planning', 'knowledge'), topic=command.query or query[:80])
            elif command.type == 'AskStatus':
                add('command:AskStatus', ('request_status',), topic='current request status')
            elif command.type in {'SwitchLanguage', 'SetPreference', 'Clarify'}:
                add(f'command:{command.type}', ('command_noop',))

    if commands is None:
        # Without validated commands the server route alone names the read.
        route_reads = {
            'request_status': ('authoritative_request_status', ('request_status',), 'current request status'),
            'navigation': ('verified_route_guidance', ('navigation', 'knowledge'), 'route guidance'),
            'planning': ('evidence_backed_itinerary', ('planning', 'knowledge'), 'itinerary'),
            'knowledge': ('verified_answer', ('knowledge',), 'guest question'),
            # Release-bound: falling through to RAG could imply live inventory
            # that the release does not verify.
            'check_schedule': ('verified_answer', ('check_schedule',), 'schedule'),
            'find_place': ('verified_route_guidance', ('find_place', 'navigation', 'knowledge'), 'place'),
            'guest_context': ('session_context_verified', ('guest_context',), 'session context'),
        }
        read = route_reads.get(decision.branch)
        if read is not None:
            add(read[0], read[1], topic=read[2])

    if not reqs:
        add('verified_answer', ('knowledge',), topic='guest question')
    return reqs


def _compat_objectives(requirements: list[GoalRequirement]) -> list[GoalObjective]:
    objectives: list[GoalObjective] = []
    req_to_obj: dict[str, str] = {}
    for req in requirements:
        cap = req.preferred_capabilities[0]
        oid = f'O{len(objectives)+1}'
        # Translate requirement dependencies to objective IDs for old consumers.
        deps = tuple(req_to_obj[dep] for dep in req.depends_on if dep in req_to_obj)
        objectives.append(GoalObjective(
            oid, cap, req.service_candidate_id, req.required, deps))
        req_to_obj[req.id] = oid
    return objectives


def _goal_summary(decision: RouteDecision, requirements: list[GoalRequirement]) -> str:
    # Avoid pretending a generated natural-language summary is authoritative.
    # This compact semantic label is deterministic and safe to persist.
    outcomes = [r.outcome for r in requirements]
    return f'{decision.branch}:' + ','.join(outcomes[:6])


def build_initial_state(*, query: str, language: str, decision: RouteDecision,
                        continuation_context: dict | None = None,
                        resume_projection: dict | None = None,
                        memory_facts: list[dict] | None = None,
                        preferences: dict | None = None,
                        context_topic: str | None = None,
                        commands: tuple[Command, ...] | None = None) -> AgentState:
    """Create a goal contract without pre-computing an execution sequence."""
    from concierge_kiosk.agent.understanding.intent_evidence import command_supported
    def unsupported_state():
        return AgentState(
            goal=query[:500], language=language, route_hint='nlu_failure',
            original_query=query[:500], objectives=[], service_candidates=[],
            goal_contract=GoalContract(summary='nlu_failure:unsupported_semantics',
                desired_outcomes=(), constraints=(), requirements=()),
            status='needs_user_input', termination_reason='unsupported_semantics')

    if (commands is None and continuation_context is None
            and decision.branch in {'service', 'handoff'} and decision.semantic_service_code
            and not command_supported(Command('StartGoal', goal=decision.semantic_service_code),
                                      query, language)):
        return unsupported_state()
    rejected = False
    if commands:
        task = continuation_context or {}
        supported = tuple(command for command in commands if command_supported(
            command, query, language, context_topic=context_topic,
            pending_goal=task.get('mode'),
            pending_reply=(task.get('missing') or (None,))[0]))
        rejected = len(supported) != len(commands)
        if not supported:
            return unsupported_state()
        commands = supported
    candidates: list[ServiceCandidate] = []
    service_deps: list[tuple[str, ...]] = []

    def apply_preference_slots(slots: dict[str, str | int], mode: str) -> dict[str, str | int]:
        """Use explicit session preferences as bounded service defaults."""
        result = dict(slots)
        if not isinstance(preferences, dict):
            return result
        accepted = frozenset(accepted_slots(mode))
        for preference_name, value in preferences.items():
            field = preference_policy().fields.get(preference_name)
            target = field.applies_to_slot if field is not None else None
            if (isinstance(target, str) and target in accepted and target not in result
                    and value not in (None, '')):
                result[target] = value
        return result

    if commands is not None:
        for command in commands:
            if command.type != 'StartGoal':
                continue
            definition = service_definition(command.goal or '')
            if (definition is None
                    or route_branch_for_request_kind(definition.request_kind) not in {'service', 'handoff'}):
                continue
            kind = definition.request_kind
            code = command.goal or ''
            hints = {slot.name: slot.text for slot in command.slots}
            # Validated model spans seed text fields and counting units;
            # deterministic extraction still owns numeric/time normalization.
            slots = apply_preference_slots(
                extract_slots(query, language, kind, mode=code, existing=hints), code)
            candidates.append(ServiceCandidate(
                id=f'S{len(candidates)+1}', service_code=code,
                request_kind=kind, guest_text=query[:500], slot_source_query=query,
                risk_tier=service_risk_tier(code), conditional=command.conditional,
                existing_slots=slots))
            service_deps.append(())
        if (not candidates and isinstance(continuation_context, dict)
                and (not rejected or any(c.type in {'SetSlot', 'CorrectSlot', 'Confirm'} for c in commands))):
            code = continuation_context.get('mode')
            kind = continuation_context.get('kind')
            details = continuation_context.get('details')
            definition = service_definition(code) if isinstance(code, str) else None
            if (definition is not None and definition.request_kind == kind
                    and isinstance(details, str)):
                slots = continuation_context.get('slots')
                candidates.append(ServiceCandidate(
                    id='S1', service_code=code, request_kind=kind, guest_text=details,
                    slot_source_query=query, risk_tier=service_risk_tier(code),
                    existing_slots=(dict(slots) if isinstance(slots, dict) else {})))
                service_deps = [()]
        if candidates:
            for command in commands:
                if command.type in {'SetSlot', 'CorrectSlot'} and command.field and command.value:
                    candidates[0].existing_slots[command.field] = command.value
    elif continuation_context is not None and decision.branch in {'service', 'handoff'}:
        code = continuation_context.get('mode')
        kind = continuation_context.get('kind')
        details = continuation_context.get('details')
        definition = service_definition(code) if isinstance(code, str) else None
        if (definition is not None and definition.request_kind == kind and isinstance(details, str)):
            slots = continuation_context.get('slots')
            candidates.append(ServiceCandidate(
                id='S1', service_code=code, request_kind=kind, guest_text=details,
                slot_source_query=query, risk_tier=service_risk_tier(code),
                existing_slots=(dict(slots) if isinstance(slots, dict) else {})))
            service_deps = [()]
    elif decision.branch in {'service', 'handoff'}:
        if decision.semantic_service_code:
            definition = service_definition(decision.semantic_service_code)
            if definition is not None and route_branch_for_request_kind(definition.request_kind) in {'service', 'handoff'}:
                slots = apply_preference_slots(
                    extract_slots(query, language, definition.request_kind,
                                  mode=decision.semantic_service_code),
                    decision.semantic_service_code)
                candidates = [ServiceCandidate(
                    id='S1', service_code=decision.semantic_service_code,
                    request_kind=definition.request_kind, guest_text=query[:500],
                    slot_source_query=query, risk_tier=service_risk_tier(decision.semantic_service_code),
                    existing_slots=slots)]
            else:
                candidates = []
        service_deps = [() for _ in candidates]

    # Constraints come only from stored session preferences; the guest's words
    # are never scanned for them (see the Plan command slots in plan.md).
    constraints: list[GoalConstraint] = []
    clean_preferences: dict[str, str | int] = {}
    if isinstance(preferences, dict):
        fields = preference_policy().fields
        for key in fields:
            value = preferences.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                clean_preferences[key] = value
        existing = {(item.kind, item.value) for item in constraints}
        # A field the profile maps to planning constraints contributes only its
        # mapped constraint; every other stated preference stays a soft preference.
        constrained = [(dict(fields[key].constraints).get(str(value)), str(value))
                       for key, value in clean_preferences.items() if fields[key].constraints]
        preference_specs: list[tuple[str, str]] = [item for item in constrained if item[0] is not None]
        preference_specs += [('preference', f'{key}:{value}')
                             for key, value in clean_preferences.items() if not fields[key].constraints]
        for kind, value in preference_specs:
            if (kind, value) not in existing and len(constraints) < 8:
                constraints.append(GoalConstraint(f'C{len(constraints)+1}', kind, value, hard=False))
                existing.add((kind, value))
    requirements = _goal_requirements(
        query=query, language=language, decision=decision,
        candidates=candidates, service_deps=service_deps,
        commands=commands)
    contract = GoalContract(
        summary=_goal_summary(decision, requirements),
        desired_outcomes=tuple(r.outcome for r in requirements),
        constraints=tuple(constraints),
        requirements=tuple(requirements),
    )
    state = AgentState(
        goal=query.strip()[:500], language=language, route_hint=decision.branch,
        original_query=query.strip()[:500], goal_contract=contract,
        objectives=_compat_objectives(requirements), service_candidates=candidates,
        commands=tuple(commands or ()),
        constraints=[c.value for c in constraints], preferences=clean_preferences,
    )

    # semantic memory is a separate, session-scoped data product. It can
    # inform planning but is never authority for a write or permission decision.
    if isinstance(memory_facts, list):
        for raw_fact in memory_facts[:8]:
            fact = VerifiedFact.from_public(raw_fact)
            if fact is not None:
                state.verified_facts.append(fact)

    # Durable checkpoint is deliberately non-authoritative.  It may provide
    # bounded public facts from an earlier accepted turn, but never service
    # candidates, tool permissions, raw retrieved text or a model-authored plan.
    if isinstance(resume_projection, dict):
        facts = resume_projection.get('facts')
        if isinstance(facts, list):
            state.prior_facts = [dict(item) for item in facts[:6] if isinstance(item, dict)]
        unresolved = resume_projection.get('unresolved_outcomes')
        saved_requirements = resume_projection.get('requirements')
        if ((isinstance(saved_requirements, list) and saved_requirements)
                or (isinstance(unresolved, list) and unresolved)):
            # Checkpoints carry semantic read goals only.  Capability choice is
            # reconstructed by server-owned outcome rules rather than trusted from
            # persisted/model-authored state.  Newer checkpoints preserve the
            # semantic topic; older checkpoints fall back to a generic resume tag.
            preferred_by_outcome = {
                'verified_answer': ('knowledge',),
                'supporting_hotel_facts': ('knowledge',),
                'verified_route_guidance': ('navigation', 'knowledge'),
                'minimal_travel_checked': ('navigation', 'knowledge'),
                'authoritative_request_status': ('request_status',),
                'evidence_backed_itinerary': ('planning', 'knowledge'),
            }
            existing_pairs = {(r.outcome, r.topic) for r in state.goal_contract.requirements}
            extended = list(state.goal_contract.requirements)

            candidates: list[tuple[str, str]] = []
            if isinstance(saved_requirements, list):
                for item in saved_requirements[:6]:
                    if not isinstance(item, dict):
                        continue
                    outcome = item.get('outcome')
                    topic = item.get('topic')
                    if (isinstance(outcome, str) and outcome in preferred_by_outcome
                            and isinstance(topic, str) and topic.strip()):
                        candidates.append((outcome, topic.strip()[:80]))
            if not candidates and isinstance(unresolved, list):
                for outcome in unresolved[:4]:
                    if isinstance(outcome, str) and outcome in preferred_by_outcome:
                        candidates.append((outcome, 'resumed goal'))

            for outcome, topic in candidates:
                pair = (outcome, topic)
                if pair in existing_pairs:
                    continue
                extended.append(GoalRequirement(
                    f'R{len(extended)+1}', outcome, preferred_by_outcome[outcome], topic=topic))
                existing_pairs.add(pair)

            if len(extended) != len(state.goal_contract.requirements):
                state.goal_contract = GoalContract(
                    state.goal_contract.summary,
                    tuple(r.outcome for r in extended),
                    state.goal_contract.constraints,
                    tuple(extended),
                )
                state.objectives = _compat_objectives(extended)
        pending_question = resume_projection.get('pending_question')
        if isinstance(pending_question, dict):
            field_name = pending_question.get('field')
            reason_code = pending_question.get('reason_code')
            question_goal = pending_question.get('question_goal')
            if (isinstance(field_name, str) and 1 <= len(field_name) <= 40
                    and isinstance(reason_code, str) and len(reason_code) <= 40
                    and isinstance(question_goal, str) and len(question_goal) <= 80):
                # The current utterance answers the previous bounded interrupt.
                # Keep it ephemeral for this run; do not re-emit the old question and
                # do not promote the answer to durable semantic memory automatically.
                state.clarification_field = field_name
                state.clarification_response = query.strip()[:120]
                state.clarification_count = 1
        state.resumed = bool(state.prior_facts or resume_projection.get('unresolved_outcomes')
                             or state.clarification_response)
    return state
