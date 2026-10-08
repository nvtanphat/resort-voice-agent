"""Bounded read-only task projections from validated understanding commands."""
from __future__ import annotations

from concierge_kiosk.agent.understanding.commands import Command


_READ_TASKS = {
    'AskInfo': ('knowledge', 'read_approved_knowledge'),
    'Navigate': ('navigation', 'read_approved_map'),
    'CheckAvailability': ('availability', 'read_approved_schedule'),
}


def read_only_task_graph(commands: tuple[Command, ...] | None) -> dict | None:
    """Project two or more validated read commands into a consent-free DAG."""
    reads = tuple(command for command in (commands or ()) if command.type in _READ_TASKS)
    if len(reads) < 2:
        return None
    return {'tasks': [
        {'id': f'T{i + 1}', 'kind': _READ_TASKS[command.type][0],
         'operation': _READ_TASKS[command.type][1], 'depends_on': [],
         'requires_confirmation': False}
        for i, command in enumerate(reads)],
        'execution': 'read_only_no_business_writes'}


def validate_read_only_result(result: dict, *, expected_graph: dict | None = None,
                              expected_reads: list[tuple[dict, dict]] | None = None) -> None:
    """Reject graphs or execution state not reconstructed from server commands."""
    expected = expected_graph
    if expected is None or result.get('task_graph') != expected or result.get('request_completed') is not False:
        raise RuntimeError('Invalid read-only task graph')
    if (not isinstance(expected, dict) or set(expected) != {'tasks', 'execution'} or
            expected.get('execution') != 'read_only_no_business_writes' or
            not isinstance(expected.get('tasks'), list) or len(expected['tasks']) < 2):
        raise RuntimeError('Invalid read-only authority')
    tasks = expected['tasks']
    for i, task in enumerate(tasks):
        kind = task.get('kind') if isinstance(task, dict) else None
        operation = {'knowledge': 'read_approved_knowledge', 'navigation': 'read_approved_map',
                     'availability': 'read_approved_schedule'}.get(kind)
        if (not isinstance(task, dict) or set(task) != {'id', 'kind', 'operation', 'depends_on', 'requires_confirmation'}
                or task['id'] != f'T{i + 1}' or task['operation'] != operation
                or task['requires_confirmation'] is not False or task['depends_on'] != []):
            raise RuntimeError('Invalid read-only authority')
    plan = result.get('task_plan')
    if not isinstance(plan, list) or len(plan) != len(tasks):
        raise RuntimeError('Invalid read-only task execution')
    if expected_reads is None or len(expected_reads) != len(tasks):
        raise RuntimeError('Missing per-read evidence')
    for step, task, (meta, raw) in zip(plan, tasks, expected_reads):
        if not isinstance(step, dict) or set(step) != set(task) | {'status'}:
            raise RuntimeError('Invalid read-only task schema')
        if any(step[key] != task[key] for key in task):
            raise RuntimeError('Read-only task mutated')
        available = (meta.get('status') == 'completed' and meta.get('verified') is True
                     and (bool(raw.get('citations')) if task['kind'] == 'knowledge' else
                          raw.get('map_guidance', {}).get('status') == 'verified'
                          if task['kind'] == 'navigation' else
                          raw.get('schedule_verified') is True or bool(raw.get('citations'))))
        if step['status'] != ('verified' if available else 'unavailable'):
            raise RuntimeError('Read-only result status does not match evidence')
