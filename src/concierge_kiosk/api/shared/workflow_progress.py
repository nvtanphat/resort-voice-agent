"""guest-safe projection of ALREADY validated/executed read-only tasks.

This has no scheduler, replay ability or transactional authority. In particular a
review choice is not a prepared proposal or a submitted service request.
"""
from __future__ import annotations

from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS

_READS = {'knowledge', 'navigation', 'planning'}
_REVIEWS = ACTION_REQUEST_KINDS


def project_task_progress(result: dict) -> list[dict]:
    """Display the accepted response's task results, never an older session task.

    The caller must have validated the underlying tool result and mixed workflow.
    Return only allowlisted statuses, no model text, tool arguments, internal proof,
    or persisted credentials. A malformed projection fails closed (empty list).
    """
    executed = result.get('task_execution')
    mixed = result.get('mixed_workflow')
    if isinstance(executed, dict) and isinstance(mixed, dict):
        if (executed.get('authority') != 'server_owned_read_and_review'
                or executed.get('business_writes') != 0
                or mixed.get('business_writes') != 0):
            return []
        tasks = executed.get('tasks')
        expected = mixed.get('tasks')
        if not isinstance(tasks, list) or not isinstance(expected, list) or len(tasks) != len(expected):
            return []
        safe = []
        for i, (step, original) in enumerate(zip(tasks, expected), 1):
            if not isinstance(step, dict) or not isinstance(original, dict):
                return []
            kind, status = step.get('kind'), step.get('status')
            if step.get('id') != f'T{i}' or original.get('id') != step['id'] or original.get('kind') != kind:
                return []
            if kind in _READS and status in {'completed', 'unavailable'} and not step.get('requires_confirmation'):
                # ReadTaskExecution status is an execution result, not an externally
                # verified business transaction. UI labels it "available"/"unavailable".
                normalized = 'available' if status == 'completed' else 'unavailable'
                if original.get('status') != ('verified' if status == 'completed' else 'unavailable'):
                    return []
            elif kind in _REVIEWS and status == 'awaiting_guest_choice' and step.get('requires_confirmation') is True:
                if original.get('status') != status:
                    return []
                normalized = status
            else:
                return []
            safe.append({'id': step['id'], 'kind': kind, 'status': normalized})
        return safe if 1 <= len(safe) <= 6 else []

    # The two-read graph is validated by validate_read_only_result before here.
    graph = result.get('task_graph')
    tasks = result.get('task_plan')
    if not isinstance(graph, dict) or not isinstance(tasks, list) or len(tasks) != 2:
        return []
    expected = graph.get('tasks')
    if not isinstance(expected, list) or len(expected) != 2:
        return []
    safe = []
    for i, (step, original) in enumerate(zip(tasks, expected), 1):
        if not isinstance(step, dict) or not isinstance(original, dict):
            return []
        if (step.get('id') != f'T{i}' or step.get('kind') != original.get('kind') or
                step.get('kind') not in {'knowledge', 'navigation'} or
                step.get('requires_confirmation') is not False or
                step.get('status') not in {'verified', 'unavailable'}):
            return []
        safe.append({'id': step['id'], 'kind': step['kind'],
                     'status': 'available' if step['status'] == 'verified' else 'unavailable'})
    return safe
