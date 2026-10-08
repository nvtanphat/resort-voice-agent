"""Atomic ingestion of one Markdown knowledge document."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date
from concierge_kiosk.persistence.sqlite_store import Store
from ..documents import LANGUAGES, document_domain, semantic_parent_id
from ..embedding.base import Embedder, valid_vector
from ..text.safety import PROMPT_INJECTION_PATTERN, unsafe_knowledge_text
from ..text.tokenization import search_index_text
from .chunking import canonicalize_markdown, chunk_sections
from .metadata import extract_doc_title, frontmatter, prepare_chunk


def ingest_text(store: Store, raw: str, *, property_id: str, embedder: Embedder | None = None,
                connection=None, target_language: str | None = None) -> int:
    """Atomic replace of old versions for the same document in one property.

    Use an independent signed, offline admin command; there is no public ingest API.
    """
    raw = canonicalize_markdown(raw)
    meta, body = frontmatter(raw)

    # If document defines multiple languages and no target is specified, ingest all
    doc_languages = meta.get("languages")
    if target_language is None and isinstance(doc_languages, (list, tuple)) and len(doc_languages) > 0:
        total = 0
        for lang in doc_languages:
            if lang in LANGUAGES:
                total += ingest_text(store, raw, property_id=property_id, embedder=embedder,
                                     connection=connection, target_language=lang)
        return total

    language = target_language or str(meta.get("language", ""))
    if language not in LANGUAGES:
        raise ValueError("Supported document language required")
    doc_id = str(meta.get("document_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,80}", doc_id):
        raise ValueError("Invalid document ID")
    if meta.get("property_id") != property_id:
        raise ValueError("Cannot ingest another property's documents")
    classification = meta.get("classification") or "public"
    if classification not in {"public", "staff_only"}:
        raise ValueError("Explicit classification required")
    effective = str(meta.get("effective_from") or "2026-01-01")
    starts = date.fromisoformat(effective)
    until = meta.get("effective_to")
    if until:
        if date.fromisoformat(str(until)) < starts:
            raise ValueError("Document effective_to precedes effective_from")
    
    full_title = extract_doc_title(meta, body)
    title = full_title[:160]
    domain = document_domain(meta)
    if not title or len(body.strip()) < 10:
        raise ValueError("Title and body required")
    # Editorial gate for an obvious class of prompt injection. This does not
    # replace staff approval/signing of the official knowledge package.
    if PROMPT_INJECTION_PATTERN.search(body):
        raise ValueError("Public document contains instruction-like untrusted content")
    if unsafe_knowledge_text(body) and classification == 'public':
        raise ValueError('Public document contains instruction-like untrusted content')
    revision = hashlib.sha256(raw.encode()).hexdigest()[:24]
    pieces = chunk_sections(body, separate_policy_paragraphs=True)
    prepared_pieces: list[tuple[str, str, int, str, dict, str, str, str | None]] = []
    for heading, section_id, section_ordinal, content in pieces:
        clean, metadata, context_text, chunk_effective, chunk_until = prepare_chunk(
            content, meta=meta, title=title, heading=heading, domain=domain)
        prepared_pieces.append((heading, section_id, section_ordinal, clean, metadata,
                                context_text, chunk_effective, chunk_until))
    # A parent is an approved Markdown heading/section, not a whole-document
    # grab bag. Keep clauses in their original order and child IDs stable.
    parent_bodies: dict[tuple[str, int, str], list[str]] = {}
    for heading, section_id, section_ordinal, content, _metadata, _context_text, _effective, _until in prepared_pieces:
        parent_bodies.setdefault((section_id, section_ordinal, heading), []).append(content)
    parents = [(semantic_parent_id(property_id, language, doc_id, revision, section_id, section_ordinal),
                revision, property_id, language, doc_id, title, heading, section_id, section_ordinal, domain,
                '\n\n'.join(parts), classification, effective,
                str(until) if until else None)
               for (section_id, section_ordinal, heading), parts in parent_bodies.items()]
    rows = []
    # Stable property-scoped IDs prevent collisions between hotel manual templates.
    property_namespace = hashlib.sha256(property_id.encode('utf-8')).hexdigest()[:16]
    expected_dimension = None
    extra_aliases = meta.get("search_aliases") or []
    if not isinstance(extra_aliases, list) or len(extra_aliases) > 30 or any(
            not isinstance(alias, str) or not 1 <= len(alias) <= 80 for alias in extra_aliases):
        raise ValueError("search_aliases must be a bounded string list")
    search_keywords = " ".join([full_title, *extra_aliases])
    for position, (heading, section_id, section_ordinal, content, metadata,
                   context_text, chunk_effective, chunk_until) in enumerate(prepared_pieces):
        key = f"{doc_id}:{language}:{position}:{property_namespace}"
        vector = ((getattr(embedder, "encode_passage", None) or embedder.encode)(
            f"{title} {heading} {context_text} {content} {search_keywords}") if embedder else None)
        if embedder is not None:
            if not valid_vector(vector) or (expected_dimension and len(vector) != expected_dimension):
                raise ValueError('Invalid embedding values or dimension; ingestion aborted')
            expected_dimension = len(vector)
        rows.append((key, property_id, language, title, heading, content,
                     search_index_text(f"{title} {heading} {context_text} {content} {search_keywords}", language), doc_id, revision,
                     classification, chunk_effective, chunk_until,
                     json.dumps(vector) if vector else None, embedder.model_name if embedder else None,
                     domain, semantic_parent_id(property_id, language, doc_id, revision, section_id, section_ordinal),
                     section_id, section_ordinal, str(metadata.get("entity_id") or meta.get("entity_id") or ""),
                     str(metadata.get("fact_type") or ""), str(metadata.get("context") or ""),
                     str(metadata.get("canonical_fact_id") or ""), context_text,
                     json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    # Catalog-only entity documents may intentionally contain no guest-visible
    # facts. They still participate in the signed bundle as metadata/frontmatter
    # and must clear any previous rows for the same source without creating a
    # synthetic retrieval chunk.
    def replace_in(con):
        old = con.execute("SELECT id,revision FROM knowledge WHERE property_id=? AND source=? AND language=?",
                          (property_id, doc_id, language)).fetchall()
        for existing in old:
            con.execute("DELETE FROM knowledge_fts WHERE doc_id=? AND revision=?",
                        (existing["id"], existing["revision"]))
        con.execute("DELETE FROM knowledge WHERE property_id=? AND source=? AND language=?",
                    (property_id, doc_id, language))
        con.execute("DELETE FROM knowledge_parents WHERE property_id=? AND source=? AND language=?",
                    (property_id, doc_id, language))
        for parent in parents:
            con.execute("INSERT INTO knowledge_parents(id,revision,property_id,language,source,title,heading,"
                        "section_id,section_ordinal,domain,body,classification,effective_from,effective_to) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", parent)
        for row in rows:
            con.execute("INSERT INTO knowledge(id,property_id,language,title,heading,body,search_text,source,"
                        "revision,classification,effective_from,effective_to,embedding,embedding_model,domain,parent_id,"
                        "section_id,section_ordinal,entity_id,fact_type,fact_context,canonical_fact_id,context_text,metadata_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            con.execute("INSERT INTO knowledge_fts(doc_id,revision,search_text) VALUES(?,?,?)",
                        (row[0], revision, row[6]))
    if connection is None:
        with store.connection(write=True) as con:
            replace_in(con)
    else:
        replace_in(connection)
    return len(rows)
