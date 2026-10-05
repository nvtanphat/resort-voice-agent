# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Single-property hotel concierge kiosk (currently the Furama resort): FastAPI backend + governed LLM agent (LangGraph) + grounded RAG + service-request workflows + local STT/TTS, with a React guest UI. Guest languages: `vi`, `en`, `zh`, `ko` (Vietnamese is the priority). Everything runs locally on CPU (Ollama SLM + bge-m3, OpenVINO reranker, faster-whisper, Piper). Project docs live in `docs/` (Vietnamese). `AGENT.md` holds the mandatory development rules (no hardcoding, where data goes, bug-fix procedure, voice/benchmark rules); follow it.

## Commands

```bash
python -m pip install -e ".[test,ops]"     # backend dev install (Python >= 3.11); add ",voice" for Whisper/Piper
set -a; . ./.env.example; set +a           # dev env: pinned hashes, Ollama SLM + bge-m3, dataset dir, voice model paths
python -m concierge_kiosk                  # API on CONCIERGE_BIND_HOST:CONCIERGE_BIND_PORT (default 0.0.0.0:8000)
python -m pytest -q                        # all tests (pytest config adds src/ and . to sys.path); run it WITHOUT .env.example sourced, its env vars override the test settings
python -m pytest -q tests/test_agent_runtime.py -k <name>   # one test
```

Open the UI at exactly `CONCIERGE_PUBLIC_ORIGIN` (default `http://localhost:8000`). Origin is enforced: `127.0.0.1:8000` gets 403 on `/api/session` and the voice WebSocket is rejected. The running server does not reload code or config; restart it after changes.

CI (`.github/workflows/ci.yml`) runs these; they should pass before finishing a change:

```bash
python -m compileall -q src tools
python tools/repin_configs.py --check      # pinned SHA-256 of config/releases match files and env templates
python tools/audit_furama_data.py
python -m pytest -q -W error::ResourceWarning   # unclosed SQLite connections/files fail the build
python -m pip_audit --strict
bandit -q -r src/concierge_kiosk tools -ll
cd frontend && npm ci && npm run build && cd ..
for f in web/guest.js web/ops.js web/pcm-worklet.js web/sos.js; do node --check "$f"; done
```

After data changes, also run `python datasets/schemas/validate_contracts.py` (fails on unclassified files or shape drift), `tools/validate_furama_schemas.py`, `tools/validate_furama_semantics.py` and `tools/validate_agent_domain.py`. To check a runtime profile, run `tools/validate_runtime_profile.py <profile.json>` with `PYTHONPATH=src`.

### Knowledge rebuild (after editing `datasets/knowledge/canonical/*`)

```bash
python tools/build_domain_vocab.py                  # data-derived NLU vocabulary
python tools/repin_configs.py                       # pin the new releases/domain-vocab.json now, or the steps below refuse to load it
set -a; . ./.env.example; set +a                    # re-source: the pinned *_SHA256 values in the env changed
python tools/build_furama_localized_knowledge.py    # -> knowledge/compiled/furama/<lang>/*.md (no CLI flags; runs immediately)
python tools/rebuild_furama_runtime_knowledge.py --model "ollama://bge-m3" \
  --manifest models/embeddings/bge-m3.ollama.manifest.json --require-learned   # re-ingests data/concierge.sqlite3 (~4 min)
python tools/build_furama_releases.py               # releases/*.json + config/local-runtime.env.example
python tools/repin_configs.py
```

- Stop the server before re-ingesting.
- After editing a data file, refresh the pinned hashes of its manifest with `tools/refresh_dataset_manifest.py`, `tools/refresh_synthetic_manifest.py` or `tools/refresh_furama_manifest.py` (each has `--check`); a stale manifest fails `tools/validate_property_dataset.py`.
- If you omit `--require-learned` and the bge-m3 model is unavailable, the rebuild can silently fall back to the hash embedder. Dense retrieval then reports a model mismatch in `/readyz`.
- Pass model URIs as plain strings: on Windows, `Path("ollama://…")` becomes `ollama:\…`.

### Evaluation (needs `.env.example` loaded and, except lexical runs, a running Ollama)

```bash
python tools/evaluation/run_retrieval_eval.py --suite grounded --output reports/retrieval/grounded.json   # also fact_holdout, compositional; --no-rerank, --mode lexical
python tools/evaluation/run_human_review_probe.py --lang vi --output reports/vi.json                       # end-to-end gold, also en/ko/zh
python tools/evaluation/run_tool_eval.py --tasks <tasks.jsonl> --base-url http://localhost:8000 --output reports/tool-eval/x.json
python tools/nlu/perturb.py && python tools/nlu/robustness_report.py         # NLU robustness (no-diacritic, typos, fillers…)
python tools/nlu/calibrate_router.py --model "ollama://bge-m3" --manifest models/embeddings/bge-m3.ollama.manifest.json
```

- Compare per-case results against a run from before your change. `run_retrieval_eval.py` writes every miss with its top-5 results and a failure cause.
- Calibrate the semantic router with the same learned embedder used at runtime; thresholds computed with the hash embedder are meaningless.
- Each eval run takes minutes.

Other tools:
- `tools/generate_ts_contracts.py`;
- `tools/runtime/download_voice_models.py` and `download_reranker_model.py` (download into the git-ignored `models/`);
- `tools/runtime/probe_local_slm.py`.

Check a script's argparse before running anything in `tools/maintenance/` or `tools/operations/` against a real DB.

Local Docker: `docker compose -f compose.local.yaml up --build` binds 127.0.0.1:8000. A container cannot reach Ollama on the host because model endpoints must be loopback.

## Architecture

Layering: `api/` routes → `application/` services → (`agent/` runtime | workflow service) → `rag/`, `domain/` → `persistence/sqlite_store.py`.

`main.py` is the composition root. `bootstrap.py` builds settings, store, workflows, embedder, reranker and `RAGPolicy`, and warms Ollama and the reranker. `main.py` builds the FastAPI `app` at import time, so importing `concierge_kiosk.main` opens a DB; set env vars first (see `tools/generate_ts_contracts.py` for the isolation pattern). Tests build isolated apps with `create_app(Settings(...))`.

- **API route groups** (`api/`): `guest`, `staff`, `public`, `internal`, `voice` (HTTP transcription, WebSocket streaming, TTS playback/proof); shared auth/security/contracts in `api/shared/`.
- **Turn flow** (`application/conversation/engine.py`, `services/turn_coordinator.py`):
  1. A deterministic router (`agent/understanding/routing.py::classify_dialogue`) runs first: emergency → language switch → greeting → confirmation → request change/status → planning → multi-task → service → knowledge fallback. Emergency always wins and must never be overridden.
  2. The embedding router (`agent/understanding/semantic_router.py`, bge-m3 kNN over route examples) is controlled by `nlu.semantic_router.mode` (`off|shadow|active`) in `config/agent-domain.json`.
  3. Model understanding produces a closed `Command[]` (`agent/understanding/commands.py`: `StartGoal`, `SetSlot`, `CorrectSlot`, `Cancel`, `Confirm`, `AskInfo`, `Navigate`, `Handoff`, `ChitChat`).
     - Commands are validated against the service registry, and slot text must be verbatim guest text.
     - Commands never authorize a write.
  4. The agent runs bounded tools, and every result passes `agent/core/tool_contracts.py` (`authorized_tool_result` / `validate_tool_result`).
     - A contract mismatch raises and becomes HTTP 500, so new result shapes must satisfy it.
     - Contracts are checked against `execution_query` (the guest text plus any resolved anchor title), not the bare query.
- **Tools and policy**:
  - Typed tool specs live in `agent/tools/registry.py`: Pydantic params/results for `hotel_info_search`, `hotel_hours_get`, `hotel_route_get`, `service_request_create|confirm|update|cancel|status`, `staff_handoff`, `itinerary_plan`, ….
  - Tool descriptions and examples are data in `agent-domain.json → tools.*`.
  - `agent/tools/policies.py::evaluate_policies` runs before tool calls and returns allow, deny(reason) or require_confirmation.
- **Planner and loop**:
  - `agent/runtime/planner.py` parses a closed JSON DAG from the local SLM, with a deterministic fallback.
  - Loop semantics are centralized in `agent/runtime/loop_semantics.py`, shared by `agent_loop.py` and the LangGraph adapter `langgraph_loop.py`. `CONCIERGE_ORCHESTRATOR` selects `langgraph` (default), `legacy` or `direct`.
  - `presentation/synthesizer.py` composes multi-read results. An abstention from one read must not be glued onto a verified answer from another.
  - `agent/proactive.py` produces suggestions only; it never writes.
- **Local SLM use is the fallback, not the default.** All SLM HTTP goes through `runtime/local_http.py`: loopback only, no redirects or proxies, per-turn deadline, connection circuit breaker. Voice turns use the tighter `voice_slm_caps`.
- **Agent vs. business truth**: the agent only proposes or orchestrates. Creating, confirming, changing or cancelling service requests must go through the workflow service and domain transition rules (`application/workflow_service.py`, `application/service_actions.py`, `domain/`). Low-risk services with `approval: none` may auto-execute, but voice turns with numeric slots (room, quantity) are forced to a read-back confirmation.
- **Two state stores**: business requests live in the main SQLite DB (`data/concierge.sqlite3`); LangGraph checkpoints live in a separate `<db-stem>-graph.sqlite3`. `tools/maintenance/reconcile_graph.py` reconciles the two (see `test_langgraph_durable_restart.py`).
- **Memory** (`agent/memory/`):
  - `conversation.py` resolves follow-ups to stored evidence anchors. Anchors keep the evidence row's own language.
  - Topic state is committed with compare-and-swap in `application/turn_lifecycle.py::TurnFinalizer`; a concurrent change returns 409.
  - Map-only answers anchor the place via `answers.place_anchor_sources`.
  - Pending service tasks and voice proposals live in `task_memory.py`; session preferences in `preferences.py`.
- **RAG** (`rag/`):
  - **Layout:** `documents`, `text/` (normalize, tokenization, safety), `embedding/` (base, hashed, onnx_e5, ollama, local, cache), `rerank/`, `ingestion/` (chunking, metadata, document, bundle, policy), `retrieval/` (engine, policy, context, evidence), `grounding/` (relevance, claims, citations), `index/`. `rag/common.py` and `rag/claims.py` are temporary re-export shims for the voice agent and semantic router; delete them once those import the focused modules.
  - **Chunks.** Each chunk is one self-contained canonical fact. The `knowledge` table carries `entity_id`, `fact_type`, `fact_context`, `canonical_fact_id`, `context_text` (entity · category · label: value (qualifier)) and `metadata_json` (including `domain_review`). Entity-card documents cover entities that have no facts.
  - **Retrieval order** (`rag/retrieval/engine.py::retrieve`):
    1. Structured lookup when `entity_ids` and `fact_types` (and optionally `fact_context`) are known.
    2. Lexical FTS.
    3. Dense vectors. Rows are filtered by `embedding_model`, so the running embedder must match the index. The shipped DB uses `ollama:bge-m3`; with any other embedder, retrieval silently drops to lexical-only, and `/readyz` reports this in `dense_retrieval`.
    4. RRF fusion.
  - **Conflict gate** (`policy.py::_policy_conflict`): runs before rerank and is keyed on `(entity_id, fact_type, fact_context)`. Do not compare digit-masked text; it equates "Ballroom 1" with "Ballroom 2".
  - **Rerank** (optional):
    - Scores `context_text` for the top `rerank_top_k` candidates, truncated to `max_length`.
    - Its score is fused with the RRF score via `rerank_fusion_alpha`, plus a bonus for structured matches.
    - On timeout, busy or error, the RRF order is kept (`rerank_on_failure`).
  - **After retrieval:** citation binding (`rag/grounding/citations.py`) and the `domain_review.runtime_gate` (pending facts need staff confirmation) are applied.
  - No-evidence answers come from a fixed template (`_allowed_no_evidence_answer`), so model prose cannot leak into them.
- **Voice**:
  - `features.voice_transport` selects `legacy|pipecat`. Development uses the authenticated same-origin `/api/voice/agent` Pipecat WebSocket; legacy `/api/audio/stream` and chunk playback remain as a rollback path until voice eval reaches parity.
  - Pipecat uses the official Protobuf WebSocket transport, Silero VAD, Local Smart Turn v3, guarded faster-whisper STT, the governed conversation engine, and in-process Piper TTS. The browser client is `frontend/src/voiceAgent.ts`.
  - STT decodes in the authenticated session language and retains the hallucination, repetition, confidence and `reject_reason` gates.
  - `SpeechGate` owns every spoken chunk: authorization, reserve/complete, and evidence freshness checks run before synthesis and again before playback acknowledgement.
  - Barge-in interrupts unheard audio and marks the active chunk failed; it does not roll back a committed workflow transition. `speech_rendering` changes only TTS input, never displayed text.

## Configuration, data-driven vocabulary and pinning

- `core/settings.py` reads `CONCIERGE_*` env vars over the selected runtime profile; `Settings.validate()` is strict when `CONCIERGE_ENV=production`. `tests/conftest.py` forces `CONCIERGE_ENV=test` and `CONCIERGE_RUNTIME_PROFILE=test` before app import.
- **Runtime profiles are generated.**
  - Edit `config/runtime-profiles/src/base.json` and the per-profile overlays, then run `python tools/build_runtime_profiles.py` (deep-merge, schema validation, repin).
  - Never edit `config/runtime-profiles/*.json` directly; `--check` detects drift.
  - A new key also needs `config/runtime-profile.schema.json` and wiring in `core/settings.py`. RAG keys additionally need `RAGPolicy` in `rag/retrieval/policy.py` and `bootstrap.py`.
- **Multilingual language rules live in `config/agent-domain.json`**: NLU frames and regexes, greeting/courtesy/cancel terms, numeral and clock grammar, normalization, emergency patterns and contacts, voice rendering, tool descriptions, semantic-router mode and thresholds. Read them through the accessors in `agent/understanding/domain_nlu.py` and `core/domain_profile.py`.
  - A new key needs `config/agent-domain.schema.json` and validation in `core/domain_profile/validate/`. Per-language keys also need the `_clone_language` helper in `tests/test_nlu_memory.py`.
  - **Do not put hotel entity or service names in it.** Those come from `datasets/` (aliases, `names_by_locale`, `build_domain_vocab.py`).
  - **Do not copy phrases from `datasets/evaluation/` into it.** When a natural phrasing is missed, add it to the router training examples, not to a phrase list.
- Guest-facing text lives in `locales/*.json`, read by `i18n/catalog.py` on the backend and through `frontend/src/i18n.ts` on the frontend. All four locales must be translated; an English placeholder left in vi/ko/zh is a bug.
- **After editing any pinned JSON** (`config/agent-domain.json`, runtime profiles, `releases/*.json`), run `python tools/repin_configs.py`. It rewrites the `.sha256` sidecars and the `*_SHA256=` lines in `.env.example` and `config/local-runtime.env.example`. With stale hashes, the map and planning releases refuse to load at runtime, and `tests/test_env_templates.py` fails.
- **Hard-code guard**: `tests/test_no_hardcode.py` fails on non-ASCII string literals (vi/zh/ko text), fixed `{'vi','en','zh','ko'}` sets, or `FURAMA`/`furama` literals in `src/`.
  - Exceptions are listed in `tests/hardcode_allowlist.txt` as `path:line:reason`.
  - Entries are keyed by line number, so edits that shift lines in an allowlisted file require updating the entry. Stale entries also fail the test. The allowlist should only shrink.
  - Use `supported_languages()` instead of literal language sets. Never add `if language == …` branches; put per-language behaviour in config.
- `property_id` comes from config/property profile, never from guest requests (one property per appliance).

## Data

- **`datasets/` is the only runtime data root** (`CONCIERGE_STRUCTURED_DATASET_DIR=datasets`). All paths go through `core/dataset_layout.py` (`dataset_path(...)`); do not build `datasets/...` paths by hand. Its layout:
  - `knowledge/canonical/`: facts, entities, aliases, service catalog, map, planning, contacts, `context_labels.json`. This is the only RAG truth.
  - `knowledge/sources/`: verified sources and evidence snapshots.
  - `quarantine/`: never indexed.
  - `synthetic/operations/`: product-authored SLAs, escalation and service policies. Usable by workflows, but never stated as official hotel facts.
  - `training/`: router examples and few-shot data.
  - `evaluation/`: measurement only.
- Never index `training/`, `evaluation/`, `quarantine/` or `synthetic/`, and never tune on `evaluation/`.
- These are **data, not project docs**. Don't delete or rewrite them when changing code or documentation.
- `knowledge/approved/furama/` (editorial source) and `knowledge/compiled/furama/` (generated, ingested into the DB) are derived artifacts. Regenerate them; don't hand-edit them.
- Keep traceability: source artifact → canonical fact → compiled knowledge → runtime release. After data changes, run `validate_contracts.py`, the schema and semantic validators, the knowledge rebuild above, and the data tests (`test_furama_*`, `test_knowledge_consistency.py`, `test_data_integrity_regressions.py`).

## Frontend

- Edit `frontend/src/` and rebuild. Never hand-patch `web/guest.js` or `web/guest.css`: the bundle comes from the custom `build_offline.cjs` (not Vite) and embeds a `source-sha256` of the sources. `npm run build` runs `tsc` first, so a type error leaves a stale bundle.
- `web/ops.html`, `web/ops.js`, `web/pcm-worklet.js` and `web/sos.js` are hand-maintained. The CSP is `script-src 'self'`, so inline `<script>` in `web/*.html` is blocked; put code in a static file.
- `frontend/src/generated/api-contracts.ts` is generated from the FastAPI OpenAPI schema by `tools/generate_ts_contracts.py`; regenerate it when the `Ask`/`Prepare`/`ServicePayload` models change.
