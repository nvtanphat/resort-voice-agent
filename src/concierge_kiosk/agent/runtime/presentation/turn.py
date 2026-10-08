"""Turn-level presentation projections for the governed agent runtime.

These helpers keep internal task/read metadata separate from the guest-facing
answer. They consume verified observations and never choose a business tool.
"""
from __future__ import annotations

from concierge_kiosk.agent.understanding.routing import directions_request
from concierge_kiosk.agent.tools.read_tasks import read_task_available, validate_read_only_result
from concierge_kiosk.i18n import text as i18n_text


def apply_verified_map_answer(result: dict, language: str, query: str, *,
                              has_navigate_command: bool = False) -> dict:
    guidance = result.get('map_guidance')
    if isinstance(guidance, dict) and guidance.get('status') == 'ambiguous':
        options = guidance.get('options')
        if isinstance(options, list) and options:
            labels = [item.get('label') for item in options
                      if isinstance(item, dict) and isinstance(item.get('label'), str)]
            if labels:
                result['answer'] = i18n_text(
                    'navigation.choose_destination', language,
                    options=', '.join(labels))
                result['sources'] = []
                result['citations'] = []
                result['grounding'] = 'map_ambiguous'
                result['evidence_status'] = 'UNSUPPORTED'
                result['suggested_action'] = None
                result['requires_staff_review'] = False
                result['related_topics'] = []
                result['support_contact'] = None
                result['recovery_mode'] = 'not_needed'
        return result
    if isinstance(guidance, dict) and guidance.get('status') == 'verified' and (result.get('grounding') == 'no_evidence') and isinstance(guidance.get('destination'), str):
        result['answer'] = i18n_text('navigation.map_verified', language, destination=guidance['destination'])
        result['sources'] = []
        result['citations'] = []
        result['grounding'] = 'map_verified'
        result['evidence_status'] = 'SUPPORTED'
        result['related_topics'] = []
        result['support_contact'] = None
        result['recovery_mode'] = 'not_needed'
        result['suggested_action'] = directions_request(query, has_navigate_command=has_navigate_command)
        result['requires_staff_review'] = True
    return result


def project_read_workflow(*, result: dict, agent_run, query: str, language: str,
                          read_graph: dict | None) -> None:
    """Attach validated read orchestration metadata for final projection."""
    if read_graph is None:
        return
    result['task_graph'] = read_graph
    # Read task order comes from validated commands; execution may include
    # retries or JIT reads. Bind each task to its own requirement and result.
    outcomes = {'knowledge': 'verified_answer', 'navigation': 'verified_route_guidance',
                'availability': 'availability_checked'}
    requirements = [req for req in agent_run.state.goal_contract.requirements
                    if req.outcome in outcomes.values()]
    expected_reads = []
    for task, requirement in zip(read_graph['tasks'], requirements):
        matching = [(meta, raw) for meta, raw in zip(agent_run.observations, agent_run.raw_results)
                    if meta.get('requirement_id') == requirement.id]
        expected_reads.append(matching[-1] if matching else ({}, {}))
    if len(expected_reads) != len(read_graph['tasks']):
        raise RuntimeError('Read tasks do not match command requirements')
    result['task_plan'] = []
    for task, (meta, raw) in zip(read_graph['tasks'], expected_reads):
        available = read_task_available(task['kind'], meta, raw)
        result['task_plan'].append({**task, 'status': 'verified' if available else 'unavailable'})
    validate_read_only_result(result, expected_graph=read_graph, expected_reads=expected_reads)


__all__ = ['apply_verified_map_answer', 'project_read_workflow']
