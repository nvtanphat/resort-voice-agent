"""Audit and verify Furama dataset integration integrity."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from concierge_kiosk.core.dataset_layout import (
    ALIASES, CONTACTS, DEPARTMENTS, ENTITIES, FACTS, KNOWLEDGE_MANIFEST,
    MAP, MAP_PATHS, PLANNING, RELATIONS, SERVICE_CATALOG,
    dataset_path, dataset_root,
)

from tools.validate_furama_semantics import validate_dataset
from tools.validate_furama_schemas import validate as validate_schemas
from tools.validate_synthetic_operations import validate as validate_synthetic_operations
from tools.evaluation.validate_production_evaluation import validate as validate_production_evaluation
from tools.evaluation.validate_hospitality import validate as validate_hospitality


def audit_furama():
    print("=" * 60)
    print("AUDITING FURAMA DATASET & KNOWLEDGE BASE")
    print("=" * 60)

    # 1. Entities
    entities = []
    with open(dataset_path(ENTITIES), encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            if line.strip():
                entities.append(json.loads(line))
    entity_ids = {e["entity_id"] for e in entities}
    print(f"1. Entities: {len(entities)} (Unique IDs: {len(entity_ids)})")
    assert len(entities) == len(entity_ids), f"Duplicate entity IDs found: {len(entities)} vs {len(entity_ids)}"
    assert len(entities) >= 100, f"Unexpectedly small curated entity set: {len(entities)}"

    # 2. Facts
    facts = []
    with open(dataset_path(FACTS), encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            if line.strip():
                facts.append(json.loads(line))
    fact_ids = {f["canonical_fact_id"] for f in facts}
    print(f"2. Facts: {len(facts)} (Unique IDs: {len(fact_ids)})")
    assert len(facts) == len(fact_ids), f"Duplicate fact IDs found: {len(facts)} vs {len(fact_ids)}"
    assert len(facts) >= 250, f"Unexpectedly small curated fact set: {len(facts)}"

    # 3. Services
    with open(dataset_path(SERVICE_CATALOG), encoding="utf-8") as f:
        services = json.load(f)
    service_ids = {s["service_id"] for s in services}
    print(f"3. Services: {len(services)} (Unique IDs: {len(service_ids)})")
    assert len(services) == len(service_ids), f"Duplicate service IDs found: {len(services)} vs {len(service_ids)}"
    assert len(services) >= 18, f"Unexpectedly small service catalog: {len(services)}"

    # 4. Workflows
    workflows = []
    with open(dataset_path("synthetic/operations/workflows/guest_service_workflows.jsonl"), encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            if line.strip():
                workflows.append(json.loads(line))
    wf_ids = {w["workflow_id"] for w in workflows}
    print(f"4. Workflows: {len(workflows)} (Unique IDs: {len(wf_ids)})")
    assert len(workflows) == 11, f"Expected 11 workflows, got {len(workflows)}"
    assert len(wf_ids) == 11, f"Duplicate workflow IDs found: {len(workflows)} vs {len(wf_ids)}"

    # 5. RAG knowledge docs
    docs = list((ROOT / "knowledge" / "approved" / "furama").rglob("*.md"))
    print(f"5. RAG knowledge docs: {len(docs)}")
    assert len(docs) == len(entities), f"Approved source docs should cover every entity: docs={len(docs)}, entities={len(entities)}"

    # 6. Aliases
    with open(dataset_path(ALIASES), encoding="utf-8") as f:
        aliases_data = json.load(f)
    aliases_by_entity = aliases_data.get("aliases_by_entity", aliases_data)
    print(f"6. Aliases entities: {len(aliases_by_entity)}")
    assert set(aliases_by_entity) == entity_ids, "Alias coverage must exactly match entity IDs"

    # 7. Relations
    relations = []
    with open(dataset_path(RELATIONS), encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            if line.strip():
                relations.append(json.loads(line))
    print(f"7. Relations: {len(relations)}")
    assert len(relations) >= 250, f"Unexpectedly small relation graph: {len(relations)}"

    # 8. Contacts
    with open(dataset_path(CONTACTS), encoding="utf-8") as f:
        contacts = json.load(f)
    print(f"8. Contacts: {len(contacts)}")
    assert len(contacts) == 7, f"Expected 7 contacts, got {len(contacts)}"

    # 9. Departments
    with open(dataset_path(DEPARTMENTS), encoding="utf-8") as f:
        departments = json.load(f)
    print(f"9. Departments: {len(departments)}")
    assert len(departments) == 7, f"Expected 7 departments, got {len(departments)}"

    # 10. Map
    with open(dataset_path(MAP), encoding="utf-8") as f:
        map_data = json.load(f)
    zones = map_data.get("zones", [])
    loc_count = sum(len(z.get("locations", [])) for z in zones)
    print(f"10. Map zones: {len(zones)}, locations: {loc_count}")
    location_ids = [loc["entity_id"] for zone in zones for loc in zone.get("locations", [])]
    assert loc_count == len(set(location_ids)) and loc_count > 0, \
        f"Map locations must have unique non-empty IDs, got {loc_count}"

    # 11. Planning
    with open(dataset_path(PLANNING), encoding="utf-8") as f:
        planning_data = json.load(f)
    sched = planning_data.get("operating_schedule", [])
    slas = planning_data.get("lead_times_and_slas", [])
    total_planning = len(sched) + len(slas)
    print(f"11. Planning entries: {total_planning} (schedule: {len(sched)}, slas: {len(slas)})")
    assert len(sched) >= 20 and len(slas) >= 1, f"Unexpectedly small planning dataset: schedule={len(sched)}, slas={len(slas)}"

    synthetic = validate_synthetic_operations()
    print(f"12. Synthetic operational policies: {synthetic['service_policies']} "
          f"(runtime dispatch: {synthetic['runtime_dispatch_policies']}, maintenance workflows: {synthetic['maintenance_workflows']})")
    production_eval = validate_production_evaluation()
    print(f"12a. Production evaluation: {production_eval['scenarios']} scenarios, "
          f"{production_eval['journeys']} journeys, {production_eval['failure_cases']} failure cases, "
          f"{production_eval['simulation_events']} counterfactual events")
    hospitality_evaluation = validate_hospitality()
    print(f"12b. Hospitality evaluation: {hospitality_evaluation['scenarios']} scenarios, "
          f"{hospitality_evaluation['journeys']} journeys, {hospitality_evaluation['failure_cases']} failure cases, "
          f"{hospitality_evaluation['stays']} stays, {hospitality_evaluation['events']} guest/ops events")

    print("-" * 60)
    print("SCHEMA & REFERENCE / PROVENANCE CHECKS")
    print("-" * 60)

    # Check facts reference valid entities or have valid provenance
    orphan_facts = []
    missing_provenance = []
    for f in facts:
        eid = f.get("entity_id")
        if eid not in entity_ids:
            orphan_facts.append((f["canonical_fact_id"], eid))
        prov = f.get("provenance_sources", [])
        if not prov:
            missing_provenance.append(f["canonical_fact_id"])
    print(f"Orphan facts (referencing non-existent entity): {len(orphan_facts)}")
    assert len(orphan_facts) == 0, f"Found orphan facts: {orphan_facts}"
    print(f"Facts missing provenance: {len(missing_provenance)}")
    assert len(missing_provenance) == 0, f"Found facts missing provenance: {missing_provenance}"

    # Check services reference valid entities or departments
    dept_ids = {d["department_id"] for d in departments}
    orphan_services = []
    for s in services:
        dept = s.get("department_id")
        if dept and dept not in dept_ids:
            orphan_services.append((s["service_id"], dept))
    print(f"Services with unknown department: {len(orphan_services)}")
    assert len(orphan_services) == 0, f"Found orphan services: {orphan_services}"

    # Check workflows reference valid services and departments
    orphan_wf_services = []
    for w in workflows:
        sid = w.get("service_id")
        if sid not in service_ids:
            orphan_wf_services.append((w["workflow_id"], sid))
        did = w.get("department_id")
        if did and did not in dept_ids:
            orphan_wf_services.append((w["workflow_id"], did))
    print(f"Workflows with unknown service/dept: {len(orphan_wf_services)}")
    assert len(orphan_wf_services) == 0, f"Found orphan workflow references: {orphan_wf_services}"

    # Check relations reference known entities/departments/services/facts/zones
    zone_ids = {z["zone_id"] for z in zones}
    known_nodes = entity_ids | fact_ids | dept_ids | service_ids | zone_ids
    orphan_relations = []
    for r in relations:
        src = r.get("source_id")
        dst = r.get("target_id")
        if src not in known_nodes or dst not in known_nodes:
            orphan_relations.append((src, dst))
    print(f"Relations referencing unknown nodes: {len(orphan_relations)}")
    assert len(orphan_relations) == 0, f"Found orphan relations: {orphan_relations[:5]}"

    # Check aliases coverage
    missing_aliases = [eid for eid in entity_ids if eid not in aliases_by_entity]
    print(f"Entities missing aliases: {len(missing_aliases)}")
    assert len(missing_aliases) == 0, f"Missing aliases for: {missing_aliases}"

    # Check property_id consistency across dataset files
    property_ids = set()
    for f in facts:
        property_ids.add(f.get("property_id"))
    property_ids.add(map_data.get("property_id"))
    property_ids.add(planning_data.get("property_id"))
    with open(dataset_path(KNOWLEDGE_MANIFEST), encoding="utf-8") as f:
        manifest_data = json.load(f)
    property_ids.add(manifest_data.get("property_id"))
    print(f"Distinct property IDs across datasets: {property_ids}")
    assert property_ids == {"FURAMA_DANANG"}, f"Unexpected property IDs: {property_ids}"

    semantic = validate_dataset(dataset_root())
    print(f"12. Semantic fact validation: {semantic['facts_checked']} facts checked, 0 semantic errors")
    schema = validate_schemas()
    print(f"12b. JSON schema validation: {schema['facts']} facts, {schema['source_artifacts']} source artifacts, 0 errors")

    # Runtime compiler must exclude legacy/unverified entities instead of leaving
    # title-only ghost documents in the guest-facing RAG corpus.
    publishable_entities = {
        e["entity_id"] for e in entities
        if e.get("publication_status", "approved") == "approved"
    }
    runtime_entities = publishable_entities
    compiled_root = ROOT / "knowledge" / "compiled" / "furama"
    compiled = list(compiled_root.rglob("*.md"))
    print(f"13. Compiled runtime docs: {len(compiled)} ({len(runtime_entities)} entity cards/documents x 4 languages)")
    assert len(compiled) == len(runtime_entities) * 4
    for language in ("en", "vi", "ko", "zh"):
        assert len(list((compiled_root / language).glob("*.md"))) == len(runtime_entities)
    legacy_entities = entity_ids - runtime_entities
    for path in compiled:
        raw = path.read_text(encoding="utf-8")
        assert not any(f"entity_id: {eid}" in raw or f'entity_id: "{eid}"' in raw for eid in legacy_entities), \
            f"Legacy entity leaked into runtime corpus: {path}"

    # Dense/hybrid artifact verification: every active runtime chunk must carry a
    # vector produced by one pinned model; locale coverage must be symmetric.
    con = sqlite3.connect(ROOT / "data" / "concierge.sqlite3")
    try:
        total = con.execute("SELECT COUNT(*) FROM knowledge WHERE property_id=? AND active=1", ("FURAMA_DANANG",)).fetchone()[0]
        embedded = con.execute("SELECT COUNT(*) FROM knowledge WHERE property_id=? AND active=1 AND embedding IS NOT NULL", ("FURAMA_DANANG",)).fetchone()[0]
        models = {r[0] for r in con.execute("SELECT DISTINCT embedding_model FROM knowledge WHERE property_id=? AND active=1", ("FURAMA_DANANG",))}
        runtime_languages = dict(con.execute("SELECT language,COUNT(*) FROM knowledge WHERE property_id=? AND active=1 GROUP BY language", ("FURAMA_DANANG",)).fetchall())
        signed_release_rows = con.execute("SELECT COUNT(*) FROM knowledge_releases WHERE property_id=?", ("FURAMA_DANANG",)).fetchone()[0]
        signed_evidence_rows = con.execute("SELECT COUNT(*) FROM knowledge_release_evidence WHERE property_id=?", ("FURAMA_DANANG",)).fetchone()[0]
    finally:
        con.close()
    print(f"14. Runtime dense index: {embedded}/{total} embedded, models={sorted(models)}, languages={runtime_languages}")
    assert total > 1000 and total == embedded
    assert len(models) == 1
    embedding_model = next(iter(models))
    assert (embedding_model.startswith("hash-multilingual@sha256:") or
            embedding_model.startswith("multilingual-e5-small@sha256:") or
            (embedding_model.startswith("ollama:") and
             Path(f"models/embeddings/{embedding_model.removeprefix('ollama:')}.ollama.manifest.json").is_file())
            ), embedding_model
    assert len(set(runtime_languages.values())) == 1 and set(runtime_languages) == {"en", "vi", "ko", "zh"}
    # The runtime DB must identify the loaded release and retain its evidence.
    # The release evidence is generated from the verified source manifest; it is
    # not an operator approval signal.
    assert signed_release_rows > 0 and signed_evidence_rows > 0

    path_graph = json.loads(dataset_path(MAP_PATHS).read_text(encoding="utf-8"))
    paths = path_graph.get("paths", [])
    raw_location_ids = {loc["entity_id"] for zone in zones for loc in zone.get("locations", [])}
    raw_zone_ids = {zone["zone_id"] for zone in zones}
    allowed_nodes = raw_location_ids | raw_zone_ids
    print(f"15. Evidence-backed navigation paths: {len(paths)}")
    assert len(paths) >= 30
    precisions, source_kinds = set(), set()
    for route in paths:
        assert route.get("from_entity_id") in allowed_nodes and route.get("to_entity_id") in allowed_nodes
        assert route.get("precision") in {"floorplan", "zone"}
        precisions.add(route["precision"])
        evidence = route.get("evidence") or {}
        assert re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("evidence_sha256", "")))
        assert str(evidence.get("source_url", "")).startswith("https://")
        assert evidence.get("source_kind") in {"official_floorplan", "secondary_facility_map"}
        source_kinds.add(evidence["source_kind"])
        assert isinstance(evidence.get("limitations"), str) and len(evidence["limitations"]) >= 3
        assert type(evidence.get("source_page")) is int and evidence["source_page"] >= 1
    assert precisions == {"floorplan", "zone"}
    assert source_kinds == {"official_floorplan", "secondary_facility_map"}

    print("-" * 60)
    print("ALL STRUCTURAL + SEMANTIC + RUNTIME ARTIFACT AUDIT CHECKS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    audit_furama()
