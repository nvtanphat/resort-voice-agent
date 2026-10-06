"""Local unplugged acceptance probe.

This probe deliberately avoids all network calls.  It proves that the local
business store, explicit guest consent, request confirmation, public status
projection and failed external dispatch path remain truthful when the upstream
hotel system is unavailable.  It is not a substitute for microphone,
loudspeaker, Jetson thermal or physical unplugged site acceptance.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from concierge_kiosk.api.shared.status_tokens import StatusTokenService
from concierge_kiosk.domain.requests.workflows import Workflows
from concierge_kiosk.integrations.hotel_ops import HttpServiceDispatcher
from concierge_kiosk.persistence.sqlite_store import Store


PROPERTY = 'OFFLINE_PROBE'


def run_probe() -> dict:
    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix='concierge-offline-probe-') as tmp:
        database = Path(tmp) / 'business.sqlite3'
        dispatcher = HttpServiceDispatcher(
            'http://127.0.0.1:9/never-listen', 'offline-probe-token-123456', timeout_seconds=0.05)
        store = Store(database)
        workflows = Workflows(store, PROPERTY, service_dispatcher=dispatcher)
        session, _, _ = workflows.new_session()
        workflows.record_guest_consent(session, 'service_request', 'privacy-v1', True)
        assert workflows.guest_consent_granted(session, 'service_request')
        checks.append('local_consent_persisted')

        proposal = workflows.prepare(
            session, 'facilities', 'en', 'Please bring towels to my room', 'offline-probe-0001')
        request = workflows.confirm(session, proposal['id'], True)
        public = workflows.public_request_by_confirmation_code(request['confirmation_code'])
        assert public['status'] == 'pending_staff' and 'payload' not in public
        checks.append('request_confirmed_without_network')

        approved = workflows.staff_transition(
            request['id'], 'approve', 'offline-probe-staff', verified=True,
            note='Offline probe approval.', idempotency_key='offline-probe-approve-0001')
        assert approved['status'] == 'approved'
        dispatched = workflows.dispatch_request(request['id'])
        assert dispatched['external_dispatch_state'] == 'pending_sync'
        assert dispatched['external_reference'] == ''
        checks.append('upstream_failure_is_pending_sync')

        token_service = StatusTokenService('offline-status-secret-' + 'x' * 24, ttl_seconds=3600)
        token, expires_at = token_service.issue(
            property_id=PROPERTY, request_id=request['id'], now=100)
        claims = token_service.verify(token, property_id=PROPERTY, now=100)
        assert claims['request_id'] == request['id'] and expires_at == 3700
        checks.append('status_token_scoped_and_expiring')

        # Re-open the business database like an edge process restart and prove
        # the committed request remains one row with the same public reference.
        reopened = Store(database)
        with reopened.connection() as con:
            row = con.execute(
                'SELECT id,confirmation_code,status,external_dispatch_state FROM service_requests WHERE id=?',
                (request['id'],)).fetchone()
        assert row is not None and row['confirmation_code'] == request['confirmation_code']
        assert row['external_dispatch_state'] == 'pending_sync'
        checks.append('business_state_survives_restart')

        alert = workflows.queue_emergency_alert(
            session, 'en', 'Emergency assistance requested', source='sos')
        assert alert['status'] == 'open' and alert['priority'] == 100
        checks.append('emergency_is_durable_and_unresolved')

    return {
        'status': 'PASS',
        'check_count': len(checks),
        'checks': checks,
        'classification': 'local_offline_contract_probe',
        'not_site_acceptance': True,
        'not_tested': [
            'microphone_recorded_audio', 'tts_loudspeaker', 'jetson_thermal_memory_cpu',
            'physical_network_unplug', 'power_loss_reboot', 'human_staff_signoff',
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = run_probe()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
