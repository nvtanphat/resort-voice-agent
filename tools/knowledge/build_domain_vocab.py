"""Build a deterministic property vocabulary release from approved datasets.

The agent-domain profile owns language grammar and safety policy.  Property
names, aliases and service nouns belong to the property data release instead.
This builder deliberately does not invent aliases: every emitted term comes
from the entity, alias or service-catalog source files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import ALIASES, ENTITIES, SERVICE_CATALOG, dataset_path

DEFAULT_DATASET = dataset_path(ENTITIES).parent
DEFAULT_OUTPUT = ROOT / "releases" / "domain-vocab.json"
DEFAULT_PROPERTY_PROFILE = ROOT / "releases" / "property-profile.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_terms(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        term = " ".join(value.casefold().split())
        if term and term not in seen:
            seen.add(term)
            result.append(term)
    return sorted(result, key=lambda item: (len(item), item))


def _localized_terms(*values: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for language, terms in value.items():
            if not isinstance(language, str):
                continue
            result.setdefault(language, []).extend(
                terms if isinstance(terms, list) else [terms])
    return {language: _clean_terms(terms) for language, terms in sorted(result.items())
            if _clean_terms(terms)}


def build(dataset_dir: Path) -> dict[str, Any]:
    entities_path = dataset_dir / Path(ENTITIES).name
    aliases_path = dataset_dir / Path(ALIASES).name
    services_path = dataset_dir / Path(SERVICE_CATALOG).name
    entities = [json.loads(line) for line in entities_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    aliases_payload = _load_json(aliases_path)
    aliases_by_entity = aliases_payload.get("aliases_by_entity", {})
    catalog = _load_json(services_path)
    request_kinds: dict[str, str] = {}
    if DEFAULT_PROPERTY_PROFILE.is_file():
        profile = _load_json(DEFAULT_PROPERTY_PROFILE)
        request_kinds = {
            str(item["id"]): str(item["request_kind"])
            for item in profile.get("service_catalog", ())
            if isinstance(item, dict) and item.get("id") and item.get("request_kind")
        }
    entities_by_id = {item["entity_id"]: item for item in entities
                      if isinstance(item, dict) and isinstance(item.get("entity_id"), str)}

    emitted_entities: list[dict[str, Any]] = []
    categories: dict[str, list[str]] = {}
    for item in sorted(entities, key=lambda value: str(value.get("entity_id", ""))):
        entity_id = item.get("entity_id")
        category = item.get("domain") or item.get("entity_type")
        if not isinstance(entity_id, str) or not isinstance(category, str):
            continue
        names = _localized_terms({language: [name] for language, name in
                                  (item.get("names_by_locale") or {}).items()},
                                 {language: [item.get("name")] for language in
                                  (item.get("names_by_locale") or {})})
        aliases = _localized_terms(aliases_by_entity.get(entity_id, {}))
        emitted_entities.append({"id": entity_id, "category": category,
                                 "names": names, "aliases": aliases})
        categories.setdefault(category, []).append(entity_id)

    emitted_services: list[dict[str, Any]] = []
    for item in sorted(catalog, key=lambda value: str(value.get("service_id", ""))):
        service_id = item.get("service_id")
        entity_id = item.get("entity_id")
        if not isinstance(service_id, str) or not isinstance(entity_id, str):
            continue
        entity = entities_by_id.get(entity_id, {})
        names = _localized_terms(item.get("names_by_locale", {}),
                                 entity.get("names_by_locale", {}))
        aliases = _localized_terms(aliases_by_entity.get(entity_id, {}),
                                   aliases_by_entity.get(service_id, {}))
        emitted_services.append({
            "id": service_id,
            "code": service_id,
            "entity_id": entity_id,
            "category": item.get("category", entity.get("domain", "general")),
            "department": item.get("department_id"),
            "request_kind": request_kinds.get(service_id),
            "names": names,
            "aliases": aliases,
        })

    source_files = {name: _sha256(dataset_dir / name)
                    for name in ("entities.jsonl", "aliases.json", "service_catalog.json")}
    return {
        "schema_version": 1,
        "property_id": str(aliases_payload.get("property_id", "")),
        "languages": sorted({language for item in emitted_entities + emitted_services
                              for field in ("names", "aliases")
                              for language in item[field]}),
        "source_files": source_files,
        "entities": emitted_entities,
        "services": emitted_services,
        "categories": {key: sorted(value) for key, value in sorted(categories.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build(args.dataset_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="")
    print(f"wrote {args.output.relative_to(ROOT)}: {len(payload['entities'])} entities, "
          f"{len(payload['services'])} services")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
