"""Validate checked-in Furama canonical facts and source-artifact registry."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import FACTS, SOURCES, dataset_path

DATA = dataset_path(FACTS).parent
SCHEMAS = ROOT / "datasets/schemas/knowledge"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_rows(rows: list[dict], validator: Draft202012Validator, label: str) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows, 1):
        for err in sorted(validator.iter_errors(row), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in err.path) or "<root>"
            errors.append(f"{label}:{index}:{location}: {err.message}")
    return errors


def validate(data_root: Path = DATA) -> dict[str, int]:
    facts = _load_jsonl(data_root / "facts.jsonl")
    # The canonical source registry is named ``verified_sources.jsonl`` in the
    # current layout.  Keep the optional colocated path for isolated callers,
    # but never guess the removed property-specific directory.
    artifact_path = data_root / "source-artifacts.jsonl"
    if not artifact_path.is_file():
        artifact_path = dataset_path(SOURCES)
    artifacts = _load_jsonl(artifact_path)
    errors = []
    errors.extend(_validate_rows(facts, _validator("fact.schema.json"), "facts"))
    errors.extend(_validate_rows(artifacts, _validator("source_artifact.schema.json"), "source-artifacts"))

    artifact_ids = {item["artifact_id"] for item in artifacts}
    for fact in facts:
        for source in fact.get("provenance_sources", []):
            document_id = source.get("document_id")
            if document_id not in artifact_ids:
                errors.append(f"{fact.get('canonical_fact_id')}: provenance document_id {document_id!r} missing from source-artifacts registry")
    if errors:
        raise ValueError("Schema validation failed:\n- " + "\n- ".join(errors[:100]))
    return {"facts": len(facts), "source_artifacts": len(artifacts), "schema_errors": 0}


if __name__ == "__main__":
    print(json.dumps({"status": "ok", **validate()}, sort_keys=True))
