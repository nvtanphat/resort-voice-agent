"""All-or-nothing signed knowledge-package ingestion."""
from __future__ import annotations
import re
from pathlib import Path
from concierge_kiosk.persistence.sqlite_store import Store
from ..documents import LANGUAGES
from ..embedding.base import Embedder
from .chunking import canonicalize_markdown, chunk_sections
from .document import ingest_text
from .metadata import frontmatter
from .policy import chunk_policy_hash


def ingest_bundle(store: Store, documents: dict[str, str], *, property_id: str,
                  embedder: Embedder | None = None,
                  release_version: int | None = None, bundle_sha256: str = '',
                  signed_chunk_policy_hash: str = '', effective_date: str | None = None,
                  replace_snapshot: bool = False) -> int:
    """All-or-nothing signed knowledge-package transaction, including FTS rows."""
    if not documents or len(documents) > 500:
        raise ValueError("Knowledge package must contain 1..500 documents")
    # Validate + embed in an isolated temporary DB before taking the production
    # write lock. A failure cannot partially replace already approved policies.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='concierge-knowledge-work-' ) as tmp:
        prepared = Store(Path(tmp) / 'work.sqlite3')
        keys = set()
        canonical_documents = {name: canonicalize_markdown(raw) for name, raw in documents.items()}
        multilingual: dict[str, list[tuple[str, str]]] = {}
        for filename, raw in sorted(canonical_documents.items()):
            meta, body = frontmatter(raw)
            multilingual.setdefault(str(meta.get('document_id', '')), []).append((str(meta.get('language', '')), body))
        for doc_id, variants in multilingual.items():
            if len({language for language, _body in variants}) > 1:
                for _language, body in variants:
                    sections = chunk_sections(body, separate_policy_paragraphs=True)
                    unique_sections = {(sid, ordinal) for _heading, sid, ordinal, _content in sections}
                    if len(unique_sections) > 1 and any(sid.startswith('legacy-section-') for sid, _ in unique_sections):
                        raise ValueError(f'Multilingual multi-section document {doc_id} requires explicit section IDs')
        for filename, raw in sorted(canonical_documents.items()):
            meta, _ = frontmatter(raw)
            key = (meta.get('document_id'), meta.get('language'))
            if key in keys:
                raise ValueError("Knowledge package repeats a document ID/language")
            keys.add(key)
            if len(raw.encode('utf-8')) > 1_000_000 or not filename.endswith('.md'):
                raise ValueError("Oversized or invalid knowledge document")
            ingest_text(prepared, raw, property_id=property_id, embedder=embedder)
        with prepared.connection() as src:
            rows = src.execute("SELECT id,property_id,language,title,heading,body,search_text,source,revision,"
                               "classification,effective_from,effective_to,active,embedding,embedding_model,domain,"
                               "parent_id,section_id,section_ordinal,entity_id,fact_type,fact_context,canonical_fact_id,"
                               "context_text,metadata_json FROM knowledge ORDER BY source,language,id").fetchall()
            staged_parents = src.execute("SELECT id,revision,property_id,language,source,title,heading,section_id,"
                                         "section_ordinal,domain,body,classification,effective_from,effective_to,active "
                                         "FROM knowledge_parents ORDER BY source,language,id").fetchall()
            if release_version is not None:
                if effective_date is None or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', effective_date):
                    raise ValueError('Signed snapshot requires an explicit property effective_date')
                effective_today = effective_date
                public_languages = {r['language'] for r in rows if r['classification'] == 'public'
                                    and r['active'] == 1 and r['effective_from'] <= effective_today
                                    and (r['effective_to'] is None or r['effective_to'] >= effective_today)}
                if not LANGUAGES.issubset(public_languages):
                    raise ValueError('Signed property snapshot must cover currently effective public documents in all four languages')
            with store.connection(write=True) as dest:
                if release_version is not None:
                    expected_policy_hash = chunk_policy_hash()
                    if (release_version < 1 or not re.fullmatch('[0-9a-f]{64}', bundle_sha256) or
                            signed_chunk_policy_hash != expected_policy_hash):
                        raise ValueError('Invalid signed knowledge release metadata or chunk policy')
                    current = dest.execute('SELECT release_version,chunk_policy_hash FROM knowledge_releases WHERE property_id=?',
                                           (property_id,)).fetchone()
                    if current and release_version <= current['release_version']:
                        raise ValueError('Knowledge release downgrade or replay denied')
                # A signed update is a complete hotel manual snapshot. The
                # offline rebuild tool may request the same atomic replacement
                # semantics without claiming a signed production release.
                if release_version is not None or replace_snapshot:
                    old = dest.execute('SELECT id,revision FROM knowledge WHERE property_id=?',
                                       (property_id,)).fetchall()
                    for item in old:
                        dest.execute('DELETE FROM knowledge_fts WHERE doc_id=? AND revision=?',
                                     (item['id'],item['revision']))
                    dest.execute('DELETE FROM knowledge WHERE property_id=?',(property_id,))
                    dest.execute('DELETE FROM knowledge_parents WHERE property_id=?',(property_id,))
                for doc_id, language in (() if (release_version is not None or replace_snapshot) else keys):
                    old = dest.execute("SELECT id,revision FROM knowledge WHERE property_id=? AND source=? AND language=?",
                                       (property_id, doc_id, language)).fetchall()
                    for item in old:
                        dest.execute("DELETE FROM knowledge_fts WHERE doc_id=? AND revision=?",
                                     (item['id'], item['revision']))
                    dest.execute("DELETE FROM knowledge WHERE property_id=? AND source=? AND language=?",
                                 (property_id, doc_id, language))
                    dest.execute("DELETE FROM knowledge_parents WHERE property_id=? AND source=? AND language=?",
                                 (property_id, doc_id, language))
                for parent in staged_parents:
                    dest.execute("INSERT INTO knowledge_parents(id,revision,property_id,language,source,title,heading,"
                                 "section_id,section_ordinal,domain,body,classification,effective_from,effective_to,active) "
                                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(parent))
                for row in rows:
                    dest.execute("INSERT INTO knowledge(id,property_id,language,title,heading,body,search_text,source,"
                                 "revision,classification,effective_from,effective_to,active,embedding,embedding_model,domain,parent_id,"
                                 "section_id,section_ordinal,entity_id,fact_type,fact_context,canonical_fact_id,context_text,metadata_json) "
                                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(row))
                    dest.execute("INSERT INTO knowledge_fts(doc_id,revision,search_text) VALUES(?,?,?)",
                                 (row['id'], row['revision'], row['search_text']))
                if release_version is not None:
                    import time
                    applied_at = int(time.time())
                    dest.execute('INSERT INTO knowledge_releases(property_id,release_version,bundle_sha256,chunk_policy_hash,applied_at) '
                                 'VALUES(?,?,?,?,?) ON CONFLICT(property_id) DO UPDATE SET '
                                 'release_version=excluded.release_version,bundle_sha256=excluded.bundle_sha256,'
                                 'chunk_policy_hash=excluded.chunk_policy_hash,applied_at=excluded.applied_at',
                                 (property_id, release_version, bundle_sha256, signed_chunk_policy_hash, applied_at))
                    public_languages = {r['language'] for r in rows if r['classification'] == 'public'
                                        and r['active'] == 1 and r['effective_from'] <= effective_today
                                        and (r['effective_to'] is None or r['effective_to'] >= effective_today)}
                    domains = {r['domain'] for r in rows}
                    embedding_model_id = embedder.model_name if embedder is not None else 'lexical-only'
                    dest.execute(
                        'INSERT INTO knowledge_release_evidence(property_id,release_version,bundle_sha256,'
                        'chunk_policy_hash,embedding_model_id,document_count,chunk_count,public_language_count,'
                        'domain_count,quality_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) '
                        'ON CONFLICT(property_id) DO UPDATE SET release_version=excluded.release_version,'
                        'bundle_sha256=excluded.bundle_sha256,chunk_policy_hash=excluded.chunk_policy_hash,'
                        'embedding_model_id=excluded.embedding_model_id,document_count=excluded.document_count,'
                        'chunk_count=excluded.chunk_count,public_language_count=excluded.public_language_count,'
                        'domain_count=excluded.domain_count,quality_status=excluded.quality_status,'
                        'created_at=excluded.created_at',
                        (property_id, release_version, bundle_sha256, signed_chunk_policy_hash,
                         embedding_model_id, len(canonical_documents), len(rows), len(public_languages),
                         len(domains), 'validated', applied_at),
                    )
        return len(rows)
