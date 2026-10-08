# Furama Concierge Dataset — FINAL

Production-prototype dataset for a single-property concierge/voice-agent system scoped to **Furama Resort Danang**.

## Runtime boundaries

- **Canonical RAG truth:** `knowledge/canonical/facts.jsonl` only.
- **Operational policies:** `synthetic/operations/` contains product-authored workflow policies and synthetic fixtures. They may drive prototype workflows but must not be stated as official Furama facts.
- **Dynamic/secret data:** credentials, live inventory, current supplier availability and sensitive room-access decisions require an authorized tool or staff-confirmation path.
- **Quarantine:** `quarantine/` is never indexed or used as live truth.
- **Training/evaluation:** never index `training/` or `evaluation/` into production RAG.

## Directory roles

- `knowledge/` — verified Furama knowledge, provenance, freshness, entities and map.
- `training/` — supervised Agent and RAG training data.
- `evaluation/` — isolated benchmark suites and final quality audit.
- `synthetic/` — product-authored operational policies plus privacy-safe simulation fixtures.
- `quarantine/` — stale, rejected, superseded or unverified material.
- `schemas/` — JSON Schema contracts and referential rules.

Release: `1.0.3-data-consistency-fix` · Reviewed: `2026-10-05`


## Production hardening in v1.0.1

- All non-schema JSON/JSONL artifacts are now classified by a strict JSON Schema contract or deterministic recursive JSON-shape contract; `schemas/validate_contracts.py` fails on unclassified files or shape drift.
- `knowledge/canonical/map.json` and `planning.json` now validate against schemas that match their runtime structures.
- `knowledge/sources/verification_snapshots.jsonl` freezes the exact evidence bundles used by canonical facts and binds them with SHA-256. Historical raw HTML/PDF was not retained in v1.0.0 and is **not fabricated**; raw archiving is mandatory on the next source refresh.
- Every canonical fact has `domain_review`. Sensitive policy/safety facts are `pending` and require staff confirmation until an authorized role approves them; no human approval is invented.
- Voice/audio production validation remains intentionally out of scope for this release.

## v1.0.2 contract-fix status

- Canonical knowledge: **366 facts / 119 entities**.
- Vietnamese agent train: **709 records**; multilingual support: **282 records**.
- Runtime integration is **blocked** until two external code contracts are reconciled: (1) `service_policies.json` must match the checksum-pinned `config/agent-domain.json` service registry exactly, and (2) evaluation route labels must be mapped to the current router/Command ontology.
- `service_actions.jsonl` now uses concrete runtime tool names and concrete existing-request preconditions.
- Five erroneous alias collisions were removed; the Chinese alias `客房服务` remains intentionally ambiguous between housekeeping and in-room dining and must resolve via clarification, not declaration order.
- 43 previously English-only entities now have `vi/ko/zh` display names.
- Tool-eval coverage is balanced at **60 cases per language**; lifecycle preconditions now seed a concrete existing request (`service_code`, `service_id`, `status`, supported-slot fixture).
- Chinese natural holdout coverage is **100 cases** (up from 56); the new batch has no exact or ≥82% near-overlap with Chinese training utterances.



## Data-consistency fix 1.0.3

- Retrieval train/evaluation coverage now spans all 366 canonical facts while preserving entity-disjoint and fact-disjoint holdouts.
- `service_actions.jsonl` is behavior-balanced per language: 10 each of create/modify/cancel/info/hours/info_and_hours; precondition slots are constrained to the corresponding service policy.
- Korean tool-eval placeholders such as `을(를)` were removed and the four language suites use natural guest wording.
- `maintenance` is classified `risk=low` to keep the existing reversible/no-approval synthetic autonomy path internally consistent; safety exclusions remain emergency-only. Runtime migration remains blocked until exact `agent-domain` parity is verified.
- Chinese alias `客房服务` is explicitly marked ambiguous between housekeeping and in-room dining and requires clarification.
- Car-rental display labels no longer include airport-transfer wording; airport transfer remains a separate canonical service.
