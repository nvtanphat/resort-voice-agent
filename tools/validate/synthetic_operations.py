"""Validate Furama synthetic operational data and its truth boundary.

synthetic operations intentionally allows realistic fabricated prototype data (prices, staffing,
SOPs, inventory, transport/tour products) only when it is explicitly labelled
synthetic, provenance/assumption-scoped, and kept separate from canonical
Furama facts. Synthetic data may improve demo realism but cannot grant runtime
business authority or masquerade as official hotel truth.
"""
from __future__ import annotations

from datetime import datetime
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))
from concierge_kiosk.core.dataset_layout import DEPARTMENTS, SOURCES, dataset_path, dataset_root
from concierge_kiosk.core.domain_profile import supported_languages

DATA = dataset_root()
SYN = DATA / 'synthetic' / 'operations'

# The validator keeps its historical logical names so its checks remain easy
# to read, but every name resolves through the canonical datasets/ layout.
_ARTIFACTS = {
    'service-policies.json': 'policies/service_policies.json',
    'departments.json': 'staffing/departments.json',
    'escalation-policy.json': 'policies/escalation_policy.json',
    'tour-operations.json': 'tours/operations.json',
    'source-register.json': 'metadata/sources.json',
    'assumption-register.json': 'metadata/assumptions.json',
    'operational-constraints.json': 'policies/constraints.json',
    'maintenance-taxonomy.json': 'engineering/maintenance_taxonomy.json',
    'evaluation-scenarios.jsonl': '../../evaluation/end_to_end/scenarios/production.jsonl',
    'realism-profile.json': 'metadata/realism_profile.json',
    'property-ops-simulation.json': 'metadata/property_simulation.json',
    'staffing-capacity.json': 'staffing/capacity.json',
    'housekeeping-catalog.json': 'housekeeping/catalog.json',
    'amenity-inventory.json': 'rooms/amenities.json',
    'minibar-catalog.json': 'food_beverage/minibar.json',
    'room-service-synthetic-menu.json': 'food_beverage/room_service_menu.json',
    'transport-products.json': 'transport/products.json',
    'guest-policy-overrides.json': 'policies/guest_overrides.json',
    'room-access-sop.json': 'rooms/access_sop.json',
    'escalation-matrix.json': 'policies/escalation_matrix.json',
    'tour-products.json': 'tours/products.json',
    'synthetic-gap-fill.json': 'metadata/coverage.json',
    'synthetic-history-metadata.json': 'history/metadata.json',
    'synthetic-history-summary.json': 'history/summary.json',
    'synthetic-request-history.jsonl': 'history/requests.jsonl',
    'demo-business-summary.json': 'metadata/demo_business_summary.json',
    'fixed-shuttle-schedule.json': 'transport/shuttle_schedule.json',
    'supplier-directory.json': 'suppliers/directory.json',
    'commercial-inventory-snapshot.json': 'inventory/commercial_snapshot.json',
    'restaurant-operations.json': 'food_beverage/restaurants.json',
    'spa-operations.json': 'spa/operations.json',
    'service-recovery-matrix.json': 'billing/service_recovery.json',
    'billing-charge-rules.json': 'billing/charge_rules.json',
    'pms-demo-stays.jsonl': 'pms/stays.jsonl',
    'room-inventory-snapshot.jsonl': 'rooms/inventory.jsonl',
    'maintenance-workflows.jsonl': 'engineering/workflows.jsonl',
}


def _load(name: str):
    relative = _ARTIFACTS.get(name, name)
    path = (SYN / relative).resolve()
    return json.loads(path.read_text(encoding='utf-8'))


def _load_jsonl(name: str) -> list[dict]:
    relative = _ARTIFACTS.get(name, name)
    path = (SYN / relative).resolve()
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _valid_minutes(value, *, allow_none: bool = False) -> bool:
    if value is None:
        return allow_none
    return not isinstance(value, bool) and isinstance(value, int) and 1 <= value <= 720


def _validate_window(value, *, label: str, allow_none: bool = True) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, list) or len(value) != 2 or any(not _valid_minutes(v) for v in value):
        raise ValueError(f'{label}: expected two valid minute values')
    if value[0] > value[1]:
        raise ValueError(f'{label}: minimum exceeds maximum')


def _clock_minutes(value: str) -> int:
    if value == '24:00':
        return 24 * 60
    try:
        hour, minute = map(int, value.split(':'))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f'invalid local clock {value!r}') from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f'invalid local clock {value!r}')
    return hour * 60 + minute


def _validate_response_windows(value, *, label: str) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f'{label}: staff_response_windows_local must be non-empty')
    previous_end = -1
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get('basis'), str) or not item['basis'].strip():
            raise ValueError(f'{label}: response window needs basis')
        start = _clock_minutes(item.get('start'))
        end = _clock_minutes(item.get('end'))
        if not 0 <= start < end <= 24 * 60 or start < previous_end:
            raise ValueError(f'{label}: invalid/overlapping response windows')
        previous_end = end


def _moment_in_windows(moment: datetime, windows: list[dict]) -> bool:
    minute = moment.hour * 60 + moment.minute + moment.second / 60
    return any(_clock_minutes(item['start']) <= minute < _clock_minutes(item['end']) for item in windows)


def _normalized_utterance(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _refs_ok(values, known: set[str], *, label: str) -> None:
    if not isinstance(values, list) or any(value not in known for value in values):
        raise ValueError(f'{label}: unknown reference')


def _require_synthetic(payload: dict, *, label: str, allowed: set[str] | None = None) -> None:
    allowed = allowed or {'synthetic_operational', 'synthetic_property_assumption', 'synthetic_simulation', 'synthetic_operational_meta'}
    if payload.get('property_id') != 'FURAMA_DANANG' or payload.get('classification') not in allowed:
        raise ValueError(f'{label}: must be explicitly synthetic for FURAMA_DANANG')


def validate() -> dict[str, int]:
    policies = _load('service-policies.json')
    departments_payload = _load('departments.json')
    escalation = _load('escalation-policy.json')
    tours = _load('tour-operations.json')
    source_register = _load('source-register.json')
    assumptions = _load('assumption-register.json')
    constraints = _load('operational-constraints.json')
    taxonomy = _load('maintenance-taxonomy.json')
    scenarios = _load_jsonl('evaluation-scenarios.jsonl')

    realism = _load('realism-profile.json')
    ops_sim = _load('property-ops-simulation.json')
    staffing = _load('staffing-capacity.json')
    housekeeping = _load('housekeeping-catalog.json')
    amenity_inventory = _load('amenity-inventory.json')
    minibar = _load('minibar-catalog.json')
    room_service = _load('room-service-synthetic-menu.json')
    transport = _load('transport-products.json')
    guest_policies = _load('guest-policy-overrides.json')
    room_access = _load('room-access-sop.json')
    escalation_matrix = _load('escalation-matrix.json')
    tour_products = _load('tour-products.json')
    gap_fill = _load('synthetic-gap-fill.json')
    history_meta = _load('synthetic-history-metadata.json')
    history_summary = _load('synthetic-history-summary.json')
    history = _load_jsonl('synthetic-request-history.jsonl')
    demo_summary = _load('demo-business-summary.json')
    shuttle = _load('fixed-shuttle-schedule.json')
    suppliers = _load('supplier-directory.json')
    commercial_inventory = _load('commercial-inventory-snapshot.json')
    restaurant_ops = _load('restaurant-operations.json')
    spa_ops = _load('spa-operations.json')
    service_recovery = _load('service-recovery-matrix.json')
    billing = _load('billing-charge-rules.json')
    pms_stays = _load_jsonl('pms-demo-stays.jsonl')
    room_inventory = _load_jsonl('room-inventory-snapshot.jsonl')

    _require_synthetic(policies, label='service-policies')
    rows = policies.get('services')
    if not isinstance(rows, list) or not rows:
        raise ValueError('service-policies.services must be a non-empty list')

    # Provenance namespace.
    sources = source_register.get('sources')
    if source_register.get('property_id') != 'FURAMA_DANANG' or not isinstance(sources, list) or not sources:
        raise ValueError('source-register must contain Furama provenance sources')
    source_ids = {item.get('source_id') for item in sources if isinstance(item, dict)}
    if None in source_ids or len(source_ids) != len(sources):
        raise ValueError('source-register source_id values must be present and unique')
    official_sources = {item['source_id'] for item in sources if item.get('source_type') == 'official_property_web'}
    curated_source_artifacts = {
        item.get('artifact_id'): item
        for item in [json.loads(line) for line in dataset_path(SOURCES).read_text(encoding='utf-8').splitlines() if line.strip()]
    }
    for item in sources:
        if item.get('source_type') == 'official_property_web':
            url = item.get('url')
            if not isinstance(url, str) or not url.startswith('https://furamavietnam.com/'):
                raise ValueError(f"{item.get('source_id')}: official property source must use Furama host")
            artifact = curated_source_artifacts.get(item.get('dataset_artifact_id'))
            if artifact is None or artifact.get('verification_state') != 'verified_evidence' or artifact.get('source_url') != url:
                raise ValueError(f"{item.get('source_id')}: official source must link to matching verified dataset evidence")
        elif not item.get('limitations'):
            raise ValueError(f"{item.get('source_id')}: non-property source must state limitations")

    # Assumption register.
    assumption_rows = assumptions.get('assumptions')
    if assumptions.get('classification') != 'synthetic_operational_assumptions' or not isinstance(assumption_rows, list) or not assumption_rows:
        raise ValueError('assumption-register must explicitly contain synthetic operational assumptions')
    assumption_ids = {row.get('assumption_id') for row in assumption_rows if isinstance(row, dict)}
    if None in assumption_ids or len(assumption_ids) != len(assumption_rows):
        raise ValueError('assumption IDs must be present and unique')
    for row in assumption_rows:
        confidence = row.get('confidence')
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError(f"{row.get('assumption_id')}: confidence must be between 0 and 1")
        if (row.get('hotel_signoff_required') is not True
                and row.get('product_policy_approved') is not True) or not row.get('replacement_rule'):
            raise ValueError(f"{row.get('assumption_id')}: synthetic assumption must be replaceable and explicitly approved")
        if row.get('official_claim_allowed') is not False:
            raise ValueError(f"{row.get('assumption_id')}: synthetic assumption cannot authorize an official claim")
        _refs_ok(row.get('source_refs', []), source_ids, label=f"{row.get('assumption_id')}.source_refs")

    # Service policy coverage / runtime authority.
    from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS
    expected_codes = set(SERVICE_DEFINITIONS)
    seen_codes = {row.get('service_code') for row in rows if isinstance(row, dict)}
    if seen_codes != expected_codes:
        raise ValueError(f'synthetic service policy coverage mismatch: missing={sorted(expected_codes-seen_codes)} extra={sorted(seen_codes-expected_codes)}')

    canonical_departments = {
        row['department_id'] for row in json.loads(dataset_path(DEPARTMENTS).read_text(encoding='utf-8'))
        if isinstance(row, dict) and isinstance(row.get('department_id'), str)
    }
    synthetic_departments = departments_payload.get('departments', [])
    for item in synthetic_departments:
        _refs_ok(item.get('source_refs', []), source_ids, label=f"{item.get('department_id')}.source_refs")
        _refs_ok(item.get('assumption_refs', []), assumption_ids, label=f"{item.get('department_id')}.assumption_refs")
        if item.get('operating_hours') is not None:
            status = item.get('operating_hours_status')
            if status not in {'officially_verified', 'synthetic_demo_schedule'}:
                raise ValueError(f"{item.get('department_id')}: operating hours need official or synthetic-demo status")
            if status == 'synthetic_demo_schedule' and not str(item.get('truth_status', '')).startswith('synthetic_'):
                raise ValueError(f"{item.get('department_id')}: synthetic demo hours must be explicitly labeled")
    synthetic_ids = {row['department_id'] for row in synthetic_departments if isinstance(row, dict) and isinstance(row.get('department_id'), str)}
    known_departments = canonical_departments | synthetic_ids | {'FRONT_OFFICE'}

    runtime_count = 0
    for row in rows:
        code = row.get('service_code')
        if row.get('department_id') not in known_departments:
            raise ValueError(f'{code}: unknown department')
        minutes = row.get('sla_minutes')
        escalation_minutes = row.get('escalation_after_minutes')
        if not _valid_minutes(minutes) or not _valid_minutes(escalation_minutes) or escalation_minutes < minutes:
            raise ValueError(f'{code}: invalid SLA/escalation')
        priority = row.get('priority')
        if priority is not None:
            if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
                raise ValueError(f'{code}: priority must be an integer from 1 to 5')
        elif row.get('priority_on_escalation') not in {'normal', 'high', 'urgent', 'critical'}:
            raise ValueError(f'{code}: priority_on_escalation is invalid')
        _refs_ok(row.get('source_refs', []), source_ids, label=f'{code}.source_refs')
        _refs_ok(row.get('assumption_refs', []), assumption_ids, label=f'{code}.assumption_refs')
        timing = row.get('timing')
        if not isinstance(timing, dict) or timing.get('completion_is_not_promised') is not True:
            raise ValueError(f'{code}: timing must distinguish target from completion')
        for key in ('first_staff_response_target_min', 'first_attendance_target_min'):
            if not _valid_minutes(timing.get(key), allow_none=True):
                raise ValueError(f'{code}: invalid {key}')
        _validate_window(timing.get('expected_resolution_window_min'), label=f'{code}.resolution')
        _validate_response_windows(row.get('staff_response_windows_local'), label=code)
        if row.get('sla_clock_basis') not in {'wall_clock', 'business_hours'}:
            raise ValueError(f'{code}: invalid sla_clock_basis')
        if row.get('runtime_dispatch_enabled'):
            runtime_count += 1
            definition = SERVICE_DEFINITIONS.get(str(code))
            if definition is None or definition.approval != 'none' or definition.risk != 'low' or not definition.reversible:
                raise ValueError(f'{code}: synthetic data cannot grant runtime business authority')
            attendance = timing.get('first_attendance_target_min')
            if attendance is not None and minutes != attendance:
                raise ValueError(f'{code}: runtime SLA must match first attendance target')
        for url in row.get('public_anchors', []):
            if not isinstance(url, str) or not url.startswith('https://furamavietnam.com/'):
                raise ValueError(f'{code}: non-official public anchor')
        for ref in row.get('official_source_refs', []):
            if ref not in official_sources:
                raise ValueError(f'{code}: invalid official source ref')

    _require_synthetic(escalation, label='escalation-policy')
    _require_synthetic(constraints, label='operational-constraints')
    _refs_ok(constraints.get('source_refs', []), source_ids, label='operational-constraints.source_refs')

    # Legacy/public tour guidance remains conservative. Synthetic commercial products live separately.
    if tours.get('classification') != 'synthetic_operational_with_public_destination_anchors':
        raise ValueError('tour-operations provenance boundary invalid')
    for item in tours.get('destinations', []):
        if item.get('price') is not None or item.get('availability') != 'staff_confirmed':
            raise ValueError('public destination guidance must not invent live commercial data')

    # synthetic operations truth boundary: fabrication is allowed only in explicitly synthetic files/rows.
    for name, payload in [
        ('realism-profile', realism), ('property-ops-simulation', ops_sim), ('staffing-capacity', staffing),
        ('housekeeping-catalog', housekeeping), ('amenity-inventory', amenity_inventory), ('minibar-catalog', minibar),
        ('room-service-synthetic-menu', room_service), ('transport-products', transport), ('guest-policy-overrides', guest_policies),
        ('room-access-sop', room_access), ('escalation-matrix', escalation_matrix), ('tour-products', tour_products),
        ('synthetic-gap-fill', gap_fill), ('synthetic-history-metadata', history_meta), ('synthetic-history-summary', history_summary),
        ('demo-business-summary', demo_summary), ('fixed-shuttle-schedule', shuttle), ('supplier-directory', suppliers),
        ('commercial-inventory-snapshot', commercial_inventory), ('restaurant-operations', restaurant_ops), ('spa-operations', spa_ops),
        ('service-recovery-matrix', service_recovery), ('billing-charge-rules', billing),
    ]:
        _require_synthetic(payload, label=name, allowed={'synthetic_operational','synthetic_property_assumption','synthetic_simulation','synthetic_operational_meta'})

    if realism.get('truth_classes', {}).get('synthetic_property_assumption', {}).get('requires_label_in_guest_answer') is not True:
        raise ValueError('realism-profile must require labeling synthetic assumptions')
    production_gate = str(realism.get('production_gate', '')).casefold()
    if 'production' not in production_gate or not any(
            marker in production_gate for marker in ('sign-off', 'authorized', 'staff-confirmed')):
        raise ValueError('realism-profile must state the production authorization gate')

    inventory_basis = ops_sim.get('inventory_basis', {})
    if (inventory_basis.get('simulated_rooms_and_suites') != 198
            or inventory_basis.get('simulated_private_pool_villas') != 68
            or inventory_basis.get('simulated_total_units') != 266):
        raise ValueError('property simulation inventory must align with official aggregate 198 rooms/suites + 68 villas')
    _refs_ok(inventory_basis.get('source_refs', []), source_ids, label='property-ops-simulation.inventory_basis.source_refs')
    if any(row.get('truth_status') != 'synthetic_simulation' for row in ops_sim.get('occupancy_bands', [])):
        raise ValueError('occupancy bands must be row-labelled synthetic_simulation')
    if any(row.get('truth_status') != 'synthetic_simulation' for row in ops_sim.get('daypart_load', [])):
        raise ValueError('daypart load must be row-labelled synthetic_simulation')

    # Staffing covers a full 24h for core simulated departments but cannot claim real headcount.
    if len(staffing.get('departments', [])) < 4:
        raise ValueError('staffing simulation too sparse')
    if 'real' in str(staffing.get('truth_note','')).casefold() and 'not' not in str(staffing.get('truth_note','')).casefold():
        raise ValueError('staffing simulation truth note unclear')

    # Housekeeping/inventory realism.
    if len(housekeeping.get('catalog', [])) < 10 or len(amenity_inventory.get('items', [])) < 8:
        raise ValueError('housekeeping/inventory catalog too sparse')
    _refs_ok(housekeeping.get('source_refs', []), source_ids, label='housekeeping.source_refs')
    _refs_ok(housekeeping.get('assumption_refs', []), assumption_ids, label='housekeeping.assumption_refs')
    for item in housekeeping.get('catalog', []):
        if item.get('truth_status') != 'synthetic_property_assumption':
            raise ValueError(f"housekeeping {item.get('item_code')}: mixed operational values must carry row-level synthetic label")
        _refs_ok(item.get('assumption_refs', []), assumption_ids, label=f"housekeeping {item.get('item_code')}.assumption_refs")
        _refs_ok(item.get('official_anchor_refs', []), source_ids, label=f"housekeeping {item.get('item_code')}.official_anchor_refs")
    for item in amenity_inventory.get('items', []):
        if item.get('truth_status') != 'synthetic_simulation':
            raise ValueError(f"inventory {item.get('item_code')}: row must be synthetic_simulation")
        _refs_ok(item.get('assumption_refs', []), assumption_ids, label=f"inventory {item.get('item_code')}.assumption_refs")
    for item in staffing.get('departments', []):
        if item.get('truth_status') != 'synthetic_simulation':
            raise ValueError(f"staffing {item.get('department_id')}: row must be synthetic_simulation")
        _refs_ok(item.get('assumption_refs', []), assumption_ids, label=f"staffing {item.get('department_id')}.assumption_refs")

    # Fabricated prices are permitted only in synthetic catalog with assumption/source labels.
    minibar_items = minibar.get('items', [])
    if len(minibar_items) < 10:
        raise ValueError('minibar catalog too sparse')
    for item in minibar_items:
        price = item.get('price_vnd')
        if not isinstance(price, int) or not 20_000 <= price <= 2_000_000:
            raise ValueError(f"minibar {item.get('item_id')}: implausible synthetic price")
        if item.get('truth_status') != 'synthetic_property_assumption':
            raise ValueError('minibar fabricated prices must be explicitly synthetic')
        _refs_ok(item.get('assumption_refs', []), assumption_ids, label=f"minibar {item.get('item_id')}.assumption_refs")
        _refs_ok(item.get('source_refs', []), source_ids, label=f"minibar {item.get('item_id')}.source_refs")

    if len(room_service.get('items', [])) < 8:
        raise ValueError('room-service demo menu too sparse')
    for item in room_service['items']:
        if not isinstance(item.get('price_vnd'), int) or item['price_vnd'] < 100_000:
            raise ValueError(f"room-service {item.get('item_id')}: implausible demo price")
        if item.get('truth_status') != 'synthetic_property_assumption':
            raise ValueError(f"room-service {item.get('item_id')}: fabricated menu row must be synthetic")
        _refs_ok(item.get('assumption_refs', []), assumption_ids, label=f"room-service {item.get('item_id')}.assumption_refs")
        _refs_ok(item.get('source_refs', []), source_ids, label=f"room-service {item.get('item_id')}.source_refs")
    if '5% service charge' in str(room_service.get('price_semantics', '')).casefold() or '5% service charge' in str(minibar.get('price_semantics', '')).casefold():
        raise ValueError('unverified fixed service-charge percentage must not be presented as Furama pricing semantics')

    # Guest-policy overrides must keep every provenance/assumption reference resolvable.
    for key, policy in guest_policies.get('policies', {}).items():
        _refs_ok(policy.get('source_refs', []), source_ids, label=f'guest-policy {key}.source_refs')
        _refs_ok(policy.get('assumption_refs', []), assumption_ids, label=f'guest-policy {key}.assumption_refs')

    # Wi-Fi demo must not store a reusable real-looking password; EV must remain labelled synthetic.
    wifi = guest_policies.get('policies', {}).get('wifi_demo', {})
    if any('password' in key.casefold() for key in wifi):
        raise ValueError('Wi-Fi demo must not store a shared password field')
    if wifi.get('truth_status') not in {
            'synthetic_property_assumption', 'nonpublic_sensitive_property_fact'}:
        raise ValueError('Wi-Fi demo must be explicitly nonpublic or synthetic')
    ev = guest_policies.get('policies', {}).get('ev_charging', {})
    if ev.get('truth_status') not in {
            'synthetic_property_assumption', 'nonpublic_dynamic_property_fact'} or not ev.get('assumption_refs'):
        raise ValueError('EV demo data must be labelled nonpublic or synthetic')

    # Transport/tours require staff approval and synthetic pricing label.
    if any(item.get('approval') != 'staff' for item in transport.get('products', [])):
        raise ValueError('synthetic transport products must remain staff-approved')
    for item in transport.get('products', []):
        if item.get('truth_status') != 'synthetic_property_assumption':
            raise ValueError(f"transport {item.get('product_id')}: product must be labelled synthetic")
        _refs_ok(item.get('assumption_refs', []), assumption_ids, label=f"transport {item.get('product_id')}.assumption_refs")
        _refs_ok(item.get('source_refs', []), source_ids, label=f"transport {item.get('product_id')}.source_refs")
    if transport.get('airport_context', {}).get('distance_status') == 'official/external corroborated':
        raise ValueError('airport distance must not imply support from the Furama taxi page')
    if tour_products.get('approval') != 'staff' or tour_products.get('live_availability') != 'always_staff_confirmed':
        raise ValueError('synthetic tour products must remain staff-confirmed')
    if len(tour_products.get('products', [])) < 5:
        raise ValueError('tour product catalog too sparse')
    for item in tour_products.get('products', []):
        if item.get('truth_status') != 'synthetic_property_assumption' or item.get('cancellation') != 'staff_confirmed':
            raise ValueError(f"tour {item.get('product_id')}: current commercial terms must remain synthetic/staff-confirmed")
        _refs_ok(item.get('assumption_refs', []), assumption_ids, label=f"tour {item.get('product_id')}.assumption_refs")
        _refs_ok(item.get('source_refs', []), source_ids, label=f"tour {item.get('product_id')}.source_refs")

    # Room access remains conservative even though SOP is synthetic.
    principles = set(room_access.get('principles', []))
    if 'kiosk_never_authorizes_forced_entry' not in principles or room_access.get('dnd', {}).get('routine_action') != 'do_not_enter':
        raise ValueError('synthetic room-access SOP weakens safety boundary')
    if any(row.get('automatic_notifications_max') != 1 for row in escalation_matrix.get('matrix', [])):
        raise ValueError('automatic escalation must remain idempotent')
    for row in escalation_matrix.get('matrix', []):
        if row.get('truth_status') != 'synthetic_property_assumption':
            raise ValueError(f"escalation {row.get('department_id')}: row must be labelled synthetic")
        _refs_ok(row.get('assumption_refs', []), assumption_ids, label=f"escalation {row.get('department_id')}.assumption_refs")

    # Every known real-production gap has a concrete synthetic demo implementation.
    fills = gap_fill.get('fills', [])
    if gap_fill.get('prototype_coverage') != 'complete' or len(fills) < 10:
        raise ValueError('synthetic gap coverage must be complete for demo mode')
    if any(row.get('official_property_validation_available') is not False
           or row.get('prototype_ready') is not True
           or row.get('official_claim_allowed') is not False for row in fills):
        raise ValueError('every synthetic gap fill must be demo-ready while preserving the real-production truth boundary')

    # Demo-complete hotel operations sandbox.
    if demo_summary.get('demo_complete') is not True or not all(demo_summary.get('coverage', {}).values()):
        raise ValueError('demo-business-summary must report complete operational coverage')
    if len(departments_payload.get('departments', [])) < 12:
        raise ValueError('synthetic department directory too sparse for full demo')
    if len(staffing.get('departments', [])) < 10:
        raise ValueError('synthetic staffing capacity too sparse for full demo')

    routes = shuttle.get('routes', [])
    if not routes or any(r.get('truth_status') != 'synthetic_property_assumption' for r in routes):
        raise ValueError('fixed shuttle schedule missing or unlabeled')
    for route in routes:
        if not isinstance(route.get('seat_capacity'), int) or route['seat_capacity'] < 4 or len(route.get('departures', [])) < 2:
            raise ValueError('fixed shuttle route lacks realistic capacity/timetable')
        if not isinstance(route.get('synthetic_fare_one_way_vnd'), int) or route['synthetic_fare_one_way_vnd'] <= 0:
            raise ValueError('fixed shuttle route lacks demo fare')

    supplier_rows = suppliers.get('suppliers', [])
    if len(supplier_rows) < 6 or len({r.get('supplier_id') for r in supplier_rows}) != len(supplier_rows):
        raise ValueError('supplier directory too sparse or duplicated')
    if any(not str(r.get('supplier_id', '')).startswith('SYN-SUP-') for r in supplier_rows):
        raise ValueError('supplier IDs must remain explicitly synthetic')

    if len(room_inventory) != 266 or len({r.get('unit_ref') for r in room_inventory}) != 266:
        raise ValueError('demo room inventory must contain 266 unique synthetic units')
    if sum(str(r.get('unit_ref','')).startswith('SIM-ROOM-') for r in room_inventory) != 198:
        raise ValueError('demo room inventory must preserve 198 room/suite units')
    if sum(str(r.get('unit_ref','')).startswith('SIM-VILLA-') for r in room_inventory) != 68:
        raise ValueError('demo room inventory must preserve 68 villa units')
    valid_room_states = {'occupied','vacant_clean','vacant_dirty','out_of_order'}
    if any(r.get('classification') != 'synthetic_simulation' or r.get('occupancy_status') not in valid_room_states for r in room_inventory):
        raise ValueError('demo room inventory contains invalid state or unlabeled row')

    unit_refs = {r['unit_ref'] for r in room_inventory}
    if len(pms_stays) < 200:
        raise ValueError('PMS demo stays too sparse')
    for row in pms_stays:
        if row.get('classification') != 'synthetic_simulation' or row.get('unit_ref') not in unit_refs:
            raise ValueError('PMS demo stay must reference a synthetic inventory unit')
        if row.get('name_is_synthetic') is not True or not str(row.get('email','')).endswith('@demo.invalid'):
            raise ValueError('PMS demo identity must be explicitly synthetic')
        if row.get('status') not in {'IN_HOUSE','ARRIVING'}:
            raise ValueError('PMS demo stay has unsupported status')

    restaurant_rows = restaurant_ops.get('venues', [])
    if len(restaurant_rows) < 6 or any(not r.get('slots') for r in restaurant_rows):
        raise ValueError('restaurant demo capacity too sparse')
    if len(spa_ops.get('slots', [])) < 30 or spa_ops.get('truth_status') != 'synthetic_property_assumption':
        raise ValueError('spa demo capacity too sparse or unlabeled')

    tour_inventory = commercial_inventory.get('tours', [])
    transport_inventory = commercial_inventory.get('transport', [])
    if len(tour_inventory) < len(tour_products.get('products', [])) * 3 or len(transport_inventory) < 6:
        raise ValueError('commercial demo inventory too sparse')
    if not any(r.get('status') == 'limited' for r in tour_inventory) or not any(r.get('status') == 'available' for r in tour_inventory):
        raise ValueError('commercial demo inventory needs varied availability states')

    if len(service_recovery.get('levels', [])) < 4:
        raise ValueError('service recovery matrix too sparse')
    if len(billing.get('charge_codes', [])) < 6 or billing.get('currency') != 'VND':
        raise ValueError('billing demo rules too sparse')

    # Maintenance workflows/taxonomy.
    maintenance_rows = _load_jsonl('maintenance-workflows.jsonl')
    if len(maintenance_rows) < 7 or any(row.get('classification') != 'synthetic_operational' for row in maintenance_rows):
        raise ValueError('maintenance workflow coverage too sparse or unlabeled')
    for row in maintenance_rows:
        _refs_ok(row.get('source_refs', []), source_ids, label=f"{row.get('workflow_id')}.source_refs")
        _validate_window(row.get('timing', {}).get('expected_resolution_window_min'), label=f"{row.get('workflow_id')}.resolution")
        if not row.get('safety_exclusions'):
            raise ValueError(f"{row.get('workflow_id')}: missing safety exclusions")
    if taxonomy.get('classification') != 'synthetic_operational_taxonomy':
        raise ValueError('maintenance taxonomy must be explicitly synthetic')
    families = taxonomy.get('issue_families', [])
    if len(families) < 8:
        raise ValueError('maintenance taxonomy too sparse')
    for family in families:
        _refs_ok(family.get('source_refs', []), source_ids, label=f"maintenance {family.get('family')}.source_refs")
        if not family.get('hazard_escalation'):
            raise ValueError(f"maintenance {family.get('family')}: missing hazard exit")
        _validate_window(family.get('expected_resolution_window_min'), label=f"maintenance {family.get('family')}.resolution")

    # Evaluation corpus: balanced and broad.
    if not scenarios or any(row.get('classification') not in {
            'synthetic_evaluation', 'production_evaluation_synthetic'} for row in scenarios):
        raise ValueError('evaluation scenarios must be explicitly synthetic')
    languages = set(supported_languages())
    counts = {lang:0 for lang in languages}
    scenario_ids: set[str] = set()
    normalized_utterances: set[tuple[str, str]] = set()
    for row in scenarios:
        sid = row.get('scenario_id'); lang = row.get('language')
        if not isinstance(sid, str) or not sid or sid in scenario_ids:
            raise ValueError('evaluation scenario IDs must be unique')
        scenario_ids.add(sid)
        if lang not in languages or not isinstance(row.get('utterance'), str) or not row['utterance'].strip():
            raise ValueError(f'{sid}: invalid scenario')
        utterance_key = (lang, _normalized_utterance(row['utterance']))
        if utterance_key in normalized_utterances:
            raise ValueError(f'{sid}: duplicate normalized evaluation utterance')
        normalized_utterances.add(utterance_key)
        counts[lang] += 1
    if set(counts) != languages or min(counts.values()) < 1:
        raise ValueError(f'evaluation corpus must cover every configured language: {counts}')

    # Operational history: deterministic, PII-free, business-hour aware and internally coherent.
    if history_meta.get('contains_real_guest_data') is not False or history_meta.get('contains_real_room_numbers') is not False:
        raise ValueError('synthetic history metadata must deny real guest/room data')
    unit_model = history_meta.get('unit_reference_model', {})
    if unit_model.get('room_units') != 198 or unit_model.get('villa_units') != 68 or unit_model.get('total_units') != 266:
        raise ValueError('synthetic unit pool must derive from official aggregate room/villa counts')
    _refs_ok(unit_model.get('source_refs', []), source_ids, label='synthetic-history-metadata.unit_reference_model.source_refs')
    if len(history) < 5000:
        raise ValueError('synthetic history too small for analytics demo')
    policy_by_code = {row['service_code']: row for row in rows}
    valid_codes = expected_codes
    seen_unit_refs: set[str] = set()
    for row in history:
        if row.get('classification') != 'synthetic_simulation' or row.get('truth_status') != 'synthetic_simulation':
            raise ValueError('history row is not explicitly synthetic')
        unit_type = row.get('unit_type'); unit_ref = str(row.get('unit_ref', ''))
        if row.get('service_code') not in valid_codes or not str(row.get('request_id','')).startswith('SIM-REQ-'):
            raise ValueError('history identifiers/service invalid')
        if unit_type == 'room' and not unit_ref.startswith('SIM-ROOM-'):
            raise ValueError('synthetic room identifier invalid')
        if unit_type == 'villa' and not unit_ref.startswith('SIM-VILLA-'):
            raise ValueError('synthetic villa identifier invalid')
        if unit_type not in {'room','villa'}:
            raise ValueError('synthetic unit type invalid')
        seen_unit_refs.add(unit_ref)
        if any(key in row for key in ('guest_name','email','phone','passport','real_room_number','room_ref')):
            raise ValueError('synthetic history must remain PII-free and use opaque unit_ref, not room_ref')
        created = datetime.fromisoformat(row['created_at'])
        ack = datetime.fromisoformat(row['acknowledged_at'])
        sla_start = datetime.fromisoformat(row['sla_clock_started_at'])
        if not (created <= ack and created <= sla_start):
            raise ValueError('history acknowledgement/SLA start precedes creation')
        policy = policy_by_code[row['service_code']]
        if row.get('sla_clock_basis') != policy.get('sla_clock_basis'):
            raise ValueError('history SLA clock basis does not match service policy')
        queue_wait = int(row.get('queue_wait_minutes', -1))
        expected_queue = max(0, (sla_start - created).total_seconds() / 60)
        if abs(queue_wait - round(expected_queue)) > 1 or bool(queue_wait) != bool(row.get('queued_until_open')):
            raise ValueError('history queue-wait fields inconsistent')
        if policy.get('sla_clock_basis') == 'business_hours' and not _moment_in_windows(sla_start, policy['staff_response_windows_local']):
            raise ValueError('business-hours SLA clock must begin inside a response window')
        if row.get('automatic_escalations') not in (0,1):
            raise ValueError('history automatic escalation must be idempotent')
        status = row.get('status')
        attendance = (datetime.fromisoformat(row['first_attendance_at'])
                      if row.get('first_attendance_at') else None)
        completed = (datetime.fromisoformat(row['completed_at'])
                     if row.get('completed_at') else None)
        staff_confirmation = (datetime.fromisoformat(row['staff_confirmation_at'])
                              if row.get('staff_confirmation_at') else None)
        cancelled_at = (datetime.fromisoformat(row['cancelled_at'])
                        if row.get('cancelled_at') else None)
        for timestamp in (staff_confirmation, attendance, completed, cancelled_at):
            if timestamp is not None and timestamp < ack:
                raise ValueError('history timestamp precedes acknowledgement')
        if staff_confirmation is not None and attendance is not None and staff_confirmation > attendance:
            raise ValueError('staff confirmation timestamp out of order')
        if attendance is not None and completed is not None and completed < attendance:
            raise ValueError('completion timestamp precedes attendance')
        if cancelled_at is not None and completed is not None:
            raise ValueError('cancelled history row cannot also be completed')
        if status == 'completed' and (attendance is None or completed is None or cancelled_at is not None):
            raise ValueError('completed history row must have attendance/completion only')
        if status == 'cancelled' and cancelled_at is None:
            raise ValueError('cancelled history row requires cancelled_at')
        if status in {'failed', 'reopened', 'escalated'} and completed is not None:
            raise ValueError(f'{status} history row cannot claim completion')
        if policy.get('sla_clock_basis') == 'business_hours' and attendance is not None:
            if not _moment_in_windows(attendance, policy['staff_response_windows_local']):
                raise ValueError(f"{row['service_code']}: staff attendance outside configured response window")
            if staff_confirmation is not None and not _moment_in_windows(staff_confirmation, policy['staff_response_windows_local']):
                raise ValueError(f"{row['service_code']}: staff confirmation outside configured response window")
        if attendance is not None:
            resolution = row.get('resolution_minutes')
            first_attendance = row.get('first_attendance_minutes')
            if resolution is not None and first_attendance is not None and resolution < first_attendance:
                raise ValueError('history resolution cannot precede first attendance')
            elapsed = row.get('sla_elapsed_to_attendance_min')
            if not isinstance(elapsed, int) or elapsed < 1:
                raise ValueError('history SLA elapsed attendance invalid')
            if bool(row.get('sla_met')) != (elapsed <= row.get('effective_sla_min_simulated')):
                raise ValueError('history SLA outcome inconsistent with service-clock elapsed time')
    if len(seen_unit_refs) > 266:
        raise ValueError('synthetic history uses more units than official aggregate inventory')

    # Summary must be reproducible from the actual history, not hand-edited.
    if history_summary.get('row_count') != len(history):
        raise ValueError('synthetic history summary row count does not match history')
    status_counts = {}
    for row in history:
        status = row.get('status')
        status_counts[status] = status_counts.get(status, 0) + 1
    if history_summary.get('status_distribution') != status_counts:
        raise ValueError('synthetic history summary status distribution is stale')

    return {
        'service_policies': len(rows),
        'runtime_dispatch_policies': runtime_count,
        'synthetic_departments': len(synthetic_departments),
        'maintenance_workflows': len(maintenance_rows),
        'maintenance_taxonomy_families': len(families),
        'tour_destinations': len(tours.get('destinations', [])),
        'synthetic_tour_products': len(tour_products.get('products', [])),
        'provenance_sources': len(sources),
        'synthetic_assumptions': len(assumption_rows),
        'evaluation_scenarios': len(scenarios),
        'synthetic_history_rows': len(history),
        'minibar_items': len(minibar_items),
        'housekeeping_catalog_items': len(housekeeping.get('catalog', [])),
        'demo_departments': len(departments_payload.get('departments', [])),
        'demo_staffed_departments': len(staffing.get('departments', [])),
        'demo_room_inventory_units': len(room_inventory),
        'demo_pms_stays': len(pms_stays),
        'demo_suppliers': len(supplier_rows),
        'demo_restaurants': len(restaurant_rows),
        'demo_spa_slots': len(spa_ops.get('slots', [])),
        'demo_commercial_tour_slots': len(tour_inventory),
    }


if __name__ == '__main__':
    print(json.dumps({'status':'ok', **validate()}, sort_keys=True))
