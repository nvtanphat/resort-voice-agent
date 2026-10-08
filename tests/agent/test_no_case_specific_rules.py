"""Guards against case-specific understanding rules and test-set leakage.

Understanding must come from validated commands, reviewed examples and
embeddings.  These tests stop three regressions: copying evaluation phrasings
into training, growing the phrase lists in ``config/agent-domain.json``, and
re-introducing literal service/entity branches in ``src/``.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src" / "concierge_kiosk"
DATASETS = ROOT / "datasets"
DOMAIN_PROFILE = ROOT / "config" / "agent-domain.json"
BUDGET_PATH = ROOT / "tests" / "agent_domain_keyword_budget.json"
TRAINING_AGENT = DATASETS / "training" / "agent"
NEAR_DUPLICATE_JACCARD = 0.85

# Language resources, policy and safety stay in the profile.  Everything else
# that is a phrase list counts against the budget below.
KEEP_PREFIXES = (
    "schema_version", "profile_id", "domain_vocab", "languages", "services", "security",
    "tools", "ui", "emergency", "memory", "presentation", "voice.",
    "nlu.normalization", "nlu.numerals", "nlu.clock", "nlu.service_selector",
    "nlu.intent.emergency", "nlu.routing.static_text", "nlu.routing.affirm",
    "nlu.routing.deny", "nlu.routing.confirmation", "nlu.slots.slot_labels",
    "nlu.slots.time_patterns", "nlu.slots.room_patterns", "nlu.slots.party_size",
    "nlu.slots.quantity", "nlu.slots.relative_time", "nlu.slots.number",
    "rag.token_stopwords", "rag.tokenization", "preferences.max",
)


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _evaluation_utterances() -> set[str]:
    found: set[str] = set()
    for path in (DATASETS / "evaluation").rglob("*.jsonl"):
        if "simulation" in path.parts:
            continue
        for row in _jsonl(path):
            texts = [row[key] for key in ("utterance", "query") if isinstance(row.get(key), str)]
            for turn in row.get("turns") or []:
                if isinstance(turn, dict):
                    texts += [turn[key] for key in ("query", "utterance", "text") if isinstance(turn.get(key), str)]
            found.update(_norm(text) for text in texts)
    return found


def _strings(node, path: str = ""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item, path)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}" if path else key)


def _case_specific_strings() -> list[tuple[str, str]]:
    profile = json.loads(DOMAIN_PROFILE.read_text(encoding="utf-8"))
    return [(path, value) for path, value in _strings(profile) if not path.startswith(KEEP_PREFIXES)]


def _budget() -> dict[str, int]:
    return json.loads(BUDGET_PATH.read_text(encoding="utf-8"))


requires_datasets = pytest.mark.skipif(not TRAINING_AGENT.is_dir(), reason="datasets/ not present")


@requires_datasets
def test_training_does_not_copy_evaluation_utterances() -> None:
    evaluation = _evaluation_utterances()
    token_sets = [set(text.split()) for text in evaluation if len(text.split()) >= 4]
    leaks: list[str] = []
    for path in sorted(TRAINING_AGENT.glob("*.jsonl")):
        if path.name == "source_registry.jsonl":
            continue
        for row in _jsonl(path):
            utterance = _norm(str(row.get("utterance", "")))
            tokens = set(utterance.split())
            if utterance in evaluation or (len(tokens) >= 4 and any(
                    len(tokens & other) / len(tokens | other) >= NEAR_DUPLICATE_JACCARD
                    for other in token_sets)):
                leaks.append(f"{path.name}:{row.get('scenario_id')}: {row.get('utterance')}")
    assert not leaks, "training copies evaluation phrasing:\n" + "\n".join(leaks[:20])


def test_agent_domain_phrase_lists_only_shrink() -> None:
    count = len(_case_specific_strings())
    budget = _budget()["case_specific_strings"]
    assert count <= budget, (
        f"agent-domain.json phrase lists grew to {count} (budget {budget}); "
        "add reviewed training examples instead of phrases")


@requires_datasets
def test_agent_domain_does_not_hold_property_names() -> None:
    vocab = json.loads((ROOT / "releases" / "domain-vocab.json").read_text(encoding="utf-8"))
    names = {_norm(term) for item in [*vocab.get("entities", ()), *vocab.get("services", ())]
             for field in ("names", "aliases") for terms in (item.get(field) or {}).values()
             for term in terms if isinstance(term, str) and len(_norm(term)) > 2}
    overlap = sorted({value for _path, value in _case_specific_strings() if _norm(value) in names})
    budget = _budget()["property_name_overlap"]
    assert len(overlap) <= budget, (
        f"{len(overlap)} property names/aliases in agent-domain.json (budget {budget}): {overlap[:20]}")


def test_src_has_no_literal_service_or_entity_branches() -> None:
    from concierge_kiosk.domain.service_registry import SERVICE_DEFINITIONS

    codes = "|".join(re.escape(code) for code in SERVICE_DEFINITIONS)
    service_branch = re.compile(
        rf"(==|!=|\bin)\s*[\(\{{\[]?\s*['\"]({codes})['\"]|['\"]({codes})['\"]\s*(==|!=)")
    entity_branch = re.compile(r"entity_type['\"]?\]?\)?\s*(==|!=|\bin)\s*[\(\{\[]?['\"]")
    found = [f"{path.relative_to(SRC_ROOT)}:{number}: {line.strip()}"
             for path in SRC_ROOT.rglob("*.py")
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
             if service_branch.search(line) or entity_branch.search(line)]
    assert not found, "literal service/entity branch:\n" + "\n".join(found)
