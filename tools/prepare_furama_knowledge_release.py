"""Prepare publishable Furama runtime Markdown for offline signing.

The signing tool accepts a flat directory. Runtime source is organized by locale,
so this command flattens it using the authenticated document_id + language pair.
No key material is handled here.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "knowledge/compiled/furama"


def _frontmatter(raw: str) -> dict:
    if not raw.startswith("---\n") or "\n---\n" not in raw:
        raise ValueError("Compiled knowledge document has invalid front matter")
    header = raw[4:].split("\n---\n", 1)[0]
    meta = yaml.safe_load(header)
    if not isinstance(meta, dict):
        raise ValueError("Compiled knowledge front matter must be an object")
    return meta


def prepare_release(output: Path, source: Path = DEFAULT_SOURCE) -> dict[str, object]:
    documents = sorted(source.rglob("*.md"))
    if not documents:
        raise ValueError(f"No compiled knowledge documents under {source}")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    seen: set[tuple[str, str]] = set()
    languages: dict[str, int] = {}
    for path in documents:
        raw = path.read_text(encoding="utf-8")
        meta = _frontmatter(raw)
        if meta.get("property_id") != "FURAMA_DANANG" or meta.get("classification") != "public":
            raise ValueError(f"Non-public or cross-property document cannot be prepared: {path}")
        doc_id = str(meta.get("document_id", ""))
        language = str(meta.get("language", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", doc_id) or language not in {"en", "vi", "ko", "zh"}:
            raise ValueError(f"Unsafe document identity in {path}")
        key = (doc_id, language)
        if key in seen:
            raise ValueError(f"Repeated document/language pair: {key}")
        seen.add(key)
        filename = f"{doc_id}__{language}.md"
        (output / filename).write_text(raw, encoding="utf-8", newline="\n")
        languages[language] = languages.get(language, 0) + 1

    if set(languages) != {"en", "vi", "ko", "zh"} or len(set(languages.values())) != 1 or len(seen) != sum(languages.values()):
        raise ValueError(f"Incomplete or unbalanced publishable locale snapshot: documents={len(seen)}, languages={languages}")
    if min(languages.values()) < 50:
        raise ValueError(f"Publishable locale snapshot is unexpectedly small: {languages}")
    return {"documents": len(seen), "languages": languages, "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Furama knowledge for offline operator signing")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    print(json.dumps(prepare_release(args.output, args.source), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
