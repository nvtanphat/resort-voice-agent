"""Structured property-data loader for any concierge deployment dataset."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from concierge_kiosk.core.dataset_layout import (
    ALIASES,
    CONTACTS,
    DEPARTMENTS,
    ENTITIES,
    FACTS,
    KNOWLEDGE_MANIFEST,
    MAP,
    PLANNING,
    RELATIONS,
    SERVICE_CATALOG,
    WORKFLOWS,
    dataset_path,
)


@dataclass
class StructuredDataset:
    property_id: str
    dataset_dir: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    property_profile: dict[str, Any] = field(default_factory=dict)
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    facts: list[dict[str, Any]] = field(default_factory=list)
    facts_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    facts_by_entity: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    services: list[dict[str, Any]] = field(default_factory=list)
    services_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflows: list[dict[str, Any]] = field(default_factory=list)
    workflows_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    workflows_by_service: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    departments: dict[str, dict[str, Any]] = field(default_factory=dict)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    contacts_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    map_data: dict[str, Any] = field(default_factory=dict)
    planning_data: dict[str, Any] = field(default_factory=dict)
    aliases: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    entity_relations: list[dict[str, Any]] = field(default_factory=list)


def load_structured_dataset(dataset_dir: str | Path) -> StructuredDataset:
    """Load, validate and index all structured hotel datasets from directory."""
    path = Path(dataset_dir)
    if not path.is_dir():
        raise FileNotFoundError(f"Dataset directory not found: {dataset_dir}")

    def artifact(relative: str, legacy_name: str | None = None) -> Path:
        current = dataset_path(relative, path)
        if current.is_file() and not current.is_symlink():
            return current
        # Keep isolated callers that construct a small legacy fixture working;
        # the deployed dataset and all repository tools use the canonical path.
        legacy = path / legacy_name if legacy_name else Path()
        return legacy if legacy.is_file() and not legacy.is_symlink() else current

    # 1. Manifest
    manifest_file = artifact(KNOWLEDGE_MANIFEST, "manifest.json")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.is_file() else {}
    property_id = manifest.get("property_id")
    if not isinstance(property_id, str) or not property_id.strip():
        raise ValueError("Structured dataset manifest requires property_id")

    dataset = StructuredDataset(property_id=property_id, dataset_dir=path, manifest=manifest)

    # 2. Property profile
    profile_file = artifact("knowledge/canonical/property_profile.json", "property-profile.json")
    if profile_file.is_file():
        dataset.property_profile = json.loads(profile_file.read_text(encoding="utf-8"))
        profile_property_id = dataset.property_profile.get("property_id")
        if profile_property_id is not None and profile_property_id != property_id:
            raise ValueError("Structured dataset property profile does not match manifest property_id")

    # 3. Entities
    entities_file = artifact(ENTITIES, "entities.jsonl")
    if entities_file.is_file():
        with open(entities_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    dataset.entities[item["entity_id"]] = item

    # 4. Facts
    facts_file = artifact(FACTS, "facts.jsonl")
    if facts_file.is_file():
        with open(facts_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    dataset.facts.append(item)
                    dataset.facts_by_id[item["canonical_fact_id"]] = item
                    dataset.facts_by_entity.setdefault(item["entity_id"], []).append(item)

    # 5. Service Catalog
    services_file = artifact(SERVICE_CATALOG, "service-catalog.json")
    if services_file.is_file():
        dataset.services = json.loads(services_file.read_text(encoding="utf-8"))
        for item in dataset.services:
            dataset.services_by_id[item["service_id"]] = item

    # 6. Operational Workflows
    wf_file = artifact(WORKFLOWS, "operational-workflows.jsonl")
    if wf_file.is_file():
        with open(wf_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    dataset.workflows.append(item)
                    dataset.workflows_by_id[item["workflow_id"]] = item
                    dataset.workflows_by_service.setdefault(item.get("service_id", ""), []).append(item)

    # 7. Departments
    dept_file = artifact(DEPARTMENTS, "departments.json")
    if dept_file.is_file():
        for d in json.loads(dept_file.read_text(encoding="utf-8")):
            dataset.departments[d["department_id"]] = d

    # 8. Contacts
    contacts_file = artifact(CONTACTS, "contacts.json")
    if contacts_file.is_file():
        dataset.contacts = json.loads(contacts_file.read_text(encoding="utf-8"))
        for c in dataset.contacts:
            dataset.contacts_by_id[c["contact_id"]] = c

    # 9. Map
    map_file = artifact(MAP, "map.json")
    if map_file.is_file():
        dataset.map_data = json.loads(map_file.read_text(encoding="utf-8"))

    # 10. Planning
    planning_file = artifact(PLANNING, "planning.json")
    if planning_file.is_file():
        dataset.planning_data = json.loads(planning_file.read_text(encoding="utf-8"))

    # 11. Aliases
    aliases_file = artifact(ALIASES, "aliases.json")
    if aliases_file.is_file():
        aliases_raw = json.loads(aliases_file.read_text(encoding="utf-8"))
        dataset.aliases = aliases_raw.get("aliases_by_entity", aliases_raw)

    # 12. Relations
    rel_file = artifact(RELATIONS, "entity-relations.jsonl")
    if rel_file.is_file():
        with open(rel_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    dataset.entity_relations.append(json.loads(line))

    return dataset
