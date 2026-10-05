"""Versioned and atomic, semantically chunked hotel-manual ingestion."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date
from pathlib import Path
import yaml
from concierge_kiosk.persistence.sqlite_store import Store
from .common import (LANGUAGES, Embedder, document_domain, unsafe_knowledge_text,
                     semantic_parent_id, search_index_text, _valid_vector)
from .safety_patterns import PROMPT_INJECTION_PATTERN

CHUNK_POLICY = {
    "version": 2,
    "tokenizer": "regex-unicode",
    "max_tokens": 256,
    "max_chars": 900,
    "overlap_chars": 80,
    "parent_budget_chars": 2400,
}

_FACT_METADATA = re.compile(r"<!--\s*fact_metadata:\s*(\{.*?\})\s*-->\s*", re.S)
_ENTITY_METADATA = re.compile(r"<!--\s*entity_metadata:\s*(\{.*?\})\s*-->\s*", re.S)


def chunk_policy_hash(policy: dict | None = None) -> str:
    payload = json.dumps(policy or CHUNK_POLICY, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonicalize_markdown(raw: str) -> str:
    """Normalize decoded Markdown after package-byte authentication.

    Archive/signature integrity remains over the original bytes. Content identity,
    front matter and chunking use one canonical UTF-8 text representation so BOM
    and Windows CRLF do not create false revisions or ingestion failures.
    """
    if not isinstance(raw, str):
        raise TypeError("Markdown must be decoded text")
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    return raw.replace("\r\n", "\n").replace("\r", "\n")


_HEADING = re.compile(
    r"^\s*(#{1,6})\s+(.+?)(?:\s+\{#([A-Za-z][A-Za-z0-9_-]{1,79})\})?\s*$"
)


def chunk_sections(raw: str, *, max_chars: int = 900, overlap: int = 80,
                   max_tokens: int = 256,
                   separate_policy_paragraphs: bool = False) -> list[tuple[str, str, int, str]]:
    """Return ``(heading, section_id, section_ordinal, content)`` chunks.

    Repeated human-readable headings are safe because parent identity is based on
    a stable section ID/ordinal, never the heading text. Explicit IDs use Markdown
    headings such as ``## Opening hours {#opening-hours}``.
    """
    raw = canonicalize_markdown(raw)
    if max_chars < 200 or not 0 <= overlap < max_chars // 2 or max_tokens < 32:
        raise ValueError("Invalid chunk configuration")

    def token_count(value: str) -> int:
        return len(re.findall(r"\w+|[^\w\s]", value, flags=re.UNICODE))

    def fits(value: str) -> bool:
        return len(value) <= max_chars and token_count(value) <= max_tokens

    def split_long(paragraph: str):
        rest = paragraph
        while rest:
            # Provenance markers may be much longer than the guest-visible
            # clause (especially entity cards with several source fields). Size
            # the chunk on retrievable text while keeping the authenticated
            # marker intact for the ingestion boundary.
            guest_text = _ENTITY_METADATA.sub("", _FACT_METADATA.sub("", rest)).strip()
            if guest_text != rest.strip() and fits(guest_text):
                yield rest
                break
            if fits(rest):
                yield rest
                break
            high = min(len(rest), max_chars)
            while high > 1 and token_count(rest[:high]) > max_tokens:
                high -= 1
            if high < 1:
                high = 1
            match = list(re.finditer(r"[.!?。！？]\s+|\s+", rest[:high]))
            boundary = next((m.end() for m in reversed(match) if m.end() >= high // 2), high)
            part = rest[:boundary].strip()
            if not part:
                part, boundary = rest[:high].strip(), high
            yield part
            start = max(0, boundary - overlap)
            if start < boundary:
                while (start < boundary and start > 0 and
                       not rest[start - 1].isspace() and not rest[start].isspace()):
                    start += 1
            if start <= 0:
                start = boundary
            rest = rest[start:].strip()
            if not rest:
                break

    chunks: list[tuple[str, str, int, str]] = []
    hierarchy: list[tuple[int, str]] = []
    paragraphs: list[str] = []
    current_lines: list[str] = []
    section_id = "root"
    section_ordinal = 0
    next_ordinal = 1
    explicit_ids: set[str] = set()

    def heading() -> str:
        return " > ".join(label for _level, label in hierarchy)[-200:] or "General"

    def flush_paragraph() -> None:
        if current_lines:
            paragraphs.append(" ".join(current_lines).strip())
            current_lines.clear()

    def flush_section() -> None:
        flush_paragraph()
        value = ""
        for paragraph in paragraphs:
            for part in split_long(paragraph):
                combined = f"{value}\n\n{part}" if value else part
                if value and (separate_policy_paragraphs or not fits(combined)):
                    chunks.append((heading(), section_id, section_ordinal, value))
                    value = part
                else:
                    value = combined
        if value:
            chunks.append((heading(), section_id, section_ordinal, value))
        paragraphs.clear()

    for line in raw.splitlines():
        matched = _HEADING.match(line)
        if matched:
            flush_section()
            level = len(matched.group(1))
            label = matched.group(2).strip()[:150]
            explicit_id = matched.group(3)
            if explicit_id:
                if explicit_id.startswith("legacy-section-") or explicit_id == "root":
                    raise ValueError("Section ID uses a reserved value")
                if explicit_id in explicit_ids:
                    raise ValueError("Section IDs must be unique within a document")
                explicit_ids.add(explicit_id)
            hierarchy[:] = [(seen_level, seen_label) for seen_level, seen_label in hierarchy
                            if seen_level < level]
            hierarchy.append((level, label))
            section_ordinal = next_ordinal
            next_ordinal += 1
            section_id = explicit_id or f"legacy-section-{section_ordinal}"
        elif not line.strip():
            flush_paragraph()
        else:
            if re.match(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)', line) or re.match(
                    r'^\s*<!--\s*(?:fact|entity)_metadata:.*-->\s+[-*+]\s+', line):
                flush_paragraph()
            current_lines.append(line.strip())
    flush_section()
    return chunks


def chunk_markdown(raw: str, *, max_chars: int = 900, overlap: int = 80,
                   max_tokens: int = 256,
                   separate_policy_paragraphs: bool = False) -> list[tuple[str, str]]:
    """Compatibility view of chunking for callers that only need heading/body."""
    return [(heading, content) for heading, _sid, _ordinal, content in chunk_sections(
        raw, max_chars=max_chars, overlap=overlap, max_tokens=max_tokens,
        separate_policy_paragraphs=separate_policy_paragraphs)]

def _frontmatter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---\n"):
        raise ValueError("Markdown must start with YAML front matter")
    parts = raw.split("\n---\n", 1)
    if len(parts) != 2:
        raise ValueError("Front matter missing terminator")
    meta = yaml.safe_load(parts[0][4:])
    if not isinstance(meta, dict):
        raise ValueError("Invalid front matter")
    return meta, parts[1]


def _prepare_chunk(content: str, *, meta: dict, title: str, heading: str,
                   domain: str) -> tuple[str, dict, str, str | None, str | None]:
    """Remove compiler markers and return clean text plus fact identity.

    The marker is intentionally kept in the compiled source so the ingestion
    boundary can bind a chunk to canonical data without exposing internal IDs in
    citations or speech. A document-level entity card has no canonical fact ID.
    """
    fact_match = _FACT_METADATA.search(content)
    entity_match = _ENTITY_METADATA.search(content)
    if fact_match and entity_match:
        raise ValueError("Chunk cannot contain both fact and entity metadata")
    metadata: dict = {
        "entity_id": str(meta.get("entity_id") or ""),
        "entity_type": str(meta.get("entity_type") or ""),
        "entity_type_label": str(meta.get("entity_type_label") or ""),
        "entity_domain_label": str(meta.get("entity_domain_label") or ""),
        "entity_card": bool(meta.get("entity_card", False)) or "entity profile" in heading.casefold(),
    }
    if fact_match:
        try:
            marker = json.loads(fact_match.group(1))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid fact metadata marker") from exc
        if not isinstance(marker, dict):
            raise ValueError("Fact metadata marker must be an object")
        if marker.get("entity_id") != metadata["entity_id"]:
            raise ValueError("Fact metadata entity does not match document")
        required = ("canonical_fact_id", "fact_type", "context", "context_text", "effective_from")
        if any(not str(marker.get(key) or "").strip() for key in required):
            raise ValueError("Fact metadata marker is incomplete")
        metadata.update(marker)
        metadata["entity_card"] = False
        metadata["chunk_kind"] = "fact"
    elif entity_match:
        try:
            marker = json.loads(entity_match.group(1))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid entity metadata marker") from exc
        if not isinstance(marker, dict):
            raise ValueError("Entity metadata marker must be an object")
        if marker.get("entity_id") != metadata["entity_id"]:
            raise ValueError("Entity metadata entity does not match document")
        if marker.get("chunk_kind") != "entity_card" or marker.get("canonical_fact_id"):
            raise ValueError("Entity card metadata has an invalid chunk kind or fact identity")
        if not str(marker.get("context_text") or "").strip():
            raise ValueError("Entity card metadata is missing context_text")
        metadata.update(marker)
        metadata["entity_card"] = True
        metadata["chunk_kind"] = "entity_card"
    clean = _FACT_METADATA.sub("", content)
    clean = _ENTITY_METADATA.sub("", clean).strip()
    if not clean:
        raise ValueError("Chunk is empty after metadata removal")
    default_prefix = f"{title} · entity card · {domain}" if metadata.get("entity_card") else f"{title} · {domain}"
    context_text = str(metadata.get("context_text") or f"{default_prefix} · {clean}").strip()
    if not 1 <= len(context_text) <= 2000:
        raise ValueError("context_text must be 1..2000 characters")
    effective_from = str(metadata.get("effective_from") or meta.get("effective_from") or "2026-01-01")
    date.fromisoformat(effective_from)
    effective_to = metadata.get("effective_to")
    if effective_to:
        effective_to = str(effective_to)
        if date.fromisoformat(effective_to) < date.fromisoformat(effective_from):
            raise ValueError("Chunk effective_to precedes effective_from")
    metadata["heading"] = heading
    metadata["domain"] = domain
    return clean, metadata, context_text, effective_from, effective_to


def _extract_doc_titles(raw: str, meta: dict, body: str) -> dict[str, str]:
    """Extract canonical English title and localized titles from frontmatter or Markdown."""
    en_title = str(meta.get("title", "")).strip()
    if not en_title:
        h1_match = re.search(r"^#\s+(.+)$", body, re.M)
        if h1_match:
            en_title = h1_match.group(1).strip()
        else:
            en_title = str(meta.get("document_id", "Hotel Information"))
    loc_match = re.search(
        r"\*\*Localizations\*\*:\s*Vietnamese:\s*\*(.+?)\*\s*\|\s*Korean:\s*\*(.+?)\*\s*\|\s*Chinese:\s*\*(.+?)\*",
        body,
    )
    vi_name = loc_match.group(1).strip() if loc_match else ""
    ko_name = loc_match.group(2).strip() if loc_match else ""
    zh_name = loc_match.group(3).strip() if loc_match else ""

    titles = {
        "en": en_title,
        "vi": f"{vi_name} ({en_title})" if vi_name and vi_name != en_title else (vi_name or en_title),
        "ko": f"{ko_name} ({en_title})" if ko_name and ko_name != en_title else (ko_name or en_title),
        "zh": f"{zh_name} ({en_title})" if zh_name and zh_name != en_title else (zh_name or en_title),
        "_vi_name": vi_name,
        "_ko_name": ko_name,
        "_zh_name": zh_name,
        "_en_name": en_title,
    }
    return titles


def ingest_text(store: Store, raw: str, *, property_id: str, embedder: Embedder | None = None,
                connection=None, target_language: str | None = None) -> int:
    """Atomic replace of old versions for the same document in one property.

    Use an independent signed, offline admin command; there is no public ingest API.
    """
    raw = canonicalize_markdown(raw)
    meta, body = _frontmatter(raw)

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
    
    titles = _extract_doc_titles(raw, meta, body)
    title = titles.get(language, titles["en"])[:160]
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
        clean, metadata, context_text, chunk_effective, chunk_until = _prepare_chunk(
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
    search_keywords = " ".join([titles['_vi_name'], titles['_en_name'], titles['_ko_name'], titles['_zh_name'], *extra_aliases])
    for position, (heading, section_id, section_ordinal, content, metadata,
                   context_text, chunk_effective, chunk_until) in enumerate(prepared_pieces):
        key = f"{doc_id}:{language}:{position}:{property_namespace}"
        vector = ((getattr(embedder, "encode_passage", None) or embedder.encode)(
            f"{title} {heading} {context_text} {content} {search_keywords}") if embedder else None)
        if embedder is not None:
            if not _valid_vector(vector) or (expected_dimension and len(vector) != expected_dimension):
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
            meta, body = _frontmatter(raw)
            multilingual.setdefault(str(meta.get('document_id', '')), []).append((str(meta.get('language', '')), body))
        for doc_id, variants in multilingual.items():
            if len({language for language, _body in variants}) > 1:
                for _language, body in variants:
                    sections = chunk_sections(body, separate_policy_paragraphs=True)
                    unique_sections = {(sid, ordinal) for _heading, sid, ordinal, _content in sections}
                    if len(unique_sections) > 1 and any(sid.startswith('legacy-section-') for sid, _ in unique_sections):
                        raise ValueError(f'Multilingual multi-section document {doc_id} requires explicit section IDs')
        for filename, raw in sorted(canonical_documents.items()):
            meta, _ = _frontmatter(raw)
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
