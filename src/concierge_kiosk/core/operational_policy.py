"""Read-only operational routing/SLA policy for the active property prototype.

Verified/canonical hotel data remains in the configured structured dataset. Unpublished
internal routing/SLA assumptions live separately under
``datasets/synthetic/operations`` and are explicitly classified as
synthetic.  Runtime consumes only the small, validated dispatch subset and
fails closed when a record is malformed.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from concierge_kiosk.core.dataset_layout import (
    DEPARTMENTS,
    ESCALATION_MATRIX,
    PROPERTY,
    SERVICE_CATALOG,
    SERVICE_POLICIES,
    STAFF_DEPARTMENTS,
    WORKFLOWS,
    dataset_path,
    dataset_root,
)
from concierge_kiosk.domain.service_registry import service_code_for_catalog_id, service_definition


@dataclass(frozen=True)
class DispatchPolicy:
    department_id: str
    sla_minutes: int
    # The acknowledgement clock starts while a ticket is waiting for staff;
    # the service clock starts only after approval.  Keep both clocks in the
    # policy so callers cannot accidentally reuse the completion SLA.
    ack_minutes: int = 1
    escalation_after_minutes: int = 0
    priority: int = 3
    response_windows_local: tuple[dict, ...] = ()
    sla_clock_basis: str = 'wall_clock'
    dedupe_window_minutes: int = 0
    max_quantity: int | None = None


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _dataset_root(cfg) -> Path:
    configured = str(getattr(cfg, 'structured_dataset_dir', '') or '').strip()
    return dataset_root(configured or None)


def _workflow_path(cfg) -> Path:
    return dataset_path(WORKFLOWS, _dataset_root(cfg))


def _synthetic_policy_path(cfg) -> Path:
    return dataset_path(SERVICE_POLICIES, _dataset_root(cfg))


def _synthetic_departments_path(cfg) -> Path:
    return dataset_path(STAFF_DEPARTMENTS, _dataset_root(cfg))


def _escalation_matrix_path(cfg) -> Path:
    return dataset_path(ESCALATION_MATRIX, _dataset_root(cfg))


def _catalog_path(cfg) -> Path:
    return dataset_path(SERVICE_CATALOG, _dataset_root(cfg))


def service_catalog_entry(service_code: str, *, cfg=None) -> dict | None:
    """Return the canonical catalog row for a registry service.

    Catalog data is descriptive only: a missing/unknown price stays unknown and
    never becomes a guessed amount.  This helper deliberately returns a copy so
    callers cannot mutate the loaded source in-process.
    """
    cfg = cfg or _legacy_config()
    definition = service_definition(service_code)
    catalog_id = getattr(definition, 'catalog_service_id', '') if definition else ''
    if not catalog_id:
        return None
    path = _catalog_path(cfg)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        rows = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get('service_id') == catalog_id:
            return dict(row)
    return None


def service_code_for_anchor(anchor, *, cfg=None) -> str | None:
    """Map a verified conversation anchor to a catalog-backed service.

    The mapping is data-owned: catalog rows may declare ``anchor_focuses`` and
    the existing entity_id/service_id relationship is checked before a service
    can be selected. Ambiguous or missing mappings fail closed.
    """
    cfg = cfg or _legacy_config()
    path = _catalog_path(cfg)
    if anchor is None or not path.is_file() or path.is_symlink():
        return None
    try:
        rows = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(rows, list):
        return None
    focus = str(getattr(anchor, 'focus', '') or '').strip().casefold()
    source_id = str(getattr(anchor, 'source_id', '') or '').strip().casefold()
    labels = {
        str(getattr(anchor, field, '') or '').strip().casefold()
        for field in ('title', 'heading')
    }
    labels.discard('')
    matches: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        focuses = {str(item).strip().casefold() for item in row.get('anchor_focuses', ())
                   if isinstance(item, str) and item.strip()}
        entity_id = str(row.get('entity_id') or '').strip().casefold()
        service_id = str(row.get('service_id') or '').strip().casefold()
        names = {str(value).strip().casefold() for value in (row.get('names_by_locale') or {}).values()
                 if isinstance(value, str) and value.strip()}
        direct = bool(source_id and source_id in {entity_id, service_id})
        named = bool(labels & names)
        if (focus and focus in focuses) or direct or named:
            code = service_code_for_catalog_id(str(row.get('service_id') or ''))
            if code:
                matches.append(code)
    distinct = tuple(dict.fromkeys(matches))
    return distinct[0] if len(distinct) == 1 else None


def catalog_entry_for_details(details: str, *, language: str = 'en', cfg=None) -> dict | None:
    """Resolve an explicitly named catalog service using catalog vocabulary."""
    cfg = cfg or _legacy_config()
    path = _catalog_path(cfg)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        rows = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    text = ' '.join(str(details or '').casefold().split())
    if not text or not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        names = row.get('names_by_locale') if isinstance(row.get('names_by_locale'), dict) else {}
        candidates = [row.get('name'), names.get(language), names.get('en')]
        for candidate in candidates:
            phrase = ' '.join(str(candidate or '').casefold().split())
            tokens = [token for token in phrase.split() if len(token) >= 3]
            matched = sum(token in text for token in tokens)
            if phrase and (phrase in text or (len(tokens) >= 2 and matched >= min(2, len(tokens)))):
                return dict(row)
    return None


def service_window_state(service_code: str, *, cfg=None, now: int | None = None) -> dict:
    """Evaluate a catalog/policy window and return the next safe opening.

    A missing window is intentionally treated as ``unknown``/open for queueing;
    it is not converted into a made-up opening-hours claim.  Explicit windows
    are only used to delay the acknowledgement clock.
    """
    cfg = cfg or _legacy_config()
    catalog = service_catalog_entry(service_code, cfg=cfg) or {}
    raw = catalog.get('operating_hours')
    if not isinstance(raw, str) or not raw.strip():
        policy = dispatch_policy_for_service(service_code, cfg=cfg) if service_code else None
        windows = policy.response_windows_local if policy else ()
    else:
        windows = ({'start': raw.split('-', 1)[0].strip(), 'end': raw.split('-', 1)[1].strip()}
                   if '-' in raw else {})
        windows = (windows,) if windows else ()
    if not windows:
        return {'status': 'unknown', 'within_hours': True, 'next_open_at': None}
    timezone_name = str(getattr(cfg, 'property_timezone', '') or 'UTC')
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        timezone = ZoneInfo('UTC')
    timestamp = int(datetime.now(timezone).timestamp()) if now is None else int(now)
    local = datetime.fromtimestamp(timestamp, timezone)
    candidates: list[tuple[int, str]] = []
    within = False
    for offset in range(0, 2):
        day = (local + timedelta(days=offset)).date()
        for item in windows:
            if not isinstance(item, dict):
                continue
            try:
                start_h, start_m = (int(value) for value in str(item['start']).split(':', 1))
                end_h, end_m = (int(value) for value in str(item['end']).split(':', 1))
                start = datetime(day.year, day.month, day.day, start_h, start_m, tzinfo=timezone)
                if end_h == 24 and end_m == 0:
                    end = datetime(day.year, day.month, day.day, tzinfo=timezone) + timedelta(days=1)
                else:
                    end = datetime(day.year, day.month, day.day, end_h, end_m, tzinfo=timezone)
                if end <= start:
                    end += timedelta(days=1)
            except (KeyError, TypeError, ValueError):
                continue
            if start <= local <= end:
                within = True
            if start.timestamp() > timestamp:
                candidates.append((int(start.timestamp()), str(item.get('start'))))
    next_open = min(candidates)[0] if candidates else None
    return {'status': 'open' if within else 'closed', 'within_hours': within,
            'next_open_at': next_open}


def escalation_thresholds(department_id: str, *, cfg=None) -> tuple[float, float]:
    """Return configured level-1/level-2 SLA ratios, or fail closed."""
    cfg = cfg or _legacy_config()
    if not _unverified_operational_data_allowed():
        return (1.0, 0.0)
    path = _escalation_matrix_path(cfg)
    if not path.is_file() or path.is_symlink():
        return (1.0, 0.0)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return (1.0, 0.0)
    rows = payload.get('matrix', []) if isinstance(payload, dict) else []
    for row in rows:
        if not isinstance(row, dict) or row.get('department_id') != department_id:
            continue
        level1 = row.get('level1_after_sla_ratio')
        level2 = row.get('level2_after_sla_ratio')
        if (isinstance(level1, (int, float)) and not isinstance(level1, bool) and level1 >= 1
                and isinstance(level2, (int, float)) and not isinstance(level2, bool) and level2 > level1):
            return (float(level1), float(level2))
    return (1.0, 0.0)


def escalation_target(department_id: str, level: int, *, cfg=None) -> str:
    """Return the configured recipient for an escalation level.

    The matrix is operational configuration, not a role guess in Python.  An
    absent or production-unapproved matrix returns an empty target so callers
    can still record the escalation without inventing an owner.
    """
    cfg = cfg or _legacy_config()
    if level not in {1, 2} or not _unverified_operational_data_allowed():
        return ''
    path = _escalation_matrix_path(cfg)
    if not path.is_file() or path.is_symlink():
        return ''
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ''
    rows = payload.get('matrix', []) if isinstance(payload, dict) else []
    field = f'level{level}_role'
    for row in rows:
        if not isinstance(row, dict) or row.get('department_id') != department_id:
            continue
        target = row.get(field)
        return target.strip()[:120] if isinstance(target, str) and target.strip() else ''
    return ''


def _priority(value: object) -> int:
    # The numeric range is an operational enum, not a property fact.  Property
    # data controls the label; safety/emergency alerts remain outside this 1-5
    # service-request scale.
    return {'urgent': 1, 'high': 2, 'normal': 3, 'low': 4}.get(str(value).strip().lower(), 3)


def _windows(row: dict) -> tuple[dict, ...]:
    value = row.get('staff_response_windows_local')
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return ()
    clean = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get('start'), str) and isinstance(item.get('end'), str):
            clean.append({'start': item['start'], 'end': item['end']})
    return tuple(clean)


def _policy_from_row(row: dict, department: str, minutes: int, *, default_dedupe: int = 0,
                     default_max_quantity: int | None = None) -> DispatchPolicy:
    timing = row.get('timing') if isinstance(row.get('timing'), dict) else {}
    ack = timing.get('first_staff_response_target_min', 1)
    ack = ack if isinstance(ack, int) and not isinstance(ack, bool) and 1 <= ack <= minutes else min(minutes, 1)
    escalation = row.get('escalation_after_minutes', minutes)
    escalation = escalation if isinstance(escalation, int) and not isinstance(escalation, bool) and escalation >= 1 else minutes
    dedupe = row.get('dedupe_window_minutes', default_dedupe)
    dedupe = dedupe if isinstance(dedupe, int) and not isinstance(dedupe, bool) and 0 <= dedupe <= 1440 else default_dedupe
    max_quantity = row.get('max_quantity', default_max_quantity)
    max_quantity = max_quantity if isinstance(max_quantity, int) and not isinstance(max_quantity, bool) and 1 <= max_quantity <= 100 else default_max_quantity
    priority = row.get('priority')
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
        priority = _priority(row.get('priority_on_escalation'))
    return DispatchPolicy(
        department.strip(), minutes, ack_minutes=ack,
        escalation_after_minutes=escalation,
        priority=priority,
        response_windows_local=_windows(row),
        sla_clock_basis=str(row.get('sla_clock_basis') or 'wall_clock'),
        dedupe_window_minutes=dedupe,
        max_quantity=max_quantity,
    )


def _unverified_operational_data_allowed() -> bool:
    """Allow synthetic/unverified operating targets only outside production.

    Missing ``CONCIERGE_ENV`` is treated as production, matching
    :func:`concierge_kiosk.core.settings.load_settings`. This keeps prototype
    SLA/routing assumptions from becoming real guest commitments by accident.
    """
    return os.getenv('CONCIERGE_ENV', 'production').strip().lower() in {'test', 'development'}


def _known_department_ids(cfg) -> set[str]:
    ids: set[str] = set()
    canonical = dataset_path(DEPARTMENTS, _dataset_root(cfg))
    if canonical.is_file() and not canonical.is_symlink():
        payload = json.loads(canonical.read_text(encoding='utf-8'))
        if isinstance(payload, list):
            ids.update(
                item['department_id'].strip()
                for item in payload
                if isinstance(item, dict) and isinstance(item.get('department_id'), str)
                and item['department_id'].strip()
            )
    synthetic = _synthetic_departments_path(cfg)
    if synthetic.is_file() and not synthetic.is_symlink():
        payload = json.loads(synthetic.read_text(encoding='utf-8'))
        rows = payload.get('departments', []) if isinstance(payload, dict) else []
        ids.update(
            item['department_id'].strip()
            for item in rows
            if isinstance(item, dict) and isinstance(item.get('department_id'), str)
            and item['department_id'].strip()
        )
    return ids


def _synthetic_dispatch_policy(cfg, service_code: str) -> DispatchPolicy | None:
    if not _unverified_operational_data_allowed():
        return None
    path = _synthetic_policy_path(cfg)
    if not path.is_file() or path.is_symlink():
        return None
    payload = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(payload, dict) or payload.get('property_id') != cfg.property_id
            or payload.get('classification') != 'synthetic_operational'):
        return None
    rows = payload.get('services')
    if not isinstance(rows, list):
        return None
    known_departments = _known_department_ids(cfg)
    for row in rows:
        if not isinstance(row, dict) or row.get('service_code') != service_code:
            continue
        if row.get('runtime_dispatch_enabled') is not True:
            return None
        department = row.get('department_id')
        minutes = row.get('sla_minutes')
        if (not isinstance(department, str) or not department.strip()
                or department.strip() not in known_departments
                or isinstance(minutes, bool) or not isinstance(minutes, int)
                or not 1 <= minutes <= 720):
            return None
        limits = payload.get('quantity_limits') if isinstance(payload.get('quantity_limits'), dict) else {}
        catalog = service_catalog_entry(service_code, cfg=cfg) or {}
        catalog_limit = catalog.get('max_quantity')
        if not isinstance(catalog_limit, int) or isinstance(catalog_limit, bool):
            catalog_limit = None
        simulation_policy = payload.get('simulation_policy')
        simulation_policy = simulation_policy if isinstance(simulation_policy, dict) else {}
        return _policy_from_row(
            row, department, minutes,
            default_dedupe=int(simulation_policy.get('dedupe_window_minutes',
                                                       payload.get('dedupe_window_minutes', 0)) or 0),
            default_max_quantity=(limits.get(service_code)
                                  if isinstance(limits.get(service_code), int)
                                  else catalog_limit),
        )
    return None


def service_access_model(service_code: str, *, cfg=None) -> str | None:
    """How staff reach the guest for a service (``access_model`` in the synthetic policy)."""
    cfg = cfg or _legacy_config()
    path = _synthetic_policy_path(cfg)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (not isinstance(payload, dict) or payload.get('property_id') != getattr(cfg, 'property_id', None)
            or payload.get('classification') != 'synthetic_operational'
            or not isinstance(payload.get('services'), list)):
        return None
    for row in payload['services']:
        if isinstance(row, dict) and row.get('service_code') == service_code:
            value = row.get('access_model')
            return value if isinstance(value, str) else None
    return None


def _legacy_config():
    """Build a temporary config for old direct callers during migration."""
    root = _dataset_root(SimpleNamespace())
    property_id = ''
    profile = dataset_path(PROPERTY, root)
    if profile.is_file() and not profile.is_symlink():
        try:
            payload = json.loads(profile.read_text(encoding='utf-8'))
            property_id = str(payload.get('property_id') or '')
        except (OSError, ValueError, TypeError):
            pass
    return SimpleNamespace(structured_dataset_dir=str(root), property_id=property_id)


def dispatch_policy_for_service(service_code: str, *, cfg=None) -> DispatchPolicy | None:
    cfg = cfg or _legacy_config()
    definition = service_definition(service_code)
    if definition is None:
        return None

    # Prototype-only synthetic operational data is allowed only for services
    # the domain registry already authorizes for no-staff-approval execution.
    # This keeps the model/data layer from granting new business authority.
    if definition.approval == 'none':
        synthetic = _synthetic_dispatch_policy(cfg, service_code)
        if synthetic is not None:
            return synthetic

    sid = definition.catalog_service_id
    if not sid:
        return None
    path = _workflow_path(cfg)
    if not path.is_file() or path.is_symlink():
        return None
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get('service_id') != sid:
            continue
        # Canonical workflow files may still contain explicitly synthetic timing
        # estimates. Production must fail closed until an official SLA is signed
        # off, rather than silently turning those estimates into commitments.
        if row.get('timing_is_official_sla') is not True and not _unverified_operational_data_allowed():
            return None
        department = row.get('department_id')
        minutes = row.get('estimated_duration_min')
        if (not isinstance(department, str) or not department.strip() or isinstance(minutes, bool)
                or not isinstance(minutes, int) or not 1 <= minutes <= 720):
            return None
        catalog = service_catalog_entry(service_code, cfg=cfg) or {}
        catalog_limit = catalog.get('max_quantity')
        if not isinstance(catalog_limit, int) or isinstance(catalog_limit, bool):
            catalog_limit = None
        return _policy_from_row(row, department, minutes, default_max_quantity=catalog_limit)
    return None
