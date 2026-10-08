"""Front matter, compiler markers and document titles for ingestion."""
from __future__ import annotations
import json
import re
from datetime import date
import yaml

FACT_METADATA = re.compile(r"<!--\s*fact_metadata:\s*(\{.*?\})\s*-->\s*", re.S)
ENTITY_METADATA = re.compile(r"<!--\s*entity_metadata:\s*(\{.*?\})\s*-->\s*", re.S)


def frontmatter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---\n"):
        raise ValueError("Markdown must start with YAML front matter")
    parts = raw.split("\n---\n", 1)
    if len(parts) != 2:
        raise ValueError("Front matter missing terminator")
    meta = yaml.safe_load(parts[0][4:])
    if not isinstance(meta, dict):
        raise ValueError("Invalid front matter")
    return meta, parts[1]


def prepare_chunk(content: str, *, meta: dict, title: str, heading: str,
                   domain: str) -> tuple[str, dict, str, str | None, str | None]:
    """Remove compiler markers and return clean text plus fact identity.

    The marker is intentionally kept in the compiled source so the ingestion
    boundary can bind a chunk to canonical data without exposing internal IDs in
    citations or speech. A document-level entity card has no canonical fact ID.
    """
    fact_match = FACT_METADATA.search(content)
    entity_match = ENTITY_METADATA.search(content)
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
    clean = FACT_METADATA.sub("", content)
    clean = ENTITY_METADATA.sub("", clean).strip()
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


def extract_doc_title(meta: dict, body: str) -> str:
    """The document title: front-matter ``title``, else the first H1, else its id.

    Each compiled document is already in its own language, so its title needs
    no per-language variants.
    """
    title = str(meta.get("title", "")).strip()
    if title:
        return title
    h1_match = re.search(r"^#\s+(.+)$", body, re.M)
    if h1_match:
        return h1_match.group(1).strip()
    return str(meta.get("document_id", "Hotel Information"))
