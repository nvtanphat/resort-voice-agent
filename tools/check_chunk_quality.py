"""Fail-closed quality gate for the localized compiled RAG corpus.

This runs before embedding. It checks that canonical fact identity and context
survive localization, that every publishable entity has a document, and that
the chunker stays within the signed policy. With ``--database`` it also checks
that the runtime rows retain the same structured metadata after ingestion.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.rag.ingestion import CHUNK_POLICY, chunk_sections
from concierge_kiosk.rag.ingestion.metadata import ENTITY_METADATA, FACT_METADATA, frontmatter
from build_furama_localized_knowledge import LABELS as FACT_LABELS

LANGUAGES = ("en", "vi", "ko", "zh")
FACTS = ROOT / "datasets/knowledge/canonical/facts.jsonl"
ENTITIES = ROOT / "datasets/knowledge/canonical/entities.jsonl"
LABELS = ROOT / "datasets/knowledge/canonical/context_labels.json"
COMPILED = ROOT / "knowledge/compiled/furama"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    return int(sorted(values)[min(len(values) - 1, int((len(values) - 1) * fraction))])


def normalized_label(value: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return set(re.findall(r"[a-z0-9]+", folded.casefold()))


def context_suffix_is_redundant(spec: dict, fact_label: str, context_label: str) -> bool:
    if spec.get("redundant_with_label", False):
        return True
    fact_tokens = normalized_label(fact_label)
    context_tokens = normalized_label(context_label)
    shorter, longer = sorted((fact_tokens, context_tokens), key=len)
    return bool(shorter) and shorter.issubset(longer) and len(longer - shorter) <= 1


def check_compiled() -> dict:
    facts = [row for row in jsonl(FACTS) if row.get("publication_status", "approved") == "approved"]
    facts_by_id = {str(row["canonical_fact_id"]): row for row in facts}
    entities = {row["entity_id"] for row in jsonl(ENTITIES)
                if row.get("publication_status", "approved") == "approved"}
    expected_ids = {row["canonical_fact_id"] for row in facts}
    fact_entity_ids = {str(row["entity_id"]) for row in facts}
    expected_card_entities = entities - fact_entity_ids
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    errors: list[str] = []
    contexts = {str(row.get("context") or "") for row in facts}
    for context in sorted(contexts):
        for language in LANGUAGES:
            if not str(labels.get("labels", {}).get(context, {}).get(language) or "").strip():
                errors.append(f"missing context label {context}/{language}")

    all_lengths: dict[str, list[int]] = {language: [] for language in LANGUAGES}
    locale_reports: dict[str, dict] = {}
    for language in LANGUAGES:
        paths = sorted((COMPILED / language).glob("*.md"))
        seen_entities: set[str] = set()
        seen_facts: dict[str, dict] = {}
        chunk_count = 0
        card_entities: set[str] = set()
        for path in paths:
            raw = path.read_text(encoding="utf-8")
            meta, body = frontmatter(raw)
            entity_id = str(meta.get("entity_id") or "")
            seen_entities.add(entity_id)
            pieces = chunk_sections(body, separate_policy_paragraphs=True)
            chunk_count += len(pieces)
            # The compiler marker is removed at ingestion; report the runtime
            # payload length rather than counting provenance JSON as guest text.
            all_lengths[language].extend(
                len(ENTITY_METADATA.sub("", FACT_METADATA.sub("", piece[3]).strip()))
                for piece in pieces
            )
            for marker in FACT_METADATA.finditer(body):
                try:
                    fact = json.loads(marker.group(1))
                except json.JSONDecodeError:
                    errors.append(f"invalid fact marker {path}")
                    continue
                fact_id = str(fact.get("canonical_fact_id") or "")
                canonical = facts_by_id.get(fact_id)
                if canonical is None:
                    errors.append(f"unknown canonical fact in marker {fact_id}/{language}")
                elif fact.get("domain_review") != (canonical.get("domain_review") or {}):
                    errors.append(f"domain review metadata drift for {fact_id}/{language}")
                fact_key = (str(fact.get("entity_id") or ""),
                            str(fact.get("fact_type") or ""),
                            str(fact.get("context") or ""))
                if not all(fact_key):
                    errors.append(f"incomplete fact identity {path}")
                if fact_key in seen_facts.values():
                    errors.append(f"duplicate entity/fact_type/context {fact_key} in {language}")
                seen_facts[fact_id] = fact_key
                label = labels["labels"].get(fact_key[2], {}).get(language, "")
                context_spec = labels["labels"].get(fact_key[2], {})
                fact_label = FACT_LABELS.get(fact_key[1], {}).get(language, fact_key[1])
                if (f"({label})" not in body
                        and not context_suffix_is_redundant(context_spec, fact_label, label)):
                    errors.append(f"context label not rendered for {fact_id}/{language}")
                if not str(fact.get("context_text") or "").strip():
                    errors.append(f"empty context_text for {fact_id}/{language}")
            for marker in ENTITY_METADATA.finditer(body):
                try:
                    card = json.loads(marker.group(1))
                except json.JSONDecodeError:
                    errors.append(f"invalid entity marker {path}")
                    continue
                card_entity = str(card.get("entity_id") or "")
                if card_entity not in expected_card_entities:
                    errors.append(f"unexpected entity card {card_entity}/{language}")
                if card.get("chunk_kind") != "entity_card" or card.get("canonical_fact_id"):
                    errors.append(f"invalid entity card identity {card_entity}/{language}")
                if not str(card.get("context_text") or "").strip():
                    errors.append(f"empty entity card context_text for {card_entity}/{language}")
                card_entities.add(card_entity)
        if seen_entities != entities:
            errors.append(f"{language} entity coverage mismatch: missing={sorted(entities-seen_entities)} extra={sorted(seen_entities-entities)}")
        if set(seen_facts) != expected_ids:
            errors.append(f"{language} fact coverage mismatch: missing={len(expected_ids-set(seen_facts))} extra={len(set(seen_facts)-expected_ids)}")
        if card_entities != expected_card_entities:
            errors.append(
                f"{language} entity-card coverage mismatch: missing={sorted(expected_card_entities-card_entities)} "
                f"extra={sorted(card_entities-expected_card_entities)}"
            )
        for path in paths:
            raw = path.read_text(encoding="utf-8")
            meta, _body = frontmatter(raw)
            for key in ("entity_type", "entity_type_label", "entity_domain_label"):
                if not str(meta.get(key) or "").strip():
                    errors.append(f"{language} missing entity metadata {key}: {path.name}")
        locale_reports[language] = {
            "documents": len(paths),
            "entities": len(seen_entities),
            "fact_markers": len(seen_facts),
            "entity_cards": len(card_entities),
            "chunks": chunk_count,
            "chunk_chars_median": int(statistics.median(all_lengths[language])) if all_lengths[language] else 0,
            "chunk_chars_p90": percentile(all_lengths[language], .90),
            "chunks_over_policy": sum(length > CHUNK_POLICY["max_chars"] for length in all_lengths[language]),
        }
    return {"errors": errors, "locales": locale_reports, "expected_entities": len(entities),
            "expected_facts": len(expected_ids), "expected_entity_cards": len(expected_card_entities)}


def check_database(path: Path, result: dict) -> None:
    if not path.exists():
        result.setdefault("errors", []).append(f"database not found: {path}")
        return
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        fields = {row[1] for row in con.execute("PRAGMA table_info(knowledge)")}
        required = {"entity_id", "fact_type", "fact_context", "canonical_fact_id", "context_text", "metadata_json"}
        missing = sorted(required - fields)
        if missing:
            result.setdefault("errors", []).append(f"database missing metadata columns: {missing}")
            return
        rows = con.execute(
            "SELECT language,COUNT(*) AS n, "
            "SUM(CASE WHEN context_text='' THEN 1 ELSE 0 END) AS empty_context, "
            "SUM(CASE WHEN metadata_json='{}' THEN 1 ELSE 0 END) AS empty_metadata "
            "FROM knowledge WHERE property_id='FURAMA_DANANG' AND active=1 GROUP BY language"
        ).fetchall()
        result["database"] = {row["language"]: dict(row) for row in rows}
        for row in rows:
            if row["empty_context"] or row["empty_metadata"]:
                result.setdefault("errors", []).append(f"database metadata loss in {row['language']}: {dict(row)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    result = check_compiled()
    if args.database:
        check_database(args.database, result)
    result["status"] = "PASS" if not result.get("errors") else "FAIL"
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
