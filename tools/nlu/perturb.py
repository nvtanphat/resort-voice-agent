"""Generate deterministic, label-preserving transcript perturbations.

The generator is intentionally model-free.  It creates a reproducible noisy
view of reviewed utterances so normalization and routing changes can be
measured without changing the gold labels or calling an external service.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import dataset_path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.agent.understanding.domain_nlu import FILLER_TERMS


_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _strip_marks(value: str) -> str:
    output: list[str] = []
    for char in unicodedata.normalize("NFD", value):
        if unicodedata.category(char) == "Mn":
            continue
        name = unicodedata.name(char, "")
        output.append("d" if name == "LATIN SMALL LETTER D WITH STROKE"
                      else "D" if name == "LATIN CAPITAL LETTER D WITH STROKE"
                      else char)
    return "".join(output)


def _replace_word(text: str, start: int, end: int, replacement: str) -> str:
    return text[:start] + replacement + text[end:]


def _diacritic_variant(text: str) -> str | None:
    value = _strip_marks(text)
    return value if value != text else None


def _character_variant(text: str, rng: random.Random, operation: str) -> str | None:
    matches = [match for match in _WORD.finditer(text) if len(match.group(0)) >= 5]
    if not matches:
        return None
    match = rng.choice(matches)
    word = match.group(0)
    if operation == "transpose":
        index = rng.randrange(1, len(word) - 1)
        changed = word[:index] + word[index + 1] + word[index] + word[index + 2:]
    elif operation == "delete":
        index = rng.randrange(1, len(word) - 1)
        changed = word[:index] + word[index + 1:]
    else:
        index = rng.randrange(len(word))
        changed = word[:index] + word[index] + word[index:]
    return _replace_word(text, match.start(), match.end(), changed)


def _filler_variant(text: str, language: str) -> str | None:
    fillers = tuple(FILLER_TERMS.get(language, ()))
    if not fillers:
        return None
    filler = fillers[0]
    return f"{filler} {text}"


def perturb_utterance(text: str, language: str, *, rng: random.Random) -> list[tuple[str, str]]:
    """Return unique ``(transform, utterance)`` pairs for one source utterance."""
    variants: list[tuple[str, str]] = []
    candidates = (
        ("strip_diacritics", _diacritic_variant(text)),
        ("repeat_letter", _character_variant(text, rng, "repeat")),
        ("transpose_letter", _character_variant(text, rng, "transpose")),
        ("delete_letter", _character_variant(text, rng, "delete")),
        ("filler_prefix", _filler_variant(text, language)),
    )
    seen = {text}
    for name, value in candidates:
        if value and value not in seen:
            variants.append((name, value))
            seen.add(value)
    return variants


def generate_variants(rows: Iterable[dict[str, Any]], *, per_case: int = 4, seed: int = 17,
                      languages: set[str] | None = None) -> list[dict[str, Any]]:
    """Create a stable perturbation set while retaining all gold metadata."""
    output: list[dict[str, Any]] = []
    for row in rows:
        language = str(row.get("language", ""))
        if languages and language not in languages:
            continue
        source = str(row.get("utterance", ""))
        rng = random.Random(f"{seed}:{row.get('scenario_id', '')}")
        for index, (transform, utterance) in enumerate(
                perturb_utterance(source, language, rng=rng)[:max(0, per_case)]):
            item = dict(row)
            item.update({
                "variant_id": f"{row.get('scenario_id', 'case')}:{index + 1}",
                "source_scenario_id": row.get("scenario_id"),
                "source_utterance": source,
                "transform": transform,
                "utterance": utterance,
            })
            output.append(item)
    return output


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path,
                        default=dataset_path("evaluation/holdout/service_workflow.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-case", type=int, default=4)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--language", action="append", dest="languages")
    args = parser.parse_args()
    rows = generate_variants(_read_jsonl(args.input), per_case=args.per_case, seed=args.seed,
                             languages=set(args.languages or ()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
