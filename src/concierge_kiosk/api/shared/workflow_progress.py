"""guest-safe projection of ALREADY validated/executed read-only tasks.

This has no scheduler, replay ability or transactional authority. In particular a
review choice is not a prepared proposal or a submitted service request.
"""
from __future__ import annotations

from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS

_REVIEWS = ACTION_REQUEST_KINDS

def project_task_progress(result: dict) -> list[dict]:
    """Display the accepted response's task results, never an older session task.

    The caller must have validated the underlying server-owned read graph.
    Return only allowlisted statuses, no model text, tool arguments, internal proof,
    or persisted credentials. A malformed projection fails closed (empty list).
    """
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
