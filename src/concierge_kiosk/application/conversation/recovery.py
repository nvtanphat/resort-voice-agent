"""No-evidence recovery without inventing hotel facts or forcing a staff handoff."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from concierge_kiosk.core.dataset_layout import (
    CONTACTS,
    DEPARTMENTS,
    KNOWLEDGE_MANIFEST,
    SERVICE_CATALOG,
    canonical_text_bytes,
    dataset_path,
)
from concierge_kiosk.rag.text.normalize import fold_accents
from concierge_kiosk.rag.text.tokenization import tokens
from concierge_kiosk.rag.grounding.relevance import fts_query


@dataclass(frozen=True)
class SupportDirectory:
    property_id: str
    services: tuple[dict[str, Any], ...]
    departments: dict[str, dict[str, Any]]
    contacts: dict[str, dict[str, Any]]


def _verified_json(root: Path, manifest: dict[str, Any], relative: str,
                   legacy_name: str | None = None) -> Any:
    artifacts = manifest.get('artifacts', {})
    name = relative if relative in artifacts else (legacy_name or relative)
    artifact = artifacts.get(name)
    path = dataset_path(relative, root) if name == relative else root / name
    if (not isinstance(artifact, dict) or not isinstance(artifact.get('sha256'), str)
            or path.is_symlink() or not path.is_file()):
        raise ValueError(f'Unpinned structured artifact: {name}')
    raw = canonical_text_bytes(path)
    if len(raw) > 512_000 or hashlib.sha256(raw).hexdigest() != artifact['sha256']:
        raise ValueError(f'Structured artifact integrity mismatch: {name}')
    return json.loads(raw.decode('utf-8'))


def load_support_directory(dataset_dir: str, property_id: str) -> SupportDirectory | None:
    """Load only manifest-pinned operational directory data.

    This data is advisory UI metadata. It never authorizes a business write and
    is intentionally unavailable when the operator has not configured a pinned
    structured dataset directory.
    """
    if not dataset_dir:
        return None
    root = Path(dataset_dir)
    manifest_path = dataset_path(KNOWLEDGE_MANIFEST, root)
    if not manifest_path.is_file() or manifest_path.is_symlink():
        manifest_path = root / 'manifest.json'
    if root.is_symlink() or not root.is_dir() or manifest_path.is_symlink() or not manifest_path.is_file():
        return None
    try:
        raw_manifest = manifest_path.read_bytes()
        if len(raw_manifest) > 128_000:
            return None
        manifest = json.loads(raw_manifest.decode('utf-8'))
        if manifest.get('property_id') != property_id:
            return None
        services = _verified_json(root, manifest, SERVICE_CATALOG, 'service-catalog.json')
        departments = _verified_json(root, manifest, DEPARTMENTS, 'departments.json')
        contacts = _verified_json(root, manifest, CONTACTS, 'contacts.json')
        if not isinstance(services, list) or not isinstance(departments, list) or not isinstance(contacts, list):
            return None
        return SupportDirectory(
            property_id=property_id,
            services=tuple(item for item in services if isinstance(item, dict)),
            departments={item['department_id']: item for item in departments
                         if isinstance(item, dict) and isinstance(item.get('department_id'), str)},
            contacts={item['contact_id']: item for item in contacts
                      if isinstance(item, dict) and isinstance(item.get('contact_id'), str)},
        )
    except (OSError, ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _query_terms(text: str) -> set[str]:
    return {fold_accents(item) for item in tokens(text, limit=None, stem=True) if len(item) > 1}


def _text_terms(value: Any) -> set[str]:
    if isinstance(value, dict):
        text = ' '.join(str(v) for v in value.values() if isinstance(v, str))
    else:
        text = str(value or '')
    return _query_terms(text)


def _contact_payload(item: dict[str, Any], *, label: str, department: str,
                     extension: str | None = None) -> dict[str, Any] | None:
    phones = [str(v).strip() for v in item.get('phones', []) if isinstance(v, str) and v.strip()]
    extensions = [str(v).strip() for v in item.get('internal_extensions', [])
                  if isinstance(v, str) and v.strip()]
    if extension and extension not in extensions:
        extensions.insert(0, extension)
    email = item.get('email') if isinstance(item.get('email'), str) else None
    if not phones and not extensions and not email:
        return None
    return {
        'label': label,
        'department': department,
        'phones': phones[:2],
        'extensions': extensions[:2],
        'email': email,
        'verified_at': item.get('verified_at') if isinstance(item.get('verified_at'), str) else None,
        'grounding': 'manifest_pinned_directory',
    }


def support_contact(directory: SupportDirectory | None, query: str, language: str) -> dict[str, Any] | None:
    """Resolve a best-effort department contact from pinned structured data.

    The resolver only returns a card when at least one meaningful query token
    overlaps a localized service name/category/description or a department
    responsibility. A weak/no-match query gets no contact card rather than the
    generic front desk by default.
    """
    if directory is None:
        return None
    q = _query_terms(query)
    if not q:
        return None
    best_service: tuple[int, dict[str, Any]] | None = None
    for service in directory.services:
        localized = service.get('names_by_locale', {}).get(language, '') if isinstance(service.get('names_by_locale'), dict) else ''
        searchable = ' '.join((str(service.get('name', '')), str(localized),
                               str(service.get('category', '')), str(service.get('service_id', ''))))
        overlap = len(q & _text_terms(searchable))
        if overlap and (best_service is None or overlap > best_service[0]):
            best_service = (overlap, service)
    department = None
    service = best_service[1] if best_service is not None else None
    if service is not None:
        department = directory.departments.get(str(service.get('department_id', '')))
    if department is None:
        best_department: tuple[int, dict[str, Any]] | None = None
        for candidate in directory.departments.values():
            text = ' '.join((str(candidate.get('name', '')), str(candidate.get('code', '')),
                             ' '.join(str(v) for v in candidate.get('responsibilities', []) if isinstance(v, str))))
            overlap = len(q & _text_terms(text))
            if overlap and (best_department is None or overlap > best_department[0]):
                best_department = (overlap, candidate)
        department = best_department[1] if best_department is not None else None
    if department is None:
        return None

    department_id = str(department.get('department_id', ''))
    extension = None
    if service is not None and service.get('contact_extension') is not None:
        extension = str(service['contact_extension']).strip() or None
    if extension is None and department.get('internal_extension') is not None:
        extension = str(department['internal_extension']).strip() or None

    # Prefer a contact whose localized description/title overlaps the resolved
    # department. The fallback contact identity is data-owned by the directory
    # identities while all returned values still come from checksum-pinned data.
    dept_terms = _text_terms(' '.join((str(department.get('name', '')), department_id)))
    ranked: list[tuple[int, dict[str, Any]]] = []
    for contact in directory.contacts.values():
        text = ' '.join((str(contact.get('title', '')),
                         ' '.join(str(v) for v in (contact.get('languages') or {}).values()
                                  if isinstance(v, str))))
        ranked.append((len(dept_terms & _text_terms(text)), contact))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    contact = ranked[0][1] if ranked and ranked[0][0] >= 2 else None
    fallback_contact_id = department.get('fallback_contact_id')
    contact = contact or directory.contacts.get(str(fallback_contact_id or ''))
    if contact is None:
        # A department extension is still useful even when no external contact
        # record exists, but never invent a phone/email.
        if not extension:
            return None
        return {
            'label': str(department.get('name') or department_id),
            'department': department_id,
            'phones': [], 'extensions': [extension], 'email': None,
            'verified_at': service.get('verified_at') if service is not None else None,
            'grounding': 'manifest_pinned_directory',
        }
    return _contact_payload(contact, label=str(department.get('name') or contact.get('title') or department_id),
                            department=department_id, extension=extension)


def related_topics(store, *, property_id: str, language: str, query: str,
                   effective_date: str, limit: int = 3) -> list[dict[str, str]]:
    """Return current approved document titles as navigation suggestions only."""
    expression = fts_query(query, language)
    if not expression:
        return []
    where = ("k.property_id=? AND k.language=? AND k.classification='public' AND k.active=1 "
             "AND k.effective_from<=? AND (k.effective_to IS NULL OR k.effective_to>=?)")
    with store.connection() as con:
        rows = con.execute(
            "SELECT k.title,k.domain,k.language,bm25(knowledge_fts) AS score FROM knowledge_fts "  # nosec B608  # constant SQL fragments; all values are bound parameters
            "JOIN knowledge k ON k.id=knowledge_fts.doc_id AND k.revision=knowledge_fts.revision "
            f"WHERE knowledge_fts MATCH ? AND {where} ORDER BY score LIMIT 12",
            (expression, property_id, language, effective_date, effective_date),
        ).fetchall()
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        title = str(row['title']).strip()
        key = title.casefold()
        if not title or key in seen:
            continue
        seen.add(key)
        result.append({'label': title, 'query': title, 'domain': str(row['domain']),
                       'language': str(row['language'])})
        if len(result) >= limit:
            break
    return result


def recovery_metadata(store, directory: SupportDirectory | None, *, property_id: str,
                      language: str, query: str, effective_date: str,
                      retrieval_mode: str) -> dict[str, Any]:
    topics = related_topics(store, property_id=property_id, language=language, query=query,
                            effective_date=effective_date)
    contact = support_contact(directory, query, language)
    # Conflict/revocation merits staff review. A routine miss does not.
    handoff = retrieval_mode in {'conflict_abstention', 'stale_evidence_abstention', 'contextual_revoked'}
    return {'related_topics': topics, 'support_contact': contact,
            'handoff_recommended': handoff}
