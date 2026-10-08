from __future__ import annotations

import pytest

from concierge_kiosk.core.settings import Settings
from concierge_kiosk.domain.requests.submissions import _resolve_declared_venue
from concierge_kiosk.domain.service_registry import service_definition


def test_client_venue_payload_is_canonicalized_from_property_dataset():
    definition = service_definition('dining_reservation')
    cfg = Settings(environment='test', structured_dataset_dir='datasets')
    payload = _resolve_declared_venue(
        {'restaurant_name': 'Don Cipriani', 'party_size': 2},
        language='vi', definition=definition, cfg=cfg)
    assert payload['restaurant_name'] == 'Nhà hàng Ý Don Cipriani'
    assert payload['party_size'] == 2


def test_client_venue_payload_rejects_unknown_property_entity():
    definition = service_definition('dining_reservation')
    cfg = Settings(environment='test', structured_dataset_dir='datasets')
    with pytest.raises(ValueError, match='venue'):
        _resolve_declared_venue(
            {'restaurant_name': 'Hoa Mai', 'party_size': 2},
            language='vi', definition=definition, cfg=cfg)
