# WP12 — Real Qwen validation and CPU NLU diagnosis

Date: 2026-10-09. **Decision: BLOCKED (RAM preflight).**

Compact-schema real NLU was **NOT_RUN**. No preload or guest inference was
sent. WP12 used **0/4 new HTTP Qwen calls**; historical **5/5** remains closed.
No real-model accuracy or latency improvement is claimed.

## A. Environment and safety preflight

- Branch `phase4-handoff`; starting and final HEAD
  `4c6b9117f9abea591450d6dd4e37c559b524a750`.
- Initial working tree: only untracked `docs/AGENT-CORRECTNESS-AUDIT.md`
  (empty file). It was preserved. No initial tracked diff.
- Windows, Python 3.12; AMD Ryzen 5 5600H with Radeon Graphics; CPU-only policy.
- Ollama loopback `http://127.0.0.1:11434`. GET tags and ps succeeded.
- Installed model `qwen2.5:3b`, Q4_K_M, parameter size reported as 3.1B.
  Exact historical operator pin matched installed digest:
  `357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b`.
- GET ps returned `[]`: **MODEL_NOT_LOADED**, not MODEL_READY. No resident
  BGE or competing runner was observed. No Python worker was present at the
  initial process inspection. This is a point-in-time observation, not a lock
  against another external application using Ollama later.
- Total physical memory: **16,520,167,424 bytes**. Initial OS inspection showed
  4,474,252 KiB available. The consequential in-harness check subsequently
  measured only **2,356,731,904 bytes available, memory load 85%**.
- Existing smoke safety rule requires installed model bytes plus 1 GiB
  headroom when cold: **1,929,912,432 + 1,073,741,824 = 3,003,654,256 bytes**.
  The measured deficit was **646,922,352 bytes**. The harness stopped before
  preload. This conservative capacity gate is not proof that Ollama could
  never load with less RAM; it is proof the prescribed safe preflight failed.
- CPU snapshots were 33% initially, 16% immediately before execution, and
  11% in the subsequent inspection. They are whole-system snapshots, not
  Qwen CPU measurements. The short blocked trace's CPU sampler had only an
  initial sample, so in-run CPU utilization and peak model RAM are NOT_MEASURED.
- Later OS/process inspection still showed pressure (roughly 2.5 GB free) with
  memory compression, IDE/language-server, browser and other applications.
  No process was killed. These observations do not establish which process
  caused the change in free RAM.

Evidence: [initial identity GET trace](../reports/wp12-preflight.json),
[blocked harness trace](../reports/wp12-real-nlu.json),
[later memory](../reports/wp12-memory-after.json),
[CPU snapshot](../reports/wp12-cpu-after.json),
[process snapshot](../reports/wp12-processes-after.json).

### Inference hook audit

| Component / actual caller | Shared boundary | Automatic / manual | Deadline / isolation in this harness |
|---|---|---|---|
| Startup `main.lifespan.warm_voice` → `local_ai.warm_local_slm` | local_chat_open → `_OPENER.open`, /api/chat | Background | Patched to no-op before import/lifespan; existing runtime admission remains intact |
| Explicit `local_ai.preload_local_slm` | Same /api/chat opener | Manual | At most one, 30 s, empty messages, predict 1, ctx 4096, gpu 0, keep_alive 5m; NOT_RUN |
| `commands.model_commands` → `semantic._chat` | Same /api/chat opener | Guest | One actual command request per turn; diagnostic socket 20 s, cumulative turn 30 s; NOT_RUN |
| Engine model action planner / next-action / goal interpreter | `semantic._chat`, same opener | Conditional | `agent_planner_enabled=False`, disabling all three |
| Reference resolver `_reference_proposal` | `semantic._chat`, same opener | Conditional follow-up | Existing min(1.8 s, intent timeout); at most one extra actual reference selection in LIVE-03 if budget remains; never after timeout |
| Semantic answer generation / independent semantic verifier | `semantic._chat`, same opener | Conditional | Disabled, verifier unset; no second semantic pass |
| Grounded answer phrasing `orchestration.grounding.grounded_response` | Same /api/chat opener | Conditional | Semantic generation disabled; lexical/extractive evidence path retained |
| Bootstrap embedding warm-up, selector warm, emergency classifier embedding | /api/embed, shared opener if configured | Automatic | Embedder/manifest empty; semantic understanding disabled; no BGE loading |
| Voice/STT/TTS startup | Local voice adapters | Background | `warm_voice_models` no-op; no real speech work |
| Readiness / pin inspection | Separate loopback http.client GET /api/tags and /api/ps | Manual/composition | Finite 1.5 s; not inference |

Source audit found three constructors of /api/chat requests: semantic adapter,
grounded phrasing adapter and explicit preload. No /api/generate execution path
was found under src. The transport rejects redirects and non-loopback endpoints.
The harness installs its accounting at the actual shared `_OPENER.open`, not at
the guest script alone, and denies unexpected endpoints/models/GPU settings.
Admission is held until the entire response closes. All automatic model hooks
are suppressed or explicitly budgeted; implicit retry is denied.

### Exact diagnostic policy

Production settings and model payload contracts were not modified. Settings
validation accepts intent timeouts only through 10 s and `model_commands` also
caps its adapter argument at 10 s. The isolated harness therefore uses validated
Settings intent=10, turn=30 and a scoped wrapper that calls the **actual** `_chat`
with timeout=20, forwarding the exact payload and cancellation callback. It
does not generate, repair or replace model output. This wrapper bypasses the
ten-second adapter cap only for this diagnostic process. Default intent=3 and
turn=8 remain unchanged; runtime profiles can override those defaults.

The payload remains temperature 0, num_ctx 4096, num_predict 220, num_gpu 0,
streaming structured JSON schema, keep_alive 5m. Socket timeout bounds first
headers/idle reads, not the full stream. The existing lazy turn budget clips
calls and cooperatively checks streamed events; a blocked read can delay the
check until its socket timeout. A finite 150 s process watchdog ends a stuck
diagnostic without retry. Its expiry would be BLOCKED, never a success.

## B. Inference accounting

| Budget / call | Purpose | Model | Duration | Result | Count |
|---|---|---|---|---|---|
| Historical closed budget | Previous stabilization | Qwen2.5:3B | Historical report | 5/5 used; no REAL_MODEL_PASS | 5 historical |
| WP12 optional preload | Load cold pinned model | Qwen2.5:3B | NOT_MEASURED | NOT_RUN: RAM gate failed first | 0 |
| WP12 LIVE-01 | Water guest NLU | Qwen2.5:3B | NOT_MEASURED | NOT_RUN | 0 |
| WP12 LIVE-02 | Pool knowledge | Qwen2.5:3B | NOT_MEASURED | NOT_RUN | 0 |
| WP12 LIVE-03 / resolver | Same-session navigation | Qwen2.5:3B | NOT_MEASURED | NOT_RUN | 0 |

Shared-client trace contains `calls: []`, `protocol_errors: []` and
`new_qwen_http_calls: 0`. Ollama log captured over the actual run contains only
GET tags and ps, **no POST**. No automatic retry or second live attempt was
made after the RAM stop. Older unrelated 404 POST log lines predate this run
and are not evidence of a successful Qwen inference or WP12 usage.

## C. Live scenarios

| Case | Real model | Commands / validation | Agent result | Business writes |
|---|---|---|---|---|
| LIVE-01 water | NOT_RUN | NOT_RUN | NOT_RUN: resource gate | NOT_RUN; no guest API invocation |
| LIVE-02 pool hours | NOT_RUN | NOT_RUN | NOT_RUN: stopped before LIVE-01 | NOT_RUN |
| LIVE-03 pool navigation | NOT_RUN | NOT_RUN | NOT_RUN: no verified live anchor established | NOT_RUN |

No raw model response exists because no model request was sent. There is no
service review, tool execution, source-span, citation or guest API latency to
report as measured. Deterministic fixtures below do not replace these cases.

The source SQLite hash before/after was identical:
`3d213efbf50b809b56290743935b5fa5c19ffdf45faa530747961678d32ffad7`.
Import-time application composition used a temporary DB, and the intended guest
store was a separate temporary copy. Env precedence was explicitly handled and
the actual configured DB path checked. No production SQLite was opened for
test writes. Startup voice/Qwen warming was suppressed before app import.

## D. Performance

| Metric | Historic baseline | WP12 compact |
|---|---|---|
| Schema bytes | 13,927 | 5,385, measured OFFLINE |
| HTTP payload bytes | 20,069 | 11,527, measured OFFLINE |
| Message bytes | 5,693 | 5,693, measured OFFLINE |
| Command alternatives / goals / per-goal slots | 34 / 14 / 28 | 17 / 14 / 28, OFFLINE |
| Actual prompt tokens | 1,153 historic log | NOT_MEASURED |
| Preload/load duration | Prior preload succeeded | NOT_MEASURED |
| Warm first header / first content | TIMEOUT at 3 s | NOT_MEASURED |
| Full structured NLU completion | NOT_MEASURED | NOT_MEASURED |
| prompt_eval_count / duration | No completed provider metrics | NOT_MEASURED |
| eval_count / duration / total_duration | No completed provider metrics | NOT_MEASURED |
| Full guest API latency | Historic 4.281 s | NOT_MEASURED |
| Valid compact real command output | None | NOT_RUN |
| Peak Qwen RAM / in-run CPU | Not established | NOT_MEASURED; no model run |

[Offline comparison](../reports/wp12-schema-offline.json) reconstructs actual
baseline and compact requests with blocked HTTP and validates both JSON schemas.
Bytes remain as WP11. Bytes are not token counts. No tokens/s were inferred
from cancellation counters. No A/B model execution occurred; no inference
speedup is established by this task.

## E. Root causes and boundaries

| Finding | First boundary | Verified evidence | Fix / disposition |
|---|---|---|---|
| WP12 could not enter preload | Resource/readiness gate | Available 2,356,731,904 < required 3,003,654,256 bytes; no POST | BLOCKED. Preserve headroom policy; stop without retry or killing other apps |
| Qwen identity correct but cold | Residency | tags exact digest / Q4_K_M; ps empty | Preload would be required; NOT_RUN due RAM |
| Diagnostic config=20 cannot pass normal Settings validation | Settings / command adapter | Settings upper bound 10; model_commands cap 10 | Isolated adapter wrapper with actual transport timeout 20; production untouched; NOT exercised live |
| Historic cold timeout | Load vs client deadline | Earlier tensor load/cancellation logs | Remains verified historical cold failure; not reclassified as semantic failure |
| Historic warm timeout | Warm processing vs 3 s deadline | Earlier resident runner canceled before headers | Still unresolved for compact schema |
| Schema/prefill/decoding overhead causes timeout | Provider processing | No completed WP12 provider metadata | Hypothesis only; no production optimization or rollback |

No model semantic or Agent workflow regression was observed because the live
path was never entered. RAM preflight failure does not diagnose the historical
CPU warm timeout. It does not prove compact grammar incompatible with Ollama.

## F. Exact code changes

| File | Change | Reason / risk |
|---|---|---|
| `tools/runtime/agent_stabilization_smoke.py` | Optional max_calls, allowed_phases and narrowly permitted repeat callback in CallBoundary; defaults retain original two-call protocol | Reuse existing shared-opener instrumentation for independent WP12 budget; response-lifetime lock remains |
| `tools/runtime/real_nlu_diagnostic.py` | Isolated four-call diagnostic harness, copied DB, exact pin/RAM gate, background suppression, actual adapter override, telemetry, raw/parsed/validated command and API tracing, stop and replay guard | Enables controlled future acceptance; current run stopped before any inference. Not a production timeout change |
| `tests/agent/test_nlu_diagnostic_protocol.py` | Four-call accounting, duplicate/hidden-call denial, one optional reference selection, acceptance rejects wrong item/unit/quantity/room or absent validated goal | Deterministic protocol checks only |
| `docs/AGENT-WP12-REAL-NLU-VALIDATION.md` | This report | Honest BLOCKED decision and evidence links |

Generated evidence is under `reports/wp12-*`; existing historical traces and
uncommitted empty audit were preserved. No src, business workflow, frontend,
model, runtime profile, locale, CI or dependency changes. No commit/push.

## G. Targeted regression results

All transports were mocked/blocked during pytest. Temporary import-time DB
override was removed before per-test app construction so fixtures retained
independent databases. No full pytest, benchmark or dense-model run occurred.

| Run | Actual result | Evidence |
|---|---|---|
| Compact schema / all goals / parsing / negative validation / failure recovery / readiness + initial two protocol tests | **91 passed**, 2 warnings, 2.14 s | [output](../reports/wp12-targeted.txt) |
| Final three protocol tests + readiness / item fidelity / date-time / change confirmation-idempotency / context / RAG / command loop | **69 passed**, 2 warnings, 12.37 s | [output](../reports/wp12-contract-regressions.txt) |
| Baseline vs compact offline schema validation | PASS_OFFLINE_JSON_SCHEMA, zero HTTP | [comparison](../reports/wp12-schema-offline.json) |
| compileall src/tools | PASS | Actual command exit 0 |
| repin_configs --check | PASS | configuration hashes are up to date |
| git diff --check | PASS | Only Windows LF/CRLF advisory |

Counts overlap and must not be summed as unique coverage. Existing pytest
asyncio config, imported-anyio and Starlette/httpx deprecation warnings remain;
they were not hidden or fixed through unrelated dependency changes.

## H. Decision and remaining work

**BLOCKED.** Exact model identity was verified; residency was cold and safe
available-RAM headroom failed. Consequently preload, real compact NLU,
knowledge and same-session signed navigation are **NOT_RUN**. Diagnostic
completion, production 3-second compatibility, actual API latency and voice
responsiveness are unaccepted. All executed targeted regressions passed.

No production text-NLU deadline can be proposed from a non-executed run.
Default voice caps and deterministic safety paths are retained. Compact schema
remains deployed based on WP11 deterministic evidence, with real quality and
latency still unverified. Live dense RAG, voice/browser E2E, aggregate accuracy
and optimized provider timings remain NOT_RUN / NOT_MEASURED.

After resources are freed deliberately, a separately initiated controlled run
can use the unused WP12 allowance only with explicit accounting carried forward:
read-only identity/RAM/ps, optional one preload, LIVE-01, then LIVE-02/03 only
after real PASS and within four total POSTs. The replay guard intentionally
refuses this recorded output path; resolving that guard must preserve this
blocked trace and the same budget ledger. Nothing in this report authorizes an
automatic retry. A success beyond 3 s must be DIAGNOSTIC_ONLY; changing production
policy would require measured provider timings and a separate acceptance decision.
