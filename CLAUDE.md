# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Single-property hotel concierge kiosk (currently the Furama resort): FastAPI backend + governed LLM agent (LangGraph) + grounded RAG + service-request workflows + local STT/TTS, with a React guest UI. Guest languages: `vi`, `en`, `zh`, `ko` (Vietnamese is the priority). Everything runs locally on CPU (Ollama SLM + bge-m3, OpenVINO reranker, faster-whisper, Piper). Project docs live in `docs/` (Vietnamese). `AGENT.md` holds the mandatory development rules (no hardcoding, where data goes, bug-fix procedure, voice/benchmark rules); follow it. `plan.md` is the current roadmap: the target is a governed AI agent, not a branch workflow; no case-specific hardcoding; delete superseded code in the same change.

## Commands

```bash
python -m pip install -e ".[test,ops]"     # backend dev install (Python >= 3.11); add ",voice" for Whisper/Piper/Pipecat
set -a; . ./.env.example; set +a           # dev env: pinned hashes, Ollama SLM + bge-m3, dataset dir, voice model paths
python -m concierge_kiosk                  # API on CONCIERGE_BIND_HOST:CONCIERGE_BIND_PORT (default 0.0.0.0:8000)
python -m pytest -q                        # all tests (pytest config adds src/ and . to sys.path); run it WITHOUT .env.example sourced, its env vars override the test settings
python -m pytest -q tests/agent/test_agent_runtime.py -k <name>   # one test
```

Tests that read the shipped knowledge DB must use `tests/shipped_db.py` (`shipped_store()` / the `shipped_db` fixture): opening the tracked `data/concierge.sqlite3` directly rewrites it. Behaviour removed with the keyword logic that returns with a rebuild step is listed as strict `xfail` in `tests/rebuild_pending.py` (an XPASS fails the run until the entry is deleted). A clean-checkout check of the CI steps needs `models/embeddings/*.ollama.manifest.json` and the `hash-multilingual` stub, which are tracked on purpose.

Open the UI at exactly `CONCIERGE_PUBLIC_ORIGIN` (default `http://localhost:8000`). Origin is enforced: `127.0.0.1:8000` gets 403 on `/api/session` and the voice WebSocket is rejected. The running server does not reload code or config; restart it after changes.

CI (`.github/workflows/ci.yml`) runs these; they should pass before finishing a change:

```bash
python -m compileall -q src tools
python tools/config/repin_configs.py --check      # pinned SHA-256 of config/releases match files and env templates
python tools/validate/audit_data.py
python -m pytest -q -W error::ResourceWarning   # unclosed SQLite connections/files fail the build
python -m pip_audit --strict
bandit -q -r src/concierge_kiosk tools -ll
cd frontend && npm ci && npm run build && cd ..
for f in web/guest.js web/staff.js web/status.js web/pcm-worklet.js web/sos.js; do node --check "$f"; done
```

After data changes, also run `python datasets/schemas/validate_contracts.py` (fails on unclassified files or shape drift), `tools/validate/schemas.py`, `tools/validate/semantics.py` and `tools/validate/agent_domain.py`. To check a runtime profile, run `tools/validate/runtime_profile.py <profile.json>` with `PYTHONPATH=src`.

### Knowledge rebuild (after editing `datasets/knowledge/canonical/*`)

```bash
python tools/knowledge/build_domain_vocab.py        # data-derived NLU vocabulary
python tools/config/repin_configs.py                # pin the new releases/domain-vocab.json now, or the steps below refuse to load it
set -a; . ./.env.example; set +a                    # re-source: the pinned *_SHA256 values in the env changed
python tools/knowledge/build_localized_knowledge.py # -> knowledge/compiled/furama/<lang>/*.md (no CLI flags; runs immediately)
python tools/knowledge/rebuild_runtime_knowledge.py --model "ollama://bge-m3" \
  --manifest models/embeddings/bge-m3.ollama.manifest.json --require-learned   # re-ingests data/concierge.sqlite3 (~4 min)
python tools/knowledge/build_releases.py             # releases/*.json + config/local-runtime.env.example
python tools/config/repin_configs.py
```

- Stop the server before re-ingesting.
- After editing a data file, refresh the pinned hashes of its manifest with `tools/manifest/refresh_dataset.py`, `tools/manifest/refresh_synthetic.py` or `tools/manifest/refresh_property.py` (each has `--check`); a stale manifest fails `tools/validate/property_dataset.py`.
- If you omit `--require-learned` and the bge-m3 model is unavailable, the rebuild can silently fall back to the hash embedder. Dense retrieval then reports a model mismatch in `/readyz`.
- Pass model URIs as plain strings: on Windows, `Path("ollama://…")` becomes `ollama:\…`.

### Evaluation (needs `.env.example` loaded and, except lexical runs, a running Ollama)

```bash
python tools/evaluation/run_retrieval_eval.py --suite grounded --output reports/retrieval/grounded.json   # also fact_holdout, compositional; --no-rerank, --mode lexical
python tools/evaluation/run_human_review_probe.py --lang vi --output reports/vi.json                       # end-to-end gold, also en/ko/zh
python tools/evaluation/run_tool_eval.py --tasks <tasks.jsonl> --base-url http://localhost:8000 --output reports/tool-eval/x.json --repeats 5
python tools/nlu/perturb.py && python tools/nlu/robustness_report.py         # NLU robustness (no-diacritic, typos, fillers…)
python tools/nlu/calibrate_service_fallback.py --model "ollama://bge-m3" --manifest models/embeddings/bge-m3.ollama.manifest.json
```

- Compare per-case results against a run from before your change. `run_retrieval_eval.py` writes every miss with its top-5 results and a failure cause.
- Calibrate the service fallback with the same learned embedder used at runtime; thresholds computed with the hash embedder are meaningless.
- Each eval run takes minutes.
- On the dev machine Ollama offloads to the GPU. For kiosk (CPU-only) latency numbers pass `options.num_gpu = 0`; otherwise SLM timings are several times too optimistic.

Other tools:
- `tools/config/generate_ts_contracts.py`;
- `tools/runtime/download_voice_models.py` and `download_reranker_model.py` (download into the git-ignored `models/`);
- `tools/runtime/probe_local_slm.py`.

Check a script's argparse before running anything in `tools/maintenance/` or `tools/operations/` against a real DB.

Local Docker: `docker compose -f compose.local.yaml up --build` binds 127.0.0.1:8000. A container cannot reach Ollama on the host because model endpoints must be loopback.

## Architecture

Layering: `api/` routes → `application/` services → (`agent/` runtime | workflow service) → `rag/`, `domain/` → `persistence/` (`sqlite_store.py` holds `Store`; DDL and triggers are in `schema.py`, upgrades in `migrations.py`).

Large modules have been split into packages that re-export their old public API: `core/domain_profile/` (`models`, `files`, `loader`, `accessors`, `validate/*`), `rag/` (see RAG below) and `voice/runtime/`. In `voice/runtime/`, `adapters.py` keeps STT and re-exports `rendering`, `audio`, `tts` and `languages`. Tests monkeypatch `adapters._whisper` and `adapters.voice_policy`, so the STT code must stay in `adapters.py`.

`main.py` is the composition root. `bootstrap.py` builds settings, store, workflows, embedder, reranker and `RAGPolicy`, and warms Ollama and the reranker. `main.py` builds the FastAPI `app` at import time, so importing `concierge_kiosk.main` opens a DB; set env vars first (see `tools/config/generate_ts_contracts.py` for the isolation pattern). Tests build isolated apps with `create_app(Settings(...))`.

- **API route groups** (`api/`): `guest`, `staff`, `public`, `internal`, `voice` (HTTP transcription, WebSocket streaming, TTS playback/proof); shared auth/security/contracts in `api/shared/`.
- **Turn flow** (`application/conversation/engine.py`, `services/turn_coordinator.py`):
  1. Emergency detection runs via a 2-tier architecture (no SLM call, emergency always wins and cannot be overridden):
     - **Tier 1 (Deterministic regex):** `agent/understanding/routing.py::classify_dialogue` matches immediate safety keywords (`emergency_event_patterns`).
     - **Tier 2 (Semantic classifier gate):** `agent/understanding/emergency_gate.py::EmergencyGate` is a small logistic-regression classifier over the bge-m3 embedding of the turn (reusing the `ServiceSelector` query-vector cache). It is trained at startup from the reviewed training examples (positive = `emergency`, negative = every other example; weights cached by data hash), so a new way of describing an emergency is handled by adding reviewed examples, never phrases. It costs one cached embedding and one dot product, uses no SLM, and keeps working when the SLM is down.
       - *Confident emergency* (probability >= `nlu.service_selector.emergency_min_prob`): routes immediately to `emergency` (`RouteDecision('emergency', True)`), aborts pending drafts/tasks, responds with `emergency_answer`, activates SOS UI (`show_staff_location`, `normal_request_disabled`), and queues staff safety alerts.
       - *Review zone* (probability >= `emergency_review_prob`): routes to `emergency_check` (`RouteDecision('emergency_check', True)`), returns emergency safety contacts and a multilingual confirmation question, enables SOS button without locking normal requests, and queues NO staff alert. If the guest affirms in the next turn (Layer A `_is_expected_confirmation`), it escalates to full emergency with staff alert. A false review costs the guest one tap, a missed emergency costs far more, so the review threshold is calibrated for recall.
       - *Calibration:* `tools/nlu/calibrate_emergency_gate.py` (group-held-out cross-validation; Vietnamese recall >= 0.98, full-escalation false positives <= 1%, review-zone false positives within budget) writes `reports/nlu/emergency-calibration.json` and the three `emergency_*` values (`emergency_l2` is the regularization strength).
       - *Failure fallback:* If the embedding model fails, the index is not ready (a guest turn never builds it) or the gate raises, it logs a warning and passes through without crashing (Tier 1 regex remains active).
  2. Every other turn gets one bounded SLM call that proposes a closed `Command[]` (`agent/understanding/commands.py::model_commands`: `StartGoal`, `SetSlot`, `CorrectSlot`, `Cancel`, `Confirm`, `AskInfo`, `Navigate`, `Handoff`, `ChitChat`). There is no other understanding mode.
     - `agent/understanding/service_selector.py::ServiceSelector` embeds the service catalog and the train-split examples in `datasets/training/agent/` with bge-m3. It ranks services by their best similarity to either and hands the model the top-k candidates plus the nearest examples as few-shots. Evaluation data is never loaded here.
     - `model_commands(on_outcome=…)` reports why a proposal is missing (`unavailable`, `no_response`, `turn_budget_expired`, `malformed_output`, `rejected_by_validation`, `accepted`); the engine logs it as `slm_commands outcome=…`. All of them still mean "no proposal" and the turn stays a knowledge read. `Confirm` is offered and accepted only while a confirmation is pending (a stray one is dropped without losing the rest of the turn); the same request stated twice becomes one `StartGoal`.
     - The JSON schema sent to Ollama is a per-type union: each `StartGoal` variant has a `const` goal from the candidates and an `enum` of that service's slots; `SetSlot`/`CorrectSlot` are offered only while a server question is pending. The model cannot emit a service or slot outside that set.
     - The server re-validates every command against the registry. A slot whose text is not verbatim guest text, or that the service does not accept, is dropped (never trusted) and the goal is kept, so the agent asks for it. Commands never authorize a write.
     - Validated commands override the route (`engine.py::_decision_from_commands`) and drive goal construction (`agent/runtime/state.py::build_initial_state`) and the loop (`loop_semantics._command_action`).
     - If the SLM is unavailable, times out or proposes nothing valid, `ServiceSelector.fallback_goal` picks a service by the nearest reviewed training turn (no model, no keyword list). It answers only above `nlu.service_selector.fallback_min_score`/`fallback_min_margin` (calibrated on the train split by `tools/nlu/calibrate_service_fallback.py`, leave-one-situation-out) and never for turns the generic question grammar marks as information questions (`intent.py::is_information_question`); otherwise the turn stays a knowledge read.
     - There is no keyword service routing anywhere: `classify_dialogue` only decides emergency; every other turn is a knowledge read until the embedding router, the validated SLM commands or the reviewed-example fallback says otherwise. `agent-domain.json` holds no service names and no intent phrase lists (greeting, thanks, cancel, confirmation, follow-up and preference wording were removed; the model proposes `ChitChat`, `Cancel`, `SetPreference`, … and the server validates them).
     - The understood service code travels end to end: `suggested_action.service` → `/api/requests/prepare` `service` → proposal payload `_service_code` → `service_requests.service_code`. The workflow never re-derives a service from free-text details; without one it uses the kind's default.
     - The selector indexes (catalog + about 600 examples, ~25 s) are built at startup in the background. A guest turn never builds them (`runtime/local_http.py::in_guest_turn`); before they are ready the model sees the full registry and no few-shots.
     - Measure understanding with `tools/evaluation/evaluate_command_understanding.py` (selector recall, command accuracy; `--fallback-only` for the model-free fallback).
     - Business-flow tests script understanding with the `understand` fixture in `tests/conftest.py`; the test profile has no SLM and `features.semantic_understanding=false`, so tests stay hermetic.
  3. The agent runs bounded tools, and every result passes `agent/core/tool_contracts.py` (`authorized_tool_result` / `validate_tool_result`).
     - A non-emergency contract mismatch fails closed. The payload is discarded and the guest gets the fixed abstention (`contract_failure_result`, HTTP 200, logged as `tool_contract_failed`). Emergency keeps its own deterministic fallback.
     - Contracts are checked against `execution_query` (the guest text; follow-up anchor resolution is not rebuilt yet), not a model-supplied query.
     - Tool exceptions inside the loop never reach HTTP: `agent/runtime/runtime.py::_execute` turns them into `unavailable` observations with a `failure_class`.
- **Tools and policy**:
  - Typed tool specs live in `agent/tools/registry.py`: Pydantic params/results for `hotel_info_search`, `hotel_hours_get`, `hotel_route_get`, `service_request_create|confirm|update|cancel|status`, `staff_handoff`, `itinerary_plan`, ….
  - Tool descriptions and examples are data in `agent-domain.json → tools.*`.
  - `agent/tools/policies.py::evaluate_policies` runs before tool calls and returns allow, deny(reason) or require_confirmation.
- **Planner and loop**:
  - `agent/runtime/planner.py` parses a closed JSON DAG from the local SLM, with a deterministic fallback.
  - Loop semantics are centralized in `agent/runtime/loop_semantics.py` and the LangGraph adapter `langgraph_loop.py`.
  - `presentation/synthesizer.py` composes multi-read results. An abstention from one read must not be glued onto a verified answer from another.
  - `agent/proactive.py` produces suggestions only; it never writes.
- **SLM calls**: the local SLM is called once per non-emergency turn for understanding. The planner model runs only for compound goals or after a failed read, and is skipped for voice. All SLM HTTP goes through `runtime/local_http.py`: loopback only, no redirects or proxies, per-turn deadline, connection circuit breaker. Voice turns use the tighter `voice_slm_caps`.
- **Agent vs. business truth**: the agent only proposes or orchestrates. Creating, confirming, changing or cancelling service requests must go through the workflow service and domain transition rules (`application/workflow_service.py`, `application/service_actions.py`, `domain/`).
  - Guest confirmation, staff review and fulfillment run as a durable LangGraph graph with `interrupt()` in `agent/orchestration/graph.py`.
  - The release property profile sets `hitl_mode=guest_confirm_all`, so even low-risk services with `approval: none` (`amenity_delivery`, `housekeeping`, `maintenance`) require the guest confirmation/staff workflow. Voice turns with numeric slots (room, quantity) are still forced to a read-back confirmation.
  - Request lifecycle, ack/SLA clocks and two-level escalation live in `domain/requests/`.
- **Two state stores**: business requests live in the main SQLite DB (`data/concierge.sqlite3`); LangGraph checkpoints live in a separate `<db-stem>-graph.sqlite3`. `tools/maintenance/reconcile_graph.py` reconciles the two (see `test_langgraph_durable_restart.py`).
- **Memory** (`agent/memory/`):
  - Follow-ups ("book it there") use no phrase list: `command_for_session` gives the model the last verified anchor title (`context_topic`) and, only then, requires `refers_to_context` on `StartGoal`/`AskInfo`/`Navigate`. The server honours the flag only when `recent_anchor` exists and then prefixes the anchor title to the execution query (`engine._referenced_topic_query`); the venue slot is resolved from that title by the registry's `venue_slot`. `snapshot()` still returns no anchor (the old keyword-based inheritance is gone), and the cases that depended on it stay strict xfails in `tests/rebuild_pending.py`. A corrected clock time keeps the daypart it replaces (`numerals.preferred_time`). Pending slots, confirmations and workflow state carry context as before.
  - Topic state is committed with compare-and-swap in `application/turn_lifecycle.py::TurnFinalizer`; a concurrent change returns 409.
  - Map-only answers anchor the place via `answers.place_anchor_sources`.
  - Pending service tasks and voice proposals live in `task_memory.py`; session preferences in `preferences.py`.
  - **A preference is a proposal until the guest confirms it.** A model `SetPreference` must carry `evidence` (a verbatim span of the guest turn; checked in `validate_commands`, an invalid one is dropped alone). A preference-only turn asks "remember '<evidence>'?" and holds the proposal in the in-memory `PendingPreferenceStore` for exactly one following turn; only an affirmative answer then writes `preference_memory`. Nothing unconfirmed reaches effective preferences, the database or a checkpoint. A pending service confirmation always wins (no preference question while one waits), emergencies are checked before an affirmation is honoured, and a preference stated together with another request is not stored (nothing is appended to a grounded answer). Reviewed training examples are exempt from `evidence` (`require_evidence=False`).
- **RAG** (`rag/`):
  - **Layout:** `documents`, `text/` (normalize, tokenization, safety), `embedding/` (base, hashed, onnx_e5, ollama, local, cache), `rerank/`, `ingestion/` (chunking, metadata, document, bundle, policy), `retrieval/` (engine, policy, context, evidence), `grounding/` (relevance, claims, citations), `index/`.
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
  - The runtime profile's `voice_stt_models` can name a different STT model per language. `CONCIERGE_WHISPER_MODEL_PATH` (set in `.env.example`) overrides all of them.
  - `SpeechGate` owns every spoken chunk: authorization, reserve/complete, and evidence freshness checks run before synthesis and again before playback acknowledgement.
  - Barge-in interrupts unheard audio and marks the active chunk failed; it does not roll back a committed workflow transition. `speech_rendering` changes only TTS input, never displayed text.

## Configuration, data-driven vocabulary and pinning

- `core/settings.py` reads `CONCIERGE_*` env vars over the selected runtime profile; `Settings.validate()` is strict when `CONCIERGE_ENV=production`. `tests/conftest.py` forces `CONCIERGE_ENV=test` and `CONCIERGE_RUNTIME_PROFILE=test` before app import.
- **Runtime profiles are generated.**
  - Edit `config/runtime-profiles/src/base.json` and the per-profile overlays, then run `python tools/config/build_runtime_profiles.py` (deep-merge, schema validation, repin).
  - Never edit `config/runtime-profiles/*.json` directly; `--check` detects drift.
  - A new key also needs `config/runtime-profile.schema.json` and wiring in `core/settings.py`. RAG keys additionally need `RAGPolicy` in `rag/retrieval/policy.py` and `bootstrap.py`.
  - **Multilingual language rules live in `config/agent-domain.json`**: NLU frames and regexes for slots, numeral and clock grammar, time expressions, negation grammar, normalization, emergency patterns and contacts, voice rendering, tool descriptions, the `rag.facet_fact_types` facet table, `nlu.service_selector` mode and thresholds. Read them through the accessors in `agent/understanding/domain_nlu.py` and `core/domain_profile/` (`supported_languages()`, `nlu_policy()`, `rag_policy()`, …).
  - A new key needs `config/agent-domain.schema.json` and validation in `core/domain_profile/validate/`. Per-language keys also need the `_clone_language` helper in `tests/agent/test_nlu_memory.py`.
  - **Do not put hotel entity or service names in it.** Those come from `datasets/` (aliases, `names_by_locale`, `tools/knowledge/build_domain_vocab.py`).
  - **Do not copy phrases from `datasets/evaluation/` into it.** When a natural phrasing is missed, add it to the router training examples, not to a phrase list.
- Guest-facing text lives in `locales/*.json`, read by `i18n/catalog.py` on the backend and through `frontend/src/i18n.ts` on the frontend. All four locales must be translated; an English placeholder left in vi/ko/zh is a bug.
- **After editing any pinned JSON** (`config/agent-domain.json`, runtime profiles, `releases/*.json`), run `python tools/config/repin_configs.py`. It rewrites the `.sha256` sidecars and the `*_SHA256=` lines in `.env.example` and `config/local-runtime.env.example`. With stale hashes, the map and planning releases refuse to load at runtime, and `tests/ops/test_env_templates.py` fails.
- **Hard-code guard**: `tests/agent/test_no_hardcode.py` fails on non-ASCII string literals (vi/zh/ko text), fixed `{'vi','en','zh','ko'}` sets, or `FURAMA`/`furama` literals in `src/`.
  - Exceptions are listed in `tests/hardcode_allowlist.txt` as `path:line:reason`.
  - Entries are keyed by line number, so edits that shift lines in an allowlisted file require updating the entry. Stale entries also fail the test. The allowlist should only shrink.
  - Use `supported_languages()` instead of literal language sets. Never add `if language == …` branches; put per-language behaviour in config.
- `property_id` comes from config/property profile, never from guest requests (one property per appliance).

## Data

- **`datasets/` is the only runtime data root** (`CONCIERGE_STRUCTURED_DATASET_DIR=datasets`). All paths go through `core/dataset_layout.py` (`dataset_path(...)`); do not build `datasets/...` paths by hand. Its layout:
  - `knowledge/canonical/`: facts, entities, aliases, service catalog, map, planning, contacts, `context_labels.json`. This is the only RAG truth.
  - `knowledge/sources/`: verified sources and evidence snapshots.
  - `quarantine/`: never indexed.
  - `synthetic/operations/`: product-authored SLAs, escalation and service policies, plus non-public operational fixtures (restaurants, tours, transport, spa, PMS stays, room inventory, charge rules). They are usable by workflows and tools but never stated as official hotel facts. Each file declares its own usage (`classification`, `truth_status`, `guest_answer_policy`).
    - Service policies, escalation, departments and workflows are wired into runtime; the read-only synthetic availability adapter also covers restaurant/spa/tour/transport observations with explicit provenance. Write/booking/PMS fixtures remain non-authoritative until an upstream contract is approved.
  - `training/`: router examples and few-shot data.
  - `evaluation/`: measurement only.
- Never index `training/`, `evaluation/`, `quarantine/` or `synthetic/`, and never tune on `evaluation/`.
- These are **data, not project docs**. Don't delete or rewrite them when changing code or documentation.
- `datasets/` and `knowledge/compiled/` are git-ignored on the `dev` branch. The `dev-full-local` branch tracks them, so checking it out and back deletes them from disk. Do not switch branches. To recover a file, use `git restore --source=dev-full-local --worktree -- <path>`.
- `knowledge/approved/furama/` (editorial source) and `knowledge/compiled/furama/` (generated, ingested into the DB) are derived artifacts. Regenerate them; don't hand-edit them.
- Keep traceability: source artifact → canonical fact → compiled knowledge → runtime release. After data changes, run `validate_contracts.py`, the schema and semantic validators, the knowledge rebuild above, and the data tests (`test_furama_*`, `test_knowledge_consistency.py`, `test_data_integrity_regressions.py`).

## Frontend

- Edit `frontend/src/` and rebuild. Never hand-patch `web/guest.js` or `web/app.css`: the bundle comes from the custom `build_offline.cjs` (not Vite) and embeds a `source-sha256` of the sources. `npm run build` runs `tsc` first, so a type error leaves a stale bundle.
- `web/staff.html`, `web/staff.js`, `web/status.html`, `web/status.js`, `web/pcm-worklet.js` and `web/sos.js` are hand-maintained. The CSP is `script-src 'self'`, so inline `<script>` in `web/*.html` is blocked; put code in a static file.
- `frontend/src/generated/api-contracts.ts` is generated from the FastAPI OpenAPI schema by `tools/config/generate_ts_contracts.py`; regenerate it when the `Ask`/`Prepare`/`ServicePayload` models change.
