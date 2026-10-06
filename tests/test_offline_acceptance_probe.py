from tools.operations.offline_acceptance_probe import run_probe


def test_offline_probe_is_truthful_and_does_not_claim_site_acceptance():
    result = run_probe()
    assert result['status'] == 'PASS'
    assert result['not_site_acceptance'] is True
    assert result['check_count'] == 6
    assert 'upstream_failure_is_pending_sync' in result['checks']
