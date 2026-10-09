# WP12 — Real Qwen validation and CPU NLU diagnosis

Date: 2026-10-09. **Latest decision: REAL_NLU_FAIL_SEMANTIC.**

## Latest authorized live attempt — 05:30:52–05:31:16 UTC

After the user's second explicit request to try again, available RAM had
recovered to 5,144,571,904 bytes in the lightweight check (CPU snapshot 17%).
The actual harness measured 4,865,028,096 bytes before preload, above the cold
threshold. HEAD was `243de4d5be07ffbdfe2010acd8dd305a41380e6f`; the existing report
edit and empty untracked audit were preserved. Prior blocked attempts consumed
zero POSTs, so the four-call WP12 allowance was intact. A separate output path
preserved both earlier preflight traces rather than overwriting them.

Preload succeeded. GET ps then verified exact pinned Qwen digest, CPU
`size_vram=0`, context 4096, exclusive residency and reported model size
2,164,166,490 bytes. This ps size is not peak process RAM. The actual compact
request used 11,527 payload bytes, 5,385 schema bytes, 5,693 message bytes,
17 alternatives, all 14 goals/28 per-goal slot definitions, zero examples,
temperature 0, predict 220, ctx 4096, gpu 0. No BGE, planner or semantic
generation was loaded/called.

### Actual inference ledger

| WP12 call | Purpose | Client HTTP | Server log | Result |
|---|---|---|---|---|
| 1 | Empty explicit preload | 200; 5.797 s | 200; 5.7720269 s | PASS; done_reason=load, resident verified |
| 2 | LIVE-01 actual FastAPI water NLU | 200; 16.484 s | 200; 16.4786517 s | REAL_NLU_FAIL_SEMANTIC |
| — | LIVE-02 / LIVE-03 | No call | No POST | NOT_RUN; stopped after semantic failure |

**WP12 used 2/4 calls. Historical 5/5 stays separate.** No automatic retry,
background inference, competing request or hidden POST appeared in the shared
client trace or captured Ollama log. The remaining two calls were not used to
try to obtain a PASS. Socket timeout was 20 s; cumulative turn budget 30 s;
production defaults/voice caps were not changed.

### Actual LIVE-01 trace and first incorrect boundary

Guest: `cho toi 3 chai nuoc suoi phong 502`, anonymized session A.
Raw Qwen command response (unchanged):

```json
{"commands":[{"type":"StartGoal","goal":"housekeeping","slots":[],"conditional":false},{"type":"SetPreference","field":"quiet","value":"quiet","evidence":"cho toi 3 chai nuoc suoi phong 502"}]}
```

- JSON parsed successfully. Both commands passed the existing closed-schema
  and server checks; validation outcome `accepted`. No commands were rejected.
- **First incorrect boundary: model goal selection, WRONG_INTENT.** Expected
  amenity_delivery, actual housekeeping. Requested item, quantity and unit were
  absent from the model response (secondary slot fidelity failure). The model
  also proposed quiet although the guest did not state that preference.
- Route: `multi_task`; goal:
  `multi_task:service:housekeeping,command:SetPreference`.
- Tool: `service_request_create`, service_code housekeeping, room 502;
  status confirmation_required. This tool produced a proposal, not a ticket.
  Room 502 was recovered downstream from the guest query, not extracted by Qwen.
- Review payload: `{"room_number":"502"}`, service_code housekeeping.
  It was an incorrect housekeeping review, not a water review or towel
  substitution. No stock availability claim, citation or context anchor.
- Actual guest API: HTTP 200, **17.484 s**. Persisted service request count delta
  **0**; Agent trace business_writes **0**. No guest confirmation was sent.

The original recorded harness labeled the failure SLOT_FIDELITY_FAILURE.
Offline review identified the earlier wrong-goal boundary. The diagnostic
classifier was fixed and a deterministic regression proves WRONG_INTENT takes
precedence over missing slots. The original live trace was preserved rather
than rewritten; its overall REAL_NLU_FAIL_SEMANTIC decision remains correct.

Evidence: [full real request/provider/API/log trace](../reports/wp12-authorized-real-nlu.json),
[console](../reports/wp12-authorized-console.txt).

### Actual CPU performance

| Metric | Historical baseline | WP12 warm compact |
|---|---|---|
| Payload/schema bytes | 20,069 / 13,927 | 11,527 / 5,385 |
| Actual prompt tokens | 1,153 historic | 1,153; cached count 0 |
| Warm first headers/content | TIMEOUT at 3 s | 12.375 s / 12.375 s |
| load_duration | No completed warm metric | 0.0093053 s |
| prompt_eval_duration | NOT_MEASURED | 12.320450 s |
| eval_count / eval_duration | NOT_MEASURED | 60 / 4.115385 s |
| total_duration (provider) | NOT_MEASURED | 16.4786517 s |
| Full client NLU HTTP | Timeout 3.046 s | 16.484 s, completed |
| Full guest API | 4.281 s historic failure | 17.484 s, semantic failure |
| Cold/preload client HTTP | Prior 4.656 s | 5.797 s |
| Preload provider load_duration | NOT_MEASURED | NOT_MEASURED; empty preload omitted duration fields |
| Minimum sampled available RAM | Not peak model RAM | 2,982,043,648 bytes; max sampled system memory load 81% |
| Whole-system CPU over load/inference window | Not comparable | Mean 49.06%, range 8.84–77.78%; not Qwen-only CPU |
| Peak Qwen process RAM | NOT_MEASURED | NOT_MEASURED |

Completed provider timings and server prompt-eval logs show the warm prompt
phase alone took 12.32 s, beyond the historical three-second deadline. Cold
load was completed separately and warm load_duration was only 9.3 ms. This run
therefore establishes a CPU prompt-processing cost that prevents this particular
uncached request from fitting the 3 s policy. It does not isolate grammar
compilation, server queue time or memory paging; those remain NOT_MEASURED.
Smaller schema did not reduce the textual prompt token count in this observation.
No controlled baseline A/B was executed, so no schema-related speedup is claimed.

### Root cause disposition and targeted fixes

| Finding | Responsible boundary | Evidence | Disposition |
|---|---|---|---|
| Wrong service selected | Qwen output before parsing | housekeeping with empty slots | REAL_NLU_FAIL_SEMANTIC; no keyword patch, model-output repair or schema rollback without comparative evidence |
| Unstated enum preference admitted | `commands._preference_value` enum branch + `validate_commands` evidence check | quiet is an allowed enum; entire guest sentence is verbatim evidence but does not entail quiet | Remaining semantic validation gap. Confirmation/unsaved preference policy remains intact; no claim this gap was fixed |
| Warm processing exceeds 3 s | Provider prompt evaluation | 12.320450 s, 1,153 uncached tokens | Keep production defaults; diagnostic completion is not production latency PASS |
| Diagnostic mislabeled wrong goal as missing slots | `real_nlu_diagnostic.classify_failure` | Existing earlier conditional labeled every failed service case SLOT_FIDELITY_FAILURE | Fixed taxonomy and timeout precedence with mocked regressions |

Registry/shape validation cannot independently prove a registered goal matches
guest meaning. Enum preference validation proves membership and verbatim quote,
not semantic entailment. No existing multilingual enum-evidence mapping was
found in the preference contract. Adding a phrase matcher or accepting a model
self-verification would not safely resolve that gap. The wrong goal and quiet
proposal remain unresolved; deterministic PASS is not claimed as a model fix.

Post-live changes: `tools/runtime/real_nlu_diagnostic.py` adds the first-boundary
failure classifier; `tests/agent/test_nlu_diagnostic_protocol.py` adds wrong-goal
and turn-timeout regressions; this report records the actual outcome. No src,
business contract, model, runtime config, CI or frontend change; no commit/push.

Post-live targeted validation: **114 passed**, two existing warnings, **16.02 s**
([output](../reports/wp12-post-live-tests.txt)): protocol, compact schema/validation,
NLU recovery, preference confirmation, readiness, item fidelity, date/time and
request-change confirmation/idempotency. Model HTTP was blocked throughout.
Compileall src/tools, repin_configs --check and git diff --check passed.
Source SQLite SHA-256 stayed
`3d213efbf50b809b56290743935b5fa5c19ffdf45faa530747961678d32ffad7`.

**Acceptance: FAIL semantic, no REAL_MODEL_PASS.** Diagnostic inference completed
but did not understand the requested business operation. Production 3-second
compatibility failed for this observed latency. Real pool knowledge, context
follow-up, dense RAG, voice/browser E2E and aggregate accuracy remain NOT_RUN /
NOT_MEASURED. A larger deadline alone would not fix this semantic failure, so
no production timeout change is proposed from this result.

## Earlier blocked attempts — retained historical evidence

The sections below describe the preceding zero-call preflight attempts and
their then-current BLOCKED status. They are superseded by the actual live
result and 2/4 accounting above, not additional inference calls.

### User-requested recheck, 2026-10-09 05:26:58 UTC

After the explicit request to try again, HEAD was
`243de4d5be07ffbdfe2010acd8dd305a41380e6f` (the prior WP12 work was committed
outside this agent's actions); tracked working tree was clean. Read-only GET
tags/ps again confirmed the same exact Qwen digest and MODEL_NOT_LOADED.
Available RAM was **1,474,760,704 bytes**, memory load **91%**, below the unchanged
cold gate of **3,003,654,256 bytes**. Result remains **BLOCKED**. No preload,
guest API turn, model POST or automatic retry was attempted. Ollama log adds
only two GET entries. WP12 accounting remains **0/4**. The original blocked
trace was preserved; this check is recorded separately in
[recheck evidence](../reports/wp12-recheck-preflight.json).

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
