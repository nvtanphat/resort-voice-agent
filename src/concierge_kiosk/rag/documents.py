"""Document domain classification and stable section identity."""
from __future__ import annotations
import hashlib
import json
from concierge_kiosk.core.domain_profile import rag_policy, supported_languages

LANGUAGES = set(supported_languages())
_DOMAIN_POLICY = rag_policy().document_domains
_DEFAULT_DOMAIN = _DOMAIN_POLICY['default']


def document_domain(meta: dict) -> str:
    """Prefer the editorial domain; legacy manuals get a deterministic label."""
    if 'domain' in meta:
        domain = meta['domain']
        if domain in DOMAINS:
            return domain
        normalized = domain.replace('-', '_').strip()
        if normalized in DOMAINS:
            return normalized
        return _DEFAULT_DOMAIN


def semantic_parent_id(property_id: str, language: str, source_id: str,
                       revision: str, section_id: str, section_ordinal: int) -> str:
    """Stable section parent identity independent of translated/display headings."""
    key = json.dumps([property_id, language, source_id, revision, section_id, section_ordinal],
                     ensure_ascii=False)
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]
