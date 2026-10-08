from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from concierge_kiosk.core.domain_profile import get_domain_profile
from concierge_kiosk.core.domain_vocab import entity_terms, service_terms


ROOT = Path(__file__).resolve().parents[2]


def test_domain_vocab_release_is_schema_valid_and_pinned():
    release = ROOT / "releases" / "domain-vocab.json"
    schema = json.loads((ROOT / "config" / "domain-vocab.schema.json").read_text(encoding="utf-8"))
    payload = json.loads(release.read_text(encoding="utf-8"))
    assert not list(Draft202012Validator(schema).iter_errors(payload))
    expected = get_domain_profile().domain_vocab
    assert expected["schema_version"] == 1
    assert hashlib.sha256(release.read_bytes()).hexdigest() == json.loads(
        (ROOT / "config" / "agent-domain.json").read_text(encoding="utf-8"))["domain_vocab"]["sha256"]


def test_runtime_reads_property_terms_from_the_release():
    assert "furama beach" in entity_terms("en", "recreation")
    assert "extra towels" in service_terms("en")
