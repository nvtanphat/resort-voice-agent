"""Forward existing evaluation results; no inference, evaluator, or dataset upload."""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
import re
from typing import Mapping

# Exact names from TrajectoryScore.public / TaskJourneyScore.public and existing
# command, retrieval, tool and voice reports. Units/scales are never normalized.
NUMERIC_SCORES = frozenset({
    'steps_to_completion', 'read_calls', 'planner_calls', 'grounded_fact_count',
    'denied_action_proposal_rate', 'unauthorized_execution_rate', 'unauthorized_action_rate',
    'recovery_rate', 'loop_rate', 'turns_to_completion', 'unexpected_action_rate',
    'wrong_answer_with_citation_rate', 'p95_latency_ms', 'route_accuracy',
    'selection_accuracy', 'typed_parameter_accuracy', 'db_accuracy',
    'tool_accuracy', 'slot_accuracy', 'wer', 'r1', 'r3', 'r5', 'mrr',
    'selector_hit', 'parsed', 'command_mode', 'command_kind', 'fallback_mode', 'fallback_answered',
    'first_audio_p50_ms', 'first_audio_p95_ms',
    'p50_upper_ms', 'p95_upper_ms', 'p99_upper_ms',
    'p50_upper_tokens_per_sec', 'p95_upper_tokens_per_sec', 'p99_upper_tokens_per_sec',
})
BOOLEAN_SCORES = frozenset({'task_success'})
CATEGORICAL_SCORES = {'budget_exhausted': frozenset({'wall_time', 'steps', 'read_calls', 'planner_calls'})}


def export_existing_scores(telemetry, result, *, evaluation_id: str,
                           evaluated_at: datetime, trace_id: str | None = None,
                           session_pseudonym: str | None = None) -> dict:
    """Opt-in adapter, invoked by callers with an actual trace/session association.

    `evaluated_at` must be the persisted result timestamp: Langfuse v4 dedupes
    on ID + name + timestamp date, not ID alone. Unlinked offline results remain
    unlinked. Arbitrary comments, labels, IDs and report objects are not exported.
    """
    report = {'status': 'disabled', 'queued': 0, 'skipped': 0}
    if telemetry.backend is None or telemetry.closed or telemetry.sample_rate <= 0:
        return report
    if trace_id is not None and (not re.fullmatch('[0-9a-f]{32}', trace_id) or int(trace_id, 16) == 0):
        return {**report, 'status': 'invalid_association'}
    if session_pseudonym is not None and not re.fullmatch('[0-9a-f]{64}', session_pseudonym):
        return {**report, 'status': 'invalid_association'}
    if trace_id is None and session_pseudonym is None:
        return {**report, 'status': 'unlinked'}
    if not isinstance(evaluated_at, datetime) or evaluated_at.utcoffset() is None or not evaluation_id:
        return {**report, 'status': 'invalid_result_identity'}
    values = result.public() if callable(getattr(result, 'public', None)) else result
    if not isinstance(values, Mapping):
        return {**report, 'status': 'invalid_result'}
    report['status'] = 'queued'
    for name, value in values.items():
        kind = ('BOOLEAN' if name in BOOLEAN_SCORES else 'NUMERIC' if name in NUMERIC_SCORES else
                'CATEGORICAL' if name in CATEGORICAL_SCORES else None)
        if kind == 'BOOLEAN' and type(value) is bool:
            value = int(value)
        elif kind == 'NUMERIC' and type(value) in (int, float) and math.isfinite(value):
            pass
        elif kind == 'CATEGORICAL' and isinstance(value, str) and value in CATEGORICAL_SCORES[name]:
            pass
        else:
            report['skipped'] += 1
            continue
        identity = '\0'.join((evaluation_id, trace_id or '', session_pseudonym or '', name))
        score = {'name': name, 'value': value, 'data_type': kind,
                 'score_id': hashlib.sha256(identity.encode()).hexdigest(), 'timestamp': evaluated_at}
        if trace_id:
            score['trace_id'] = trace_id
        if session_pseudonym:
            score['session_id'] = session_pseudonym
        try:
            telemetry.backend.score(**score)
            report['queued'] += 1
        except Exception:
            report['status'] = 'export_failed'
    return report
