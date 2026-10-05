from concierge_kiosk.agent.runtime.eval.harness import (
    grade_journey, grade_task_suite, load_journeys,
)


def _journey():
    return {
        'journey_id': 'demo',
        'turns': [
            {'turn': 1, 'utterance': 'bring towels', 'expected_route': 'service',
             'assertions': {'staff_review': False}},
            {'turn': 2, 'utterance': 'where is it?', 'expected_route': 'status',
             'assertions': {'must_not_claim_completed': True}},
        ],
    }


def test_grade_journey_tracks_completion_safety_and_latency():
    responses = [
        {'tool_route': 'service', 'requires_staff_review': False,
         'request_completed': False, 'task_completed': True},
        {'tool_route': 'request_status', 'request_completed': False,
         'citations': []},
    ]

    score = grade_journey(
        _journey(), lambda _turn, index: (responses[index - 1], index * 10))
    assert score.task_success is True
    assert score.turns_to_completion == 1
    assert score.unexpected_action_rate == 0
    assert score.route_accuracy == 1
    assert score.p95_latency_ms == 20


def test_grade_suite_exposes_requested_product_metrics():
    def runner(_turn, index):
        return {
            'tool_route': 'service' if index == 1 else 'request_status',
            'requires_staff_review': False, 'request_completed': False,
        }

    suite = grade_task_suite([_journey()], runner)
    public = suite.public()
    assert public['task_completion_rate'] == 1.0
    assert public['average_turns_to_completion'] == 2
    assert public['unexpected_action_rate'] == 0.0


def test_production_journey_fixture_is_loadable():
    rows = load_journeys('datasets/evaluation/end_to_end/journeys/production.jsonl')
    assert len(rows) == 32
    assert all(row.get('truth_status') == 'synthetic_simulation' for row in rows)
