"""Document domain classification and stable section identity."""
from __future__ import annotations
import hashlib
import json
import re
from concierge_kiosk.core.domain_profile import rag_policy, supported_languages

LANGUAGES = set(supported_languages())
_DOMAIN_POLICY = rag_policy().document_domains
_DEFAULT_DOMAIN = _DOMAIN_POLICY['default']
# The editorial `domain` is data; the profile owns no list of domain names.  Only
# the identifier shape (the same one the profile schema enforces) is checked.
_DOMAIN_ID = re.compile(r'^[a-z][a-z0-9_]{0,47}$')


def document_domain(meta: dict) -> str:
    """Use the editorial domain when it is a well-formed identifier, else the profile default."""
    domain = meta.get('domain')
    if isinstance(domain, str):
        normalized = domain.strip().replace('-', '_')
        if _DOMAIN_ID.fullmatch(normalized):
            return normalized
    return _DEFAULT_DOMAIN


def semantic_parent_id(property_id: str, language: str, source_id: str,
                       revision: str, section_id: str, section_ordinal: int) -> str:
    """Stable section parent identity independent of translated/display headings."""
    key = json.dumps([property_id, language, source_id, revision, section_id, section_ordinal],
                     ensure_ascii=False)
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]
