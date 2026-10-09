# CPU / SLM stabilization evidence — 2026-10-09

HEAD: `72e749649f764589e3dd671b3d993e9fe79f47a5`, branch `phase4-handoff`.
The local changes from the preceding audits were preserved. No commit, push,
model download, CI change, dependency upgrade or benchmark was performed.

**Latest readiness/warm run:** explicit preload PASS; real guest NLU **TIMEOUT / WARM_NLU_TIMEOUT** even with verified resident Qwen. Two new HTTP calls, cumulative **5/5**; no further inference permitted in this acceptance budget. No REAL_MODEL_PASS. See the latest section below and [raw trace](../reports/agent-warm-nlu-smoke.json). Earlier sections retain historical cold-run measurements, not the latest state.

## Verified diagnosis (historical cold run)

The previous smoke used two HTTP Qwen calls: background startup warm-up and a
guest command, overlapping. Its warm-up load took about 5.04 seconds; this is
not a guest prompt-processing or decoding measurement.

The corrected harness was executed once, with startup warm-up disabled,
planner and semantic generation disabled, no fallback model, no embedding
inference, and an empty Ollama `/api/ps` immediately before execution. BGE-M3
had expired before this run. Qwen was installed but cold. The model was
`qwen2.5:3b`, Q4_K_M, digest
`357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b`.

LIVE-01 again timed out. Ollama's existing server log establishes the first
failure boundary: model tensor loading had not finished when the client closed
its connection. At `2026-10-09T08:30:44.453+07:00`:

```text
client connection closed before llama-server finished loading, aborting load
Load failed ... timed out waiting for llama-server to start: context canceled
[GIN] 2026/10/09 - 08:30:44 | 499 | 3.1925269s | ... POST "/api/chat"
```

Thus cold loading exceeds the existing three-second command deadline on this
run, independently of the previous competing warm-up. The model never returned
guest commands. Prompt length may affect subsequent warm inference, but it is
not a demonstrated cause of this cold-load timeout. Warm performance, queue
waiting, prompt evaluation and token generation remain unmeasured.

## Measured request and configuration

| Field | Previous guest attempt | Corrected LIVE-01 |
| --- | --- | --- |
| Startup Qwen warm-up | Overlapped guest request | Disabled before lifespan |
| Command timeout | 3.0 s | 3.0 s, unchanged |
| Effective HTTP timeout | About 3 s | 3.0 s |
| Turn SLM budget | 8 s | 8 s; does not override the smaller command timeout |
| `num_ctx` | 4096 | 4096 |
| `num_predict` | 220 | 220 |
| `num_gpu` | 0 | 0 |
| HTTP payload bytes | 20,587 | 20,069 |
| Message-content bytes | Not separately recorded | 5,693 |
| JSON schema bytes | Not separately recorded | 13,927 |
| Offered service count | Not separately recorded | 14 |
| Sent few-shots | Not separately recorded | 0 |
| Measured prompt tokens | Not available | Not available |
| Command transport latency | 3.016 s | 3.000 s |
| End-to-end guest API latency | 4.797 s | 4.109 s |
| Raw command response | null | null |

Byte counts are not token counts. The older harness's `transport.prompt_bytes`
field actually means serialized HTTP payload bytes; the corrected harness
separately records `http_calls.prompt_bytes` and `http_payload_bytes`.
No final Ollama event was received, so `load_duration`, `prompt_eval_count`,
`prompt_eval_duration`, `eval_count` and `eval_duration` are unavailable.
Parsing/validation had no model JSON to process. The fallback runtime reports
202 ms, one deterministic knowledge tool, no verified evidence, and zero
business writes; this is not successful service understanding.

## Inference accounting and hooks

**This stabilization task: 1 actual Qwen HTTP call. Prior task: 2. Total: 3/5.**
Two unused calls were deliberately left unused after TIMEOUT. LIVE-02 and
LIVE-03 are NOT_RUN. No retry or extra warm-up occurred. Health GETs are excluded.

All source chat paths were inspected:

- `main._build_lifespan → local_ai.warm_local_slm` (background, default 90 s).
- `understanding.commands.model_commands → semantic._chat` (command NLU).
- `memory.reference_resolver.model_reference_choice → semantic._chat`.
- `runtime.planning.goal_interpreter` and `runtime.planner` model calls.
- `understanding.semantic` generation and optional model verification.
- `orchestration.grounding` direct legacy generation transport.

These paths converge on `runtime.local_http.local_chat_open → _OPENER.open`.
`tools/runtime/agent_stabilization_smoke.py` intercepts that actual HTTP boundary,
limits calls to three total and one per case, blocks embeddings and additional
requests, disables warm-up before TestClient lifespan, and refuses to repeat
an already recorded run. Capturing only `commands._chat` would miss other hooks.
The broader runtime still has startup warm-up; this task disables it in smoke,
not in deployment. The deadline closes the HTTP connection, and this run's
server log specifically confirms cancellation while loading.

## Changes and limits

`commands.model_commands` now treats a retrieval shortlist as ordering rather
than an allowlist: every enabled registry service remains available in the
dynamic schema, even outside top-k or when the shortlist is empty. Unused,
duplicated metadata was removed from prompt candidates and the user JSON is
compact. A deterministic red/green regression reproduced lost out-of-shortlist
goals. Guest span checking, goal-specific slot validation, tool authority and
confirmation remain enforced.

The smaller serialized request is not evidence of faster decoding. Timeout,
model and context limits were not increased. A longer bounded cold-load budget
or a readiness-gated warmed deployment would be an operational trade-off;
neither has been measured or accepted here. Three-second cold command service
is **TIMEOUT**, and stable real-model understanding remains unproven.

Detailed local trace: `reports/agent-stabilization-real-smoke.json`.
The SQLite copy was temporary; source SHA-256 stayed
`3d213efbf50b809b56290743935b5fa5c19ffdf45faa530747961678d32ffad7`.

## Readiness and warm inference validation — latest run, 2026-10-09

### Repository and evidence

HEAD/branch remain `72e749649f764589e3dd671b3d993e9fe79f47a5` / `phase4-handoff`. At task entry the existing tree contained 43 modified tracked files and 12 untracked files; these prior edits were preserved. This task added changes to runtime readiness, startup admission, the existing smoke harness, readiness regressions and these two reports. No business workflow, UI, CI, model, dependency, commit or push change was made in this task.

Evidence: [complete client/API/model/server trace](../reports/agent-warm-nlu-smoke.json), [targeted test output](../reports/warm-nlu-targeted.txt). Python 3.12 on Windows, AMD Ryzen 5 5600H, six inference threads reported by Ollama, CPU-only. Qwen2.5:3B Q4_K_M digest `357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b`; no fallback model. Available memory before preload: 3,537,629,184 bytes; after: 1,574,494,208 bytes (90% memory load). The preflight and post-load headroom checks passed; memory pressure remains a possible performance contributor, not a proven explanation of throughput.

### Complete inference hook audit

All current Qwen HTTP paths converge on `runtime.local_http.local_chat_open` and its shared `_OPENER.open`; no source `/api/generate` caller exists. The smoke ledger intercepts that opener, counts each real POST before sending it, holds its lock through response closure, rejects unexpected models/endpoints, and enforces two calls maximum, one per phase. GET `/api/tags` and `/api/ps` do not perform inference. Server POST logs corroborate both counted calls.

| Component / caller | Model call | Automatic/manual | Deadline | Can overlap? |
|---|---|---|---|---|
| `main._build_lifespan.warm_voice` → `warm_local_slm` | Qwen `/api/chat`, empty messages after fix | Automatic startup | Existing finite 90 s default, outside guest turn | Before fix yes; now shares app `AudioAdmission` with guest SLM; skip if busy, no retry |
| `engine.command_for_session` → `commands.model_commands` → `semantic._chat` | Qwen `/api/chat`, structured commands | Guest routing | NLU 3 s; clipped to remaining 8 s turn budget; voice cap applies | App admission denies another local SLM; smoke permits exactly one NLU |
| `engine` reference resolution → `model_reference_choice` | Qwen via `_chat` | Conditional verified reference selection | min(1.8 s, intent timeout), remaining turn budget | Same app admission; not invoked for this live case |
| `engine` planning → `model_action_plan` / `model_next_action` | Qwen via `_chat` | Compound/recovery planning; possible replan/fallback model attempts | 5 s default, remaining turn budget and voice cap | Same admission; disabled in smoke; no model planner for a supported simple read |
| `engine.goal_interpreter_for_session` → model goal interpreter | Qwen via `_chat` | Conditional semantic understanding | 2.5 s default, remaining turn budget / voice cap | Same admission; semantic understanding disabled in smoke |
| `answers` → `semantic_grounded_response` (+ optional model verifier) | Qwen via `_chat`; configured verifier can add a call | Conditional evidence generation | min(8 s turn configuration, text/voice generation cap), remaining turn budget | Same admission; no sources in this smoke, semantic generation disabled |
| `answers` → `agent.orchestration.grounding.grounded_response` | Qwen via `local_chat_open` | Evidence generation fallback after semantic attempt | Same bounded generation setting | Same admission; no evidence, so zero generation calls here |
| Startup `warm_voice_models` | Local Whisper/Piper | Automatic | Native model initialization, not Qwen HTTP | Can compete for CPU/RAM; harness suppresses before lifespan |
| `service_selector.warm`, lazy `_background_warm`, emergency gate, retrieval/embedding | BGE/local embedding; Ollama embedding backend may use `/api/embed` | Startup, lazy background or retrieval | Embedding backend's configured timeout; not command NLU timeout | Can consume CPU outside SLM lane; harness disables embeddings and rejects embedding POSTs |
| Local reranker / independent NLI readiness | Local model load/evaluation, no Qwen chat | Configured startup/retrieval/strict readiness | Native evaluation rather than chat deadline | Possible CPU/RAM contention in normal app; disabled or not loaded in this smoke |
| Harness explicit preload | Qwen `/api/chat` empty messages | Manual acceptance run only | Separate finite 30 s, not an increased NLU timeout | Exactly one, completed before guest; HTTP boundary rejects overlap |

Production strict readiness checks configured identity and independent NLI; that does not prove model residency. Development's fallback/automatic GPU defaults are explicitly overridden only in this harness with the approved digest, no fallback and `num_gpu=0`. Production requires an operator-pinned digest; this task does not rewrite runtime profiles.

### Readiness fix and its limits

`runtime.local_ai.model_residency` queries both tags (installed identity) and ps (resident identity), with bounded loopback-only GETs, rejecting invalid/missing/mismatched digests. States are `MODEL_NOT_LOADED`, `MODEL_LOADING`, `MODEL_READY`, `MODEL_UNAVAILABLE`. LOADING represents this process's registered explicit preload and is cleared even on failure. An empty external ps cannot reveal another process's in-progress load; residency also does not prove that an external client has no queued task. The live server trace shows an initially empty/idle slot and no competing POST in this run.

The harness blocks guest NLU until the exact pinned model is exclusively resident with CPU `size_vram=0` and context 4096. It skips preload if already resident, otherwise sends one empty-message supported chat preload, checks ps again, and never retries. Startup warm is disabled in the harness before app lifespan; production startup instead shares existing app admission with every guest SLM path. A guest arriving while that preload owns admission uses existing safe fallback rather than launching competing NLU; production does not preload each guest request.

Warm-up now uses a finite five-minute keep_alive instead of thirty minutes and empty messages instead of a dummy generated answer. No permanent RAM residency is requested. Cancellation is cooperative before/after preload and between streamed events, with response closure on failure; a blocked socket read is bounded by its timeout, not instantly interrupted by asyncio cancellation. Startup admission remains held by the native worker until it actually returns.

### Root cause classification

| Finding | Verified evidence | Root cause | Fix / result |
|---|---|---|---|
| Previous cold NLU timeout | Earlier Ollama 499 during tensor loading | Guest deadline included cold model load | Separate bounded preload and ps readiness gate; preload now PASS |
| Prior startup/guest overlap | Old startup warm lacked `AudioAdmission` | Warm and guest could independently occupy CPU model work | Startup now uses same existing admission lane; deterministic overlap test PASS |
| Resident guest still times out | Verified ps before guest; server launched prompt of 1,153 tokens, cached tokens 0; canceled with n_tokens 512 | Warm request cannot reach first response headers within configured 3 s on this run | Classified WARM_NLU_TIMEOUT; stop, no retry or timeout increase |
| No raw commands / no review | Raw response null; validation outcome `no_response`; commands empty | Transport deadline, not demonstrated schema/goal/slot rejection | Preserve validation; no fabricated model output or service draft |
| Schema or decoding overhead | Schema 13,927 bytes, output cap 220, no completed provider timing record | Possible costs, not separately measured causes | No unsupported prompt surgery or contract reduction |

The warm server log reports HTTP **500** after 3.0136142 s, then `cancel task` and slot release. The client saw TimeoutError before receiving headers, not an HTTP 500 response. This differs from the earlier cold-run server 499. No logged error body explains the precise server status; the task cancellation and incomplete prompt processing are observable. Queue wait, schema compilation and token generation duration cannot be separated from this trace. The server's n_tokens=512 is a cancellation-state counter, not a completed prompt_eval_count or eval_count. Do not derive tokens/sec from it.

### Performance and configuration

| Metric | Cold / explicit preload | Warm guest NLU |
|---|---|---|
| Provider load_duration | NOT_MEASURED (field absent) | NOT_MEASURED |
| Runner startup log | 4.52 s | Already resident; no reload in log |
| Prompt evaluation duration | NOT_MEASURED; empty preload does not validate NLU | NOT_MEASURED (unfinished); server prompt 1,153 tokens, zero cached tokens |
| Token generation | NOT_MEASURED; empty content, done_reason=load | NOT_MEASURED; no response/token received |
| Client HTTP elapsed | 4.656 s, HTTP 200 | 3.046 s; TimeoutError, no response headers |
| Server HTTP elapsed / status | 4.6562673 s / 200 | 3.0136142 s / 500 |
| Guest API total elapsed | NOT_APPLICABLE | 4.281 s / API HTTP 200 safe abstention |
| Socket timeout | Separate 30 s | Existing 3.0 s, unchanged |
| Shared guest turn model budget | NOT_APPLICABLE | Existing 8.0 s, unchanged |

A socket timeout is an idle/header/read bound, not an independent hard total three-second deadline after streaming begins. The existing shared lazy turn budget limits successive model calls and streamed processing to eight seconds, checked cooperatively at events. This live failure occurred before headers, so its effective first-response deadline was three seconds. No default timeout, num_ctx or num_predict was increased. A longer warm deadline could trade responsiveness for completion, but successful completion time is NOT_MEASURED; no new timeout is justified quantitatively by this unfinished run.

Offline construction used the real command builder: total serialized payload **20,069 bytes**, message content **5,693 bytes**, JSON schema **13,927 bytes**, 34 command variants, all **14 enabled service goals**, 28 per-goal slot definitions, zero few-shots, temperature 0, num_ctx 4096, num_predict 220, num_gpu 0. Candidate ordering prioritizes a shortlist but includes every enabled service. requested_item, unit, requested_date and source-span validation remain present. Bytes are not token estimates; the separate 1,153 token observation comes from Ollama. No new prompt/schema optimization was made: reducing enabled services would violate coverage, and individual schema/decoding cost has not been measured. No BGE model was loaded in live smoke.

### Inference accounting

| Call | Purpose and UTC start/end | Result | Count |
|---|---|---|---|
| Historical 1–3 | Prior overlapping warm/guest and corrected cold NLU | Historical evidence above | 3 |
| 4 | Explicit empty preload, 02:06:53.726328 – 02:06:58.383635 | PASS, server/client HTTP 200; exact model resident afterward | 1 |
| 5 | Real API guest NLU, starts 02:07:00.243698 | TIMEOUT / WARM_NLU_TIMEOUT; corresponding server POST at 09:07:03 +07 | 1 |
| Further warming/planner/reference/guest/retry | Not executed | NOT_RUN, budget exhausted | 0 |

Total **2 new / 5 historical cumulative calls**. Two client POST records match two Ollama POST rows (preload and warm NLU). No hidden/parallel Qwen call appears in this trace; unexpected calls would have been blocked at the common HTTP opener. GET probes, offline payload construction and mock tests use zero inference. Ollama's internal empty runner initialization is part of call 4, not a separate application HTTP inference request.

### Guest trace and validation

Anonymized session A, input `cho toi 3 chai nuoc suoi phong 502`. Raw model response `[null]`; validated commands `[]`; validation `no_response`. Selected service goal, extracted/rejected slots and service review payload: **NOT_RUN / unavailable because no commands arrived**, not slot validation failures. Route fell back to `knowledge:verified_answer`; the only tool was `hotel_info_search`, `safe_fallback`, unverified. No anchors, evidence or citations; response abstained. No proposed service, no inventory claim, no substitution, but item recognition is **not proven**. Business writes delta **0**.

The API ran the real application/session/ask paths through in-process FastAPI TestClient, real Ollama transport and real governed runtime, with a temporary SQLite copy. No external web server or browser E2E ran. DB source SHA-256 before and after: `3d213efbf50b809b56290743935b5fa5c19ffdf45faa530747961678d32ffad7` (identical).

| Targeted validation | Result | Evidence |
|---|---|---|
| Four readiness states, identity/malformed digest, loading cleanup | DETERMINISTIC_PASS | test_slm_readiness.py |
| Startup/guest isolation, HTTP accounting, stream timeout/response closure | DETERMINISTIC_PASS | test_slm_readiness.py; mocked HTTP |
| All enabled goals and slot contracts outside priority shortlist | DETERMINISTIC_PASS | test_slm_readiness.py, test_command_cpu_contract.py |
| Command grounding, item/unit/quantity/room and review fidelity | DETERMINISTIC_PASS | test_commands.py, test_item_fidelity.py |
| Existing guest change confirmation and expiry/idempotency boundaries | DETERMINISTIC_PASS | test_change_confirmation.py |
| Date/time correction and persistence independence | DETERMINISTIC_PASS | test_service_dates.py |
| Existing finite transport turn latency policy | DETERMINISTIC_PASS | test_slm_latency.py |
| Combined targeted run | DETERMINISTIC_PASS | 71 passed, one existing Starlette warning, 8.42 s; prior pre-live run 69 passed |
| Real preload / ps identity and CPU residency | PASS | Real HTTP/server trace, not health-only |
| Real item fidelity / review completion | TIMEOUT | WARM_NLU_TIMEOUT; zero business writes |
| Real context, weather/flight, compound intent | NOT_RUN | User's two-call protocol; no budget remaining |
| Browser E2E / overall production readiness | NOT_RUN | Outside current validation |

Executed: `python -m pytest -q tests/agent/test_slm_readiness.py tests/agent/test_slm_latency.py tests/agent/test_command_cpu_contract.py tests/agent/test_commands.py tests/agent/test_item_fidelity.py tests/agent/test_change_confirmation.py tests/agent/test_service_dates.py --tb=short`. No full pytest/benchmark. Context/RAG modules were not changed in this task; their earlier deterministic results remain historical, not fresh real-model results.

### Exact code changes and remaining gaps

- `src/concierge_kiosk/runtime/local_ai.py`: bounded tags/ps readiness and four states; one explicit finite preload with cleanup/cancellation; malformed digest fails closed; startup warm acquires/releases existing admission. Five-minute residency limits retention. No schema/semantic or guest timeout changes.
- `src/concierge_kiosk/main.py`: pass the existing app audio admission to the startup worker, eliminating startup/guest Qwen overlap within this app.
- `tools/runtime/agent_stabilization_smoke.py`: enforce two-call protocol, exact identity/CPU/RAM checks, readiness gate, startup/embedding suppression, common HTTP accounting through response closure, offline schema diagnostics, real API/raw-command/tool/write trace and DB hash evidence; refuse repeating an existing acceptance output.
- `tests/agent/test_slm_readiness.py`: mocked readiness/loading/admission/accounting/timeout/contract regressions. No real calls.
- `docs/AGENT-CPU-SLM-REPORT.md`, `docs/AGENT-E2E-SMOKE-REPORT.md`: preserve old evidence and distinguish current warm failure, real observations and deterministic coverage.

Cold readiness is fixed for the acceptance protocol, but warm NLU remains **TIMEOUT** on this CPU/deadline. Semantics remain **BLOCKED** by lack of a completed response; no REAL_MODEL_PASS is claimed. External Ollama callers are outside app admission; ps alone cannot certify their absence in future runs. Exact schema compilation/queue/decode costs and viable completion timeout remain NOT_MEASURED. Memory pressure warrants a future controlled measurement under a new budget, not additional calls now. No unbounded retention or automatic retry was added.

Protocol reference: Ollama documents [resident model inspection](https://docs.ollama.com/api/ps), [chat structured output/timing fields](https://docs.ollama.com/api/chat), and [empty-message chat loading](https://github.com/ollama/ollama/blob/main/docs/api.md). Omitted response fields are reported as NOT_MEASURED.

### Final checks and before/after configuration

| Setting | Before this task | After this task |
|---|---|---|
| Startup Qwen admission | Separate from guest CPU lane | Existing shared app AudioAdmission; skip when busy |
| Startup warm request | Dummy user `ok`, output cap 1, keep_alive 30m | Empty messages, output cap 1, keep_alive 5m |
| Startup socket timeout | 90 s | 90 s, unchanged |
| Smoke readiness | Installed/cold observation without preload gate | Exact tags + ps, one optional 30 s preload, then resident CPU verification |
| Guest NLU / turn budget | 3 s / 8 s | 3 s / 8 s, unchanged |
| NLU model/options/schema | Qwen2.5:3B, ctx4096, predict220, CPU0, full enabled registry | Unchanged |

Final `python -m compileall -q src tools`, `python tools/config/repin_configs.py --check`, and `git diff --check` all completed successfully. Git emitted existing Windows LF/CRLF notices; no whitespace error was reported. Configuration hashes remained current without edits. The source DB hash was rechecked after the final targeted tests and remained identical. No live call was made after the warm timeout; final checks were offline/mock/read-only.
