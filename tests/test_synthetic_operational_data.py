import json
from pathlib import Path

from concierge_kiosk.core.operational_policy import dispatch_policy_for_service
from concierge_kiosk.core.dataset_layout import DEPARTMENTS, dataset_path
from tools.validate_synthetic_operations import _load as _load_artifact, _load_jsonl as _load_artifact_jsonl, validate

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'datasets'


def _load(name: str):
    return _load_artifact(name)


def _jsonl(name: str):
    return _load_artifact_jsonl(name)


def test_synthetic_operations_scenario_validate_and_cover_broad_operational_data():
    result = validate()
    assert result['service_policies'] == 14
    assert result['runtime_dispatch_policies'] == 3
    assert result['maintenance_workflows'] >= 7
    assert result['maintenance_taxonomy_families'] >= 10
    assert result['provenance_sources'] >= 24
    assert result['synthetic_assumptions'] >= 35
    assert result['evaluation_scenarios'] >= 240
    assert result['synthetic_history_rows'] >= 5000
    assert result['minibar_items'] >= 12
    assert result['housekeeping_catalog_items'] >= 10
    assert result['synthetic_tour_products'] >= 5


def test_low_risk_runtime_dispatch_still_uses_data_and_cannot_gain_authority():
    assert dispatch_policy_for_service('amenity_delivery').department_id == 'HOUSEKEEPING'
    assert dispatch_policy_for_service('amenity_delivery').sla_minutes == 15
    assert dispatch_policy_for_service('housekeeping').department_id == 'HOUSEKEEPING'
    assert dispatch_policy_for_service('housekeeping').sla_minutes == 15
    maintenance = dispatch_policy_for_service('maintenance')
    assert maintenance is not None
    assert maintenance.department_id == 'ENGINEERING'
    assert maintenance.sla_minutes == 15
    payload = _load('service-policies.json')
    by_code = {row['service_code']: row for row in payload['services']}
    for code in ('late_checkout','dining_reservation','spa_reservation','tour_reservation','transport_request'):
        assert by_code[code]['approval'] == 'staff'
        assert by_code[code]['runtime_dispatch_enabled'] is False


def test_every_fabricated_property_specific_value_is_labeled_synthetic_or_external():
    minibar = _load('minibar-catalog.json')
    assert all(row['truth_status'] == 'synthetic_property_assumption' for row in minibar['items'])
    assert all(row['assumption_refs'] for row in minibar['items'])

    guest = _load('guest-policy-overrides.json')['policies']
    assert guest['wifi_demo']['truth_status'] == 'nonpublic_sensitive_property_fact'
    assert guest['ev_charging']['truth_status'] == 'nonpublic_dynamic_property_fact'
    assert 'pet_policy' not in guest
    assert guest['parking']['truth_status'] == 'external_corroborated'

    tours = _load('tour-products.json')
    assert tours['classification'] == 'synthetic_property_assumption'
    assert tours['approval'] == 'staff'
    assert all(item['synthetic_price_vnd_per_adult'] > 0 for item in tours['products'])


def test_realism_profile_keeps_truth_classes_separate():
    payload = _load('realism-profile.json')
    truth = payload['truth_classes']
    assert truth['official_public']['may_answer_as_property_fact'] is True
    for cls in ('external_corroborated','industry_benchmark','synthetic_property_assumption','synthetic_simulation'):
        assert truth[cls]['requires_label_in_guest_answer'] is True
        assert truth[cls]['may_answer_as_property_fact'] is False


def test_wifi_demo_does_not_store_a_reusable_password():
    wifi = _load('guest-policy-overrides.json')['policies']['wifi_demo']
    assert wifi['credential_storage'] == 'none'
    assert wifi['auth_details'] == 'staff_or_authorized_property_channel_required'
    assert all('password' not in key.casefold() for key in wifi)


def test_staffing_and_load_model_are_simulation_not_real_furama_headcount():
    staffing = _load('staffing-capacity.json')
    assert staffing['classification'] == 'synthetic_simulation'
    assert len(staffing['departments']) >= 4
    assert 'not' in staffing['truth_note'].casefold()
    ops = _load('property-ops-simulation.json')
    assert ops['classification'] == 'synthetic_simulation'
    bands = {row['band']: row for row in ops['occupancy_bands']}
    assert bands['high']['sla_multiplier'] > bands['normal']['sla_multiplier']
    assert bands['sold_out_like']['sla_multiplier'] > bands['high']['sla_multiplier']


def test_housekeeping_catalog_has_standard_special_and_access_sensitive_requests():
    payload = _load('housekeeping-catalog.json')
    by_code = {row['item_code']: row for row in payload['catalog']}
    assert by_code['bath_towel']['target_delivery_min'] == 15
    assert by_code['baby_cot']['target_delivery_min'] >= 30
    assert by_code['room_makeup']['access_required'] is True
    assert by_code['turndown']['access_required'] is True


def test_room_access_sop_never_lets_kiosk_force_entry_and_respects_dnd():
    payload = _load('room-access-sop.json')
    assert 'kiosk_never_authorizes_forced_entry' in payload['principles']
    assert payload['dnd']['routine_action'] == 'do_not_enter'
    assert 'human' in payload['dnd']['welfare_concern_outcome'].casefold()


def test_minibar_and_room_service_demo_prices_are_plausible_but_non_committing():
    minibar = _load('minibar-catalog.json')
    prices = [row['price_vnd'] for row in minibar['items']]
    assert min(prices) >= 20_000
    assert max(prices) <= 2_000_000
    assert minibar['inventory_policy']['kiosk_can_commit_charge'] is False

    menu = _load('room-service-synthetic-menu.json')
    assert len(menu['items']) >= 8
    assert menu['order_policy']['approval'] == 'staff'
    assert menu['order_policy']['kiosk_may_confirm_charge'] is False


def test_transport_and_tours_remain_staff_confirmed_despite_synthetic_prices():
    transport = _load('transport-products.json')
    assert all(item['approval'] == 'staff' for item in transport['products'])
    assert transport['taxi']['availability'] == '24h at front entrance'

    tours = _load('tour-products.json')
    assert tours['live_availability'] == 'always_staff_confirmed'
    assert all(item['synthetic_price_vnd_per_adult'] > 0 for item in tours['products'])


def test_demo_gap_fill_never_marks_production_gap_closed():
    payload = _load('synthetic-gap-fill.json')
    assert len(payload['fills']) >= 10
    assert all(row['official_property_validation_available'] is False for row in payload['fills'])


def test_evaluation_corpus_is_balanced_large_and_contains_truth_boundary_cases():
    rows = _jsonl('evaluation-scenarios.jsonl')
    counts = {lang: sum(row['language'] == lang for row in rows) for lang in ('vi','en','ko','zh')}
    assert set(counts) == {'vi', 'en', 'ko', 'zh'}
    assert all(count >= 30 for count in counts.values())
    routes = {row['expected_route'] for row in rows}
    assert {'service','emergency','clarification','knowledge_abstain','status','multi_step','safety_escalation','non_action'} <= routes
    assert all(row['classification'] in {'synthetic_evaluation', 'production_evaluation_synthetic'} for row in rows)


def test_synthetic_history_is_large_reproducible_pii_free_and_operationally_coherent():
    rows = _jsonl('synthetic-request-history.jsonl')
    assert len(rows) == 5000
    assert all(row['request_id'].startswith('SIM-REQ-') for row in rows)
    assert all(row['unit_ref'].startswith(('SIM-ROOM-', 'SIM-VILLA-')) for row in rows)
    assert all(row['unit_type'] in {'room', 'villa'} for row in rows)
    assert all('room_ref' not in row for row in rows)
    assert all(row['automatic_escalations'] in (0,1) for row in rows)
    assert all(key not in row for row in rows for key in ('guest_name','email','phone','passport','real_room_number'))
    service_codes = {row['service_code'] for row in rows}
    assert {'amenity_delivery','housekeeping','maintenance','food_order','transport_request','late_checkout'} <= service_codes
    completed = [row for row in rows if row['status'] == 'completed']
    cancelled = [row for row in rows if row['status'] == 'cancelled']
    assert completed and cancelled
    sla_rate = sum(bool(row['sla_met']) for row in completed) / len(completed)
    assert 0.70 <= sla_rate <= 0.95
    assert all(row['completed_at'] is None for row in cancelled)
    assert all(row['automatic_escalations'] in (0, 1) for row in cancelled)
    assert all(row['resolution_minutes'] >= row['first_attendance_minutes'] for row in completed)



def test_production_fails_closed_for_unsigned_synthetic_routing_and_sla(monkeypatch):
    monkeypatch.setenv('CONCIERGE_ENV', 'production')
    assert dispatch_policy_for_service('maintenance') is None
    assert dispatch_policy_for_service('amenity_delivery') is None
    assert dispatch_policy_for_service('housekeeping') is None


def test_business_hour_history_queues_closed_requests_and_attends_only_when_open():
    policies = {row['service_code']: row for row in _load('service-policies.json')['services']}
    rows = _jsonl('synthetic-request-history.jsonl')
    assert any(row['queued_until_open'] for row in rows if row['service_code'] == 'dining_reservation')
    assert any(row['queued_until_open'] for row in rows if row['service_code'] == 'spa_reservation')

    def clock(value):
        if value == '24:00':
            return 24 * 60
        hour, minute = map(int, value.split(':'))
        return hour * 60 + minute

    def inside(iso_value, windows):
        from datetime import datetime
        moment = datetime.fromisoformat(iso_value)
        minute = moment.hour * 60 + moment.minute + moment.second / 60
        return any(clock(w['start']) <= minute < clock(w['end']) for w in windows)

    for row in rows:
        policy = policies[row['service_code']]
        if row['status'] == 'cancelled' or policy['sla_clock_basis'] != 'business_hours':
            continue
        if row['first_attendance_at'] is not None:
            assert inside(row['first_attendance_at'], policy['staff_response_windows_local'])
        if row['staff_confirmation_at'] is not None:
            assert inside(row['staff_confirmation_at'], policy['staff_response_windows_local'])


def test_evaluation_utterances_are_unique_within_language():
    rows = _jsonl('evaluation-scenarios.jsonl')
    seen = set()
    for row in rows:
        key = (row['language'], ' '.join(row['utterance'].casefold().split()))
        assert key not in seen
        seen.add(key)


def test_synthetic_history_unit_pool_matches_official_aggregate_inventory_without_real_room_numbers():
    meta = _load('synthetic-history-metadata.json')
    model = meta['unit_reference_model']
    assert model['room_units'] == 198
    assert model['villa_units'] == 68
    assert model['total_units'] == 266
    rows = _jsonl('synthetic-request-history.jsonl')
    assert len({row['unit_ref'] for row in rows}) <= 266
    assert all(row['unit_ref'].startswith(('SIM-ROOM-', 'SIM-VILLA-')) for row in rows)


def test_canonical_departments_still_do_not_claim_unverified_internal_hours_or_extensions():
    departments = {row['department_id']: row for row in json.loads(dataset_path(DEPARTMENTS).read_text(encoding='utf-8'))}
    assert departments['HOUSEKEEPING']['operating_hours'] is None
    assert departments['FO_CONCIERGE']['internal_extension'] is None
    assert departments['BUTLER_SERVICE']['operating_hours'] == '06:00 - 00:00'
    assert departments['MEDICAL_CENTRE']['operating_hours'] == '08:00 - 17:00; except Saturday afternoon and Sunday'


def test_demo_business_dataset_is_complete_and_operationally_linked():
    summary = _load('demo-business-summary.json')
    assert summary['demo_complete'] is True
    assert all(summary['coverage'].values())
    assert summary['counts']['room_inventory_units'] == 266
    assert summary['counts']['pms_stays'] >= 200
    assert summary['counts']['departments'] >= 12
    assert summary['counts']['staffed_departments'] >= 10

    rooms = _jsonl('room-inventory-snapshot.jsonl')
    stays = _jsonl('pms-demo-stays.jsonl')
    unit_refs = {row['unit_ref'] for row in rooms}
    assert len(unit_refs) == 266
    assert sum(ref.startswith('SIM-ROOM-') for ref in unit_refs) == 198
    assert sum(ref.startswith('SIM-VILLA-') for ref in unit_refs) == 68
    assert all(row['unit_ref'] in unit_refs for row in stays)
    assert all(row['name_is_synthetic'] is True for row in stays)
    assert all(row['email'].endswith('@demo.invalid') for row in stays)


def test_demo_commercial_inventory_has_positive_limited_and_sold_out_states():
    inventory = _load('commercial-inventory-snapshot.json')
    tours = inventory['tours']
    assert len(tours) >= 18
    statuses = {row['status'] for row in tours}
    assert {'available', 'limited'} <= statuses
    assert all(row['supplier_id'].startswith('SYN-SUP-') for row in tours)

    suppliers = _load('supplier-directory.json')['suppliers']
    assert len(suppliers) >= 6
    assert len({row['supplier_id'] for row in suppliers}) == len(suppliers)

    shuttle = _load('fixed-shuttle-schedule.json')['routes'][0]
    assert len(shuttle['departures']) >= 4
    assert shuttle['seat_capacity'] >= 10
    assert shuttle['synthetic_fare_one_way_vnd'] > 0


def test_all_known_operational_gaps_are_demo_filled():
    fills = _load('synthetic-gap-fill.json')
    assert fills['prototype_coverage'] == 'complete'
    assert len(fills['fills']) == 10
    assert all(row['prototype_ready'] is True for row in fills['fills'])

    assert fills['profile_role'] == 'production_prototype_policy_coverage'
    assert all(row['official_claim_allowed'] is False for row in fills['fills'])
