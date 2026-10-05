"""Canonical Markdown and semantic section chunking."""
from __future__ import annotations
import re
from .metadata import ENTITY_METADATA, FACT_METADATA


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
            guest_text = ENTITY_METADATA.sub("", FACT_METADATA.sub("", rest)).strip()
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
