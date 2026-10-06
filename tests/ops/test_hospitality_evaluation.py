from tools.evaluation.validate_hospitality import validate


def test_hospitality_evaluation_is_balanced_realistic_and_self_consistent():
    result = validate()
    assert result['scenarios'] == 270
    assert result['journeys'] == 32
    assert result['failure_cases'] == 16
    assert result['stays'] == 12000
    assert result['events'] == 50000
    assert 0.72 <= result['agent_sla_met_rate'] <= 0.93
    assert 0.12 <= result['recovery_needed_rate'] <= 0.35
