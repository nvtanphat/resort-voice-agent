import json
from concierge_kiosk.agent.understanding import commands
from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS, ACTION_REQUEST_KINDS


def test_shortlist_is_a_hint_not_a_closed_service_authority(monkeypatch):
    payloads = []
    monkeypatch.setattr(commands, '_chat', lambda base, payload, timeout, cancel:
        payloads.append(payload) or '{"commands":[{"type":"Clarify"}]}')
    commands.model_commands(query='Guest needs assistance', language='en',
        enabled_request_kinds=ACTION_REQUEST_KINDS,
        service_candidates=[{'service_mode':'amenity_delivery'}],
        base_url='http://127.0.0.1:11434',model='mock')
    data = json.loads(payloads[0]['messages'][1]['content'])
    expected = {code for code,d in SERVICE_DEFINITIONS.items()
                if d.request_kind in ACTION_REQUEST_KINDS and d.request_kind != 'directions'}
    assert {item['service_mode'] for item in data['available_services']} == expected
    assert data['available_services'][0]['service_mode'] == 'amenity_delivery'
