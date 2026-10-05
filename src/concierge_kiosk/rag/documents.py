"""Document domain classification and stable section identity."""
from __future__ import annotations
import hashlib
import json
from concierge_kiosk.core.domain_profile import rag_policy, supported_languages
from concierge_kiosk.core.domain_vocab import all_category_terms

LANGUAGES = set(supported_languages())
_DOMAIN_POLICY = rag_policy().document_domains
DOMAINS = set(_DOMAIN_POLICY['markers'])
_DOMAIN_MARKERS = {
    domain: tuple(dict.fromkeys((*markers, *all_category_terms(domain))))
    for domain, markers in _DOMAIN_POLICY['markers'].items()
}
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
    label = f"{meta.get('document_id', '')} {meta.get('title', '')}".casefold()
    return next((domain for domain, hints in _DOMAIN_MARKERS.items()
                 if any(marker in label for marker in hints)), _DEFAULT_DOMAIN)


def semantic_parent_id(property_id: str, language: str, source_id: str,
                       revision: str, section_id: str, section_ordinal: int) -> str:
    """Stable section parent identity independent of translated/display headings."""
    key = json.dumps([property_id, language, source_id, revision, section_id, section_ordinal],
                     ensure_ascii=False)
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]
