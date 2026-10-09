# Agent E2E smoke and root-cause report

**Latest readiness/warm validation, 2026-10-09:** explicit preload **PASS** (4.656 s), exact Qwen CPU residency verified; real guest NLU **TIMEOUT / WARM_NLU_TIMEOUT** (HTTP 3.046 s, API 4.281 s), zero business writes. **Two new calls; cumulative 5/5**, stopped without retry. **71 targeted tests DETERMINISTIC_PASS**. No REAL_MODEL_PASS. Startup warm now shares existing SLM admission. Submitted-change confirmation and requested_date remain covered by deterministic regressions. See section 11 and [CPU report](AGENT-CPU-SLM-REPORT.md); sections 1–10 preserve preceding runs, including historical cold timeout and their then-current budgets.

## 1. Repository and scope

HEAD before and after work: `72e749649f764589e3dd671b3d993e9fe79f47a5`, branch `phase4-handoff`.
The initial tree had 31 modified tracked files and four untracked files from the
preceding item/context/grounding work. Those changes were inspected and preserved.
No reset, commit, push, CI change, training, dataset cleanup, or UI redesign was performed.

This report distinguishes real Qwen calls from scripted NLU. Deterministic tests
run the actual FastAPI application, governed LangGraph loop, signed map, current
knowledge sources, confirmation APIs, SQLite workflows, and authenticated staff APIs.
They do not prove that Qwen will propose the same commands.

## 2. Environment and preflight

- Windows, PowerShell, Python 3.12; CPU-only test configuration (`num_gpu=0`).
- Ollama `127.0.0.1:11434` answered `/api/tags` and `/api/ps` without inference.
- Qwen2.5:3B was installed, Q4_K_M, digest
  `357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b`.
- `/api/ps` was initially empty: Qwen and BGE were cold. No model was downloaded.
- API entry points: `POST /api/session`, `POST /api/ask`, prepare/confirm endpoints,
  authenticated `/staff/requests/{id}` and transition endpoints.
- The pinned property profile and signed map release loaded successfully. Data
  audit found 1,480/1,480 BGE-indexed chunks across four languages and 34
  evidence-backed navigation paths. The stored index was inspected, not regenerated.
- Smoke used lexical retrieval, no embedding model, no reranker, no semantic
  generation, no model planner, and no fallback model. This avoids loading BGE
  alongside Qwen, but does not validate live dense retrieval.
- Configured intent timeout: 3.0 seconds; `num_ctx=4096`, `num_predict=220`.
  Actual serialized request size: 20,587 bytes, not a measured token count.
- Client transport supports cancellation before/while consuming a stream and
  closes the response on exit. A timed-out client is not proof that native model
  computation instantly stopped.

All application writes were directed to disposable SQLite copies. The source
`data/concierge.sqlite3` was read for copying/hash and, later, through a read-only
SQLite connection to verify baseline counts. Its SHA-256 stayed:
`3d213efbf50b809b56290743935b5fa5c19ffdf45faa530747961678d32ffad7`.
The source copy already contained four service requests and six proposals;
the real case added **zero** requests and **zero** proposals. Deterministic tests
measure deltas against this baseline and check the source hash after each case.

## 3. Real inference accounting and protocol blocker

**Two Qwen HTTP requests occurred: one automatic startup warm-up and one guest
TC-01 request.** The initial instrumentation counted only the guest command call.
Inspection of lifecycle code and Ollama logs exposed the omitted warm-up:

- `main._build_lifespan` schedules `warm_voice` in the background.
- That function calls `runtime.local_ai.warm_local_slm` when Qwen is configured.
- The warm-up itself posts `/api/chat`, `num_predict=1`, with a 90-second default
  timeout. It is inference, not a free health check.
- Ollama logs show startup taking 5.04 seconds, a 30-token warm-up prompt, and
  `/api/chat` returning HTTP 200 after 5.5981356 seconds.
- TC-01's command transport timed out after 3.016 seconds; its complete API turn
  took 4.797 seconds. Raw model command response was null.

The warm-up and guest request overlapped. **Smoke protocol: BLOCKED** because the
sequential-call requirement was not met. The 5.598-second response belongs to
warm-up and must not be attributed to the guest case. Cold startup/overlapping
warm-up are verified contributors to an unsuitable smoke setup; the trace does
not isolate guest queue time, decoding time, or prove a model understanding error.

The local smoke harness now disables startup warm-up before entering TestClient
lifespan. It was **not rerun**, respecting the stop-after-timeout instruction.
TC-02 through TC-05 were not run against the real model. No real-model PASS or
strictly sequential smoke claim is made. Both HTTP attempts are included in the
five-call ceiling; no further inference followed this discovery.

## 4. TC-01 through TC-19

For TC-02 through TC-05, the deterministic equivalents use English guest wording
with scripted, server-validated commands. TC-01 uses the specified Vietnamese
without accents. These equivalents are not substituted for the required real
Vietnamese smoke results.

| ID | Real model | Deterministic API/workflow result | Evidence / limit |
|---|---|---|---|
| TC-01 | TIMEOUT | PASS | Water/item/unit/quantity/room survive the draft; no towel substitution and zero new tickets. |
| TC-02 | NOT_RUN | PASS | Pool opening-hours citation from current copied DB; accepted turn establishes pool context. |
| TC-03 | NOT_RUN | PASS | Same-session flagged follow-up resolves pool and returns verified signed-map guidance. Existing failed-NLU resolver integrations also pass. |
| TC-04 | NOT_RUN | PASS | Both unsupported sub-questions return unavailable progress, no citations and no golf fact. High-score unrelated-source regressions also pass. |
| TC-05 | NOT_RUN | PASS | Both service and spa read execute; towel payload remains in `proposed_actions`; spa hours have citations; zero new tickets. |
| TC-06 | NOT_RUN | PASS | Wake-up draft preserves room 710 and 06:00; no ticket is queued. |
| TC-07 | NOT_RUN | NOT_IMPLEMENTED for calendar-date preservation | 06:00 → 07:00 correction and room retention PASS. Wake-up contract has time and optional room, but no requested calendar date; “tomorrow” is not durably represented. No date guarantee is claimed. |
| TC-08 | NOT_RUN | PASS | `CheckAvailability` executes read-only; no unwanted booking proposal or business ticket. Does not certify live table inventory. |
| TC-09 | NOT_RUN | PASS after fix | Handoff review and verified spa navigation both survive composition; neither submits a ticket. |
| TC-10 | NOT_RUN | PASS after fix | API-prepared pending proposal remains awaiting confirmation; the compound confirmation/spa question retains the spa citation and creates no ticket. |
| TC-11 | NOT_RUN | PASS | Cancellation becomes `cancel_requested` for staff review; original ticket is still pending. Trace accurately counts the review-change write. |
| TC-12 | NOT_RUN | PASS after fix | Two active tickets require explicit selection; neither is changed. Selection prompt checked in all four languages. Named-reference follow-up targets only the selected session ticket. |
| TC-13 | NOT_RUN | PASS | Travel sewing kit remains the requested item; no catalog-default substitution or inventory promise. |
| TC-14 | NOT_RUN | PASS | A separate session's bare reference cannot reuse the pool map anchor. |
| TC-15 | NOT_RUN | PASS | Pool drowning emergency uses emergency route; NLU invocation is forbidden by the test; no normal service ticket. |
| TC-16 | NOT_RUN | PASS | API confirm repeated returns the same ticket; water journey completes through staff transitions. |
| TC-17 | NOT_RUN | PASS | Advancing the existing memory clock beyond anchor TTL prevents inherited verified route. |
| TC-18 | NOT_RUN | PASS | Injected RuntimeError and TimeoutError from map tool cannot produce verified directions, citations, or a successful business action. |
| TC-19 | NOT_RUN | PASS | Invalid invented service command is rejected; valid spa read remains and returns a citation. |

## 5. Fail traces and root causes

| Failure | First incorrect boundary | Root cause and fix |
|---|---|---|
| TC-01 TIMEOUT / protocol BLOCKED | Startup/transport | Background warm-up escaped the first call counter and overlapped the guest request. Corrected only the smoke harness; no application timeout inflation or inference retry. |
| TC-09 FAIL | `_decision_from_commands` / final contract | Handoff priority selected fast route even with a navigation command. Verified read content then violated fast-path contracts and the final response became abstention. Compound reads now enter `multi_task` before single-command priority. |
| TC-09 latent presentation loss | `compose_multi_result` | Handoff is not a service candidate; composer ignored its review suggestion. An observed, verified `command:Handoff` now projects a confirmation action. Validation requires exact equality with that server observation; a tampering regression rejects invented details. |
| TC-10 FAIL with API prepare | `_decision_from_commands` / final contract | Confirm priority chose fast confirmation despite a valid spa read; evidence was discarded as a contract mismatch. Same compound-route fix preserves the read while API consent remains mandatory. |
| TC-12 FAIL | `ServiceActionService.manage_request_tool` | Without a reference, `active[0]` selected the newest ticket. Multiple active tickets now prompt for a reference before any change write. No model-supplied ID is accepted as authority. |
| TC-11 trace FAIL | `AgentRun.trace` | `business_writes` was a constant zero although the workflow had stored a staff-review change. Receipt counting now admits only verified `manage_request` observations with the expected change/write shape; service drafts cannot claim a commit. Idempotent review replays report zero additional writes. |

Minimal preserved actual traces:

- TC-09 before fix: route `handoff`; `staff_handoff` unavailable after contract
  failure; final answer “I could not find verified information for this question.”
- TC-10 with API prepare before fix: `tool_contract_failed branch=confirmation`,
  no citations, pending proposal still awaiting confirmation.
- TC-12 before fix: two session tickets; unqualified Cancel selected one and
  persisted `cancel_requested` instead of asking which ticket.
- TC-11 before trace repair: `agent_action.business_writes=1` but
  `agent_trace.business_writes=0`.

Some initial failures were harness errors, not agent defects: missing property
name failed pinned identity validation; copied historical tickets invalidated a
zero-total assumption; compound drafts use `proposed_actions[*].payload`; queued
confirm correctly returns HTTP 202, not 200. Those assertions/setup were corrected
without weakening business contracts. Failure artifacts distinguish them.

## 6. Files changed during this task

Modified source:

```text
src/concierge_kiosk/application/conversation/engine.py
src/concierge_kiosk/application/service_actions.py
src/concierge_kiosk/agent/runtime/result.py
src/concierge_kiosk/agent/runtime/execution/models.py
locales/en.json
locales/vi.json
locales/ko.json
locales/zh.json
```

Reasons: compound-route preservation, observed handoff confirmation projection,
unambiguous ticket selection, accurate governed write receipts, and localized
selection prompts. This adds to the existing uncommitted diff; it does not replace it.

Added tracked-intended files:

```text
tests/agent/test_e2e_guest_journeys.py
docs/AGENT-E2E-SMOKE-REPORT.md
```

Local ignored verification artifacts:

```text
reports/run_agent_e2e_smoke.py
reports/agent-e2e-real-smoke.json
reports/agent-e2e-ollama-excerpts.log
reports/agent-e2e-failure-traces.json
reports/agent-e2e-deterministic/*.json
reports/agent-e2e-regressions.xml
reports/agent-e2e-selection-regressions.xml
```

Deleted files: none. No production database migration or production write.

## 7. Verification

| Check | Result |
|---|---|
| 11 selected modules covering journeys, understanding, item/context/RAG boundaries, runtime and security invariants | PASS: 127 tests, `-W error::ResourceWarning`; JUnit saved. This is not full pytest. |
| Four-language ticket selection plus named-ticket/mixed-cancel-read checks | PASS: 6 tests after adding locale coverage; separate JUnit saved. These overlap the broader run. |
| Python compileall | PASS |
| Config hash check | PASS |
| Structural/semantic/provenance/runtime data audit | PASS; no evaluation inference |
| Bandit | PASS, exit 0; existing nosec/comment warnings |
| Diff whitespace | PASS |
| Full pytest, full benchmark, training, stress tests | NOT_RUN, prohibited |

Existing pytest-asyncio fixture-loop and Starlette/httpx deprecation notices remain.
No claim is made about aggregate accuracy, recall, latency, or production readiness.

## 8. Business E2E journey

The API journey is scripted NLU, with all later boundaries real:

1. Guest requests `3 chai nuoc suoi`, room `502`; structured slots are validated
   against guest spans. Draft/review preserve all four fields, with staff
   availability verification wording. New business tickets: 0.
2. Existing UI payload shape is sent to `/api/requests/prepare`. Proposal is
   awaiting confirmation; staff ticket count delta remains 0.
3. Explicit `confirmed=true` reaches `/api/requests/confirm`, returns HTTP 202
   and one pending-staff ticket. Repeating confirmation returns the same ID.
4. Authenticated staff detail shows item, unit, quantity and room unchanged.
5. Staff API transitions: `approve → approved`, `start → in_progress`,
   `complete → completed`. SQLite retains the same payload throughout.
6. Guest status endpoint reflects completed DB state. Another session receives
   403/404 accessing that ticket.

An emergency alert and a staff-reviewed cancel/modify request are distinct valid
workflow writes; they are not guest service-ticket confirmation. The new trace
counter records permitted review-change receipts instead of pretending no write
occurred. It rejects a fabricated service-draft commit observation.

## 9. Risks and remaining limits

- **BLOCKED real smoke validation:** the one guest case timed out and startup
  warm-up overlapped it. Corrected harness has not been executed. No Qwen extraction,
  reference interpretation, compound decomposition, or warm-model behavior is certified.
- **P1 NOT_IMPLEMENTED:** durable requested wake-up date is absent from the
  existing service contract. Time correction is tested; calendar-date support
  requires a separately scoped business/schema decision.
- Canonical queries with no validated facet retain the previous lexical coverage
  gate. This is not general semantic entailment, nor exhaustive arbitrary-aspect
  coverage. Live dense retrieval was not exercised.
- Text HTTP and staff workflow were tested. Browser clicks and real voice/STT/TTS
  were NOT_RUN. Existing UI adapter payload is exercised by the prepare API journey;
  no new browser automation or frontend change was introduced.
- Synthetic operational/staff adapters do not establish real inventory, external
  delivery, live weather, flight prices, or third-party system completion.
- Confirm plus a knowledge read preserves the API confirmation policy; spoken or
  typed affirmation alone cannot queue the prepared service request.
- Source hashes and copied SQLite tests establish local write isolation; they do
  not certify deployed infrastructure or all possible concurrency schedules.

## 10. Stabilization follow-up — 2026-10-09

### Repository and execution boundaries

HEAD/branch remain `72e749649f764589e3dd671b3d993e9fe79f47a5` /
`phase4-handoff`. At the start of this follow-up the tree had 36 modified tracked
files and six untracked files. At delivery it has 43 modified tracked files and
12 untracked files, including earlier work and this follow-up. Existing local
item/context/RAG changes were read and preserved. No reset, stash, revert,
commit, push, CI/dependency change, model download or full pytest was performed.

Required audit documents, CLAUDE.md, AGENT.md and the existing smoke trace/harness
were inspected. All integration writes used disposable SQLite databases or
private copies. Production/source SHA-256 before and after remains
`3d213efbf50b809b56290743935b5fa5c19ffdf45faa530747961678d32ffad7`.

### Root causes and fixes

| Finding | First incorrect boundary | Fix / evidence |
| --- | --- | --- |
| Submitted cancel/modify writes from chat | `ServiceActionService.manage_request_tool` called `request_guest_change` immediately | It now calls read-only `Workflows.review_change`; review has owned ticket, action and changed fields; business writes = 0. New cancellation regression failed before this fix with `cancel_requested`, then passed with `none`. |
| Existing guest `/requests/{id}/change` also committed immediately | Guest route invoked the same domain transition directly | Endpoint now prepares an existing proposal; returns `proposal_id`, `awaiting_confirmation` and confirmation requirement. It does not queue the change. |
| Change review could disappear beside a read | `compose_multi_result` only projected service candidates and handoff | Server-verified Cancel/Modify observations project the exact observed review/target/payload; output tampering is checked against the observation. Cancel + spa read retains both and writes 0. |
| No independent wake-up date | Registry had time but no calendar slot | Optional canonical `requested_date` ISO `YYYY-MM-DD`; property-local clock and profile-owned relative offsets; strict HTTP/domain validation; JSON storage, no SQL migration. |
| Unresolved date could disappear on a later time-only turn | Continuation projected slots but discarded the missing optional-date requirement | Pending `missing` requirements propagate to the governed candidate. Invalid/ambiguous date remains a blocker until supplied, while corrected time is preserved. |
| Retrieved top-k became NLU goal authority | `model_commands` built its allowed goal set solely from shortlist | Shortlist orders candidates; all enabled registry goals remain available, including empty/invalid shortlist fallback. Red/green CPU contract regression. |
| Corrected live request still timed out | Ollama was loading tensors when the three-second client deadline closed the socket | Verified server log: load aborted, HTTP 499 at 3.1925269 s. This is cold-load failure, not demonstrated model semantic error. |

No command/span validation, tool permission, current evidence binding, session
ownership or explicit-confirm boundary was relaxed. Client fields are checked
for sensitive data before server-owned change metadata is added; server ticket
IDs cannot accidentally match the sensitive-data scanner as guest payment data.

### Confirmation contract and compatibility

Submitted request changes now follow:

```text
Guest chat → read-only change review
Guest review → existing prepare/proposals table (no staff queue change)
Explicit /api/requests/confirm → one atomic owned-ticket change + proposal confirmed
Staff review → approved effective overlay or cancellation/rejection
Guest status → current authoritative DB state
```

Normal prepare/confirm service requests retain their lifecycle LangGraph.
Change proposals use the same immutable proposal, nonce, TTL, withdrawal and
confirmation machinery; their atomic DB confirm does not create a second
service ticket or begin another service-lifecycle graph. `orchestration_sync`
is `not_applicable` for this branch; the original staff ticket graph is retained.

`Prepare.change = {request_id, action}` is additive. The existing `/id/change`
response intentionally changes from a committed change receipt to a prepared
proposal. Old clients expecting immediate `change_state` must use the existing
confirm endpoint. Guest frontend buttons and chat suggestions were adapted to
that transport, reuse the existing confirmation card, and guard session changes
while asynchronous prepare completes. This is the required compatibility
change; no UI redesign or second confirmation engine was introduced.

Both cancel and modify were tested through explicit API confirmation, repeated
confirmation, staff approval and status projection. The original ticket remains
`pending_staff` during change review. Approved modifications expose an effective
overlay and preserve the original payload. Replay after staff review reports
the current change state without announcing another submission. A rejected,
expired or foreign-session proposal cannot commit. Two active tickets require
selection; an explicitly named foreign ticket cannot be prepared.

Draft cancellation/edit remains an ephemeral conversation operation; it does
not invoke a submitted-ticket transition.

### Date/time contract

Only `wake_up_call` receives optional `requested_date`, preserving older
time-only tickets. Date evidence is normalized separately from `preferred_time`.
The application supplies a timezone-aware reference time using the configured
property timezone (`Asia/Ho_Chi_Minh` here), not host time or the user's timezone.
Relative vocabulary/offsets live in the checksum-pinned domain profile, with
four-language validation. No exact guest sentence or new training example was
added. Supported absolute form is ISO; configured relative forms include today,
tomorrow and the day after tomorrow and their locale equivalents.

Missing reference clock, invalid calendar date, conflicting absolute/relative
dates and contradictory relative dates require clarification. Unknown grounded
date spans cannot become canonical dates. Date-only corrections preserve time;
time-only corrections preserve date. Date survives draft/review/confirm/persisted
JSON and participates in duplicate-request identity, so different service dates
are not merged. Non-date services do not extract this slot and reject it in
direct prepare. Natural weekday/locale-specific calendar formats beyond this
profile are not implemented. Date interpretation by real Qwen is NOT_RUN.

### A01–A16 acceptance

All statuses below describe **scripted NLU / deterministic adapters**, not real
Qwen understanding.

| ID | Status | Evidence |
| --- | --- | --- |
| A01 | DETERMINISTIC_PASS | Water item, unit, quantity 3, room 502 retained through staff complete. |
| A02 | DETERMINISTIC_PASS | Unknown catalog item preserved; no towel/default substitution or stock promise. |
| A03 | DETERMINISTIC_PASS | Pool source establishes verified anchor; follow-up uses owned current anchor and signed navigation. |
| A04 | DETERMINISTIC_PASS | Missing/foreign anchor abstains; no invented destination. |
| A05 | DETERMINISTIC_PASS | Weather + flight unsupported; no golf claim/citation. |
| A06 | DETERMINISTIC_PASS | Handoff review and navigation both survive. |
| A07 | DETERMINISTIC_PASS | Confirm + spa read keeps citations and API confirmation boundary. |
| A08 | DETERMINISTIC_PASS | Chat and both prepare transports leave original change state `none`, writes 0. |
| A09 | DETERMINISTIC_PASS | Explicit confirm records one staff-review change; staff approval controls effective state. |
| A10 | DETERMINISTIC_PASS | Two tickets require selection; chosen owned ID only. |
| A11 | DETERMINISTIC_PASS | Four-language relative-date tests include Vietnamese morning/tomorrow; date/time independent. |
| A12 | DETERMINISTIC_PASS | Time correction preserves date in API conversation and persisted payload. |
| A13 | DETERMINISTIC_PASS | Expired/withdrawn proposal cannot write; missing date stays a blocker. |
| A14 | DETERMINISTIC_PASS | Emergency bypasses command inference and queues only its authorized emergency workflow. |
| A15 | DETERMINISTIC_PASS | Invalid command rejected individually, valid read retained. |
| A16 | DETERMINISTIC_PASS | Repeated service/change confirmation produces one ticket/change audit receipt; replay reads current staff state. |

### Real smoke and minimal timeout trace

| Case | Status | Actual new HTTP Qwen calls |
| --- | --- | --- |
| LIVE-01 water | TIMEOUT | 1 |
| LIVE-02 pool hours | NOT_RUN | 0; stopped after timeout |
| LIVE-03 same-session navigation | NOT_RUN | 0; stopped after timeout |
| Real multi-intent / weather / date | NOT_RUN | 0; explicitly outside remaining live order |

Anonymized session `A`; input `cho toi 3 chai nuoc suoi phong 502`:
raw model response `null`; structured commands `[]`; no accepted command;
route `knowledge`; goal `knowledge:verified_answer`; deterministic tool
`hotel_info_search` returned `safe_fallback`, verified false; anchors `[]`;
citations `[]`; proposal `null`; business write delta **0**; goal unresolved.
Guest API latency **4.109 s**, command transport **3.000 s**. HTTP payload
**20,069 bytes**, actual message bytes **5,693**, schema **13,927**, offered
services **14**, few-shots **0**, context **4096**, output limit **220**, CPU **0 GPU**,
effective command deadline **3.0 s**. No token count or decoding latency was
received. Timeout/model/context configuration was not increased.

The fallback offers related topic titles; it does not establish water delivery,
availability or a service draft. This is not a real-model PASS. The full
anonymized trace is `reports/agent-stabilization-real-smoke.json`;
[CPU report](AGENT-CPU-SLM-REPORT.md) records log evidence and all inference hooks.

**Accounting:** two earlier HTTP Qwen calls + one new call = **3/5 total**.
New budget used **1/3**. No parallel inference, hidden startup warm-up, retry,
extra planner, reference resolver or background Qwen call occurred in the
corrected run. The shared HTTP boundary was intercepted, not only the smoke's
command wrapper. Subsequent existing Ollama log tail still ends with that call.

### Files changed in this follow-up

Prior item/RAG/memory changes remain in the dirty tree; the following list
identifies additional stabilization work rather than attributing all git diff
to this task.

- `agent/understanding/commands.py`: complete enabled goal coverage and compact
  candidate/user JSON; `tools/runtime/agent_stabilization_smoke.py`: actual HTTP
  accounting, preflight, warm-up suppression, stop-on-timeout and replay guard.
- `application/service_actions.py`, `application/workflow_service.py`,
  `domain/requests/submissions.py`, `api/guest/routes.py`: read-only change review,
  shared proposal discriminator, atomic owned change confirmation and API transport.
- `agent/runtime/result.py`: exact observed change review projection beside reads;
  `runtime/runtime.py`: date tool payload and pending-date requirement propagation.
- `agent/tools/service_dates.py`, `service_slots.py`, `tools/registry.py`,
  `domain/service_registry.py`, `application/conversation/engine.py`,
  `agent/memory/conversation.py`: date normalization, correction and clarification.
- `api/shared/contracts.py`, `config/agent-domain.json`, its schema/hash and
  `core/domain_profile/validate/nlu.py`: additive date/change contracts,
  language-owned date vocabulary and strict profile validation; `.env.example`
  domain checksum refreshed by the official pin tool.
- `frontend/src/{App.tsx,api.ts,types.ts}`, `components/ChatSection.tsx`,
  `hooks/useRequestDraft.ts`, generated API contracts and `web/{guest.js,staff.js}`:
  necessary review/confirm transport and official generated assets.
- Four `locales/*.json`: review labels and honest change confirmation message.
- New regressions: `test_change_confirmation.py`, `test_command_cpu_contract.py`,
  `test_service_dates.py`; existing understanding/journey/command/manage/operations
  tests now assert the new confirmation/item contract with unchanged substantive
  staff, idempotency and ownership acceptance checks.

### Targeted validation and remaining gaps

Actual local pytest runs (overlapping tests; **do not sum as unique coverage**):

| Run / local output | Result |
| --- | --- |
| Item, understanding, E2E journeys, reference, RAG boundaries, registry, initial date/change/CPU regressions — `reports/stabilization-targeted.txt` | 110 passed, 45.77 s |
| Date/change/manage/command/schema/UI/workflow/operations contracts — `reports/stabilization-contracts.txt` | 68 passed, 8.61 s |
| Final date/change/E2E/reference/RAG boundaries — `reports/stabilization-final.txt` | 59 passed, 39.90 s |
| Final owned change/cancel/selection/water journey subset — `reports/stabilization-confirmation-final.txt` | 15 passed, 17 deselected, 16.03 s |
| Final temporal and command/memory/follow-up paths — `reports/stabilization-memory.txt` | 21 passed, 4 existing xfailed, 10.71 s |
| Final temporal persistence, conflicting dates and distinct-date deduplication — `reports/stabilization-temporal-final.txt` | 11 passed, 3.51 s |

Initial expanded contract run exposed stale fixed-towel/shortlist/immediate-write
test expectations and item-less operational fixtures; they were updated to the
current registry and explicit-confirm contract. No failing assertion was removed
to bypass ownership, stock, confirmation or staff-state checks. The availability
ranking test supplies an explicit best-first fake ranking, because its old
two-axis embedder depended on the removed fixed towel catalog binding.

The four existing B2 xfails are tracked in `tests/rebuild_pending.py`: legacy
cross-language, question-mark reference, map-only-anchor and localized route
understanding paths. They remain **NOT_IMPLEMENTED** in that legacy model-free
path, not PASS; scripted verified-anchor tests do not certify those paths or
real Qwen reference interpretation.

`agent_domain.py`, zero-error domain JSON-schema validation, official
`repin_configs.py --check`, Python compileall for `src` and `tools`,
TypeScript compile and official frontend build passed. Source hash stayed intact;
`.github` has no diff. Targeted Bandit emitted one **LOW B101** at the pre-existing
end-of-confirm invariant `assert result is not None`; zero medium/high findings.
Its exit was nonzero for that low finding, so this is not reported as a clean
scanner PASS. Existing pytest deprecation warnings remain.

The water business journey was rerun: draft → review → prepare (no ticket) →
explicit confirm → one persisted owned water ticket → replay same ID → staff
approve → start → complete → guest DB status. Item, unit, quantity and room are
unchanged; another session cannot read the ticket. Change journeys separately
verify the original immutable payload and staff-approved effective overlay.

Browser click automation and real voice/STT/TTS are **NOT_RUN**. API integration,
frontend transport compilation and generated bundles were checked. Live dense
RAG, warm-Qwen throughput, queue timing, decoding speed, real multi-intent/date
recognition and production readiness remain unverified. No inference was added
to investigate these after timeout. Stable real-model service on this CPU with
the existing cold three-second deadline remains unaccepted.

Delivery checks: deterministic item/context/RAG/multi-intent and business journey
are DETERMINISTIC_PASS; reviewed submitted changes and temporal preservation are
DETERMINISTIC_PASS; smoke has no competing startup inference and stayed within
budget; CPU timeout cause is verified. Stable real-model service, real context
navigation and browser automation remain TIMEOUT / NOT_RUN, not achieved claims.

## 11. Readiness, explicit preload and warm guest NLU

This task preserved HEAD `72e749649f764589e3dd671b3d993e9fe79f47a5`, branch `phase4-handoff`, and all local changes. Read-only inference hook audit preceded every new call. The runtime startup warm had bypassed guest `AudioAdmission`; it now shares that lane. A new bounded tags/ps identity check distinguishes MODEL_NOT_LOADED, MODEL_LOADING (this process's tracked preload), MODEL_READY and MODEL_UNAVAILABLE. No guest-per-request production preload was added.

The existing harness now suppresses startup warm/voice loading before lifespan, disables BGE and model planner/fallbacks, checks CPU context/digest/RAM, counts every model POST at the shared transport opener, and permits at most one empty preload followed by one real API guest NLU. It refuses a repeat of the existing recorded output. No NLU timeout/prompt contract was relaxed. Preload has a separate finite 30 s limit and five-minute keep_alive.

| Validation | Status | Actual evidence |
|---|---|---|
| Exact model preload | PASS | Client HTTP 200 / 4.656 s; server 200 / 4.6562673 s; done_reason=load; correct model resident via ps |
| Guest service item NLU, anonymized A | TIMEOUT | `cho toi 3 chai nuoc suoi phong 502`; warm HTTP 3.046 s; API 4.281 s; raw null; no_response; no commands |
| Routing/review after transport failure | TIMEOUT | knowledge safe fallback, hotel_info_search unverified, no evidence/citations/anchor/service review; abstained; item recognition unproven |
| Business writes and source DB | PASS | Zero new service rows; before/after source SHA-256 identical |
| Runtime/readiness/schema/item/review/confirmation/date targeted tests | DETERMINISTIC_PASS | 71 passed, 8.42 s, one existing warning; mocks use no Qwen |
| Additional real context / compound intent / retry | NOT_RUN | Two calls consumed; lifetime budget 5/5 |
| Browser E2E | NOT_RUN | Real API path used TestClient and temporary SQLite, no browser |

Server warm log measured 1,153 actual prompt tokens, zero cached tokens; cancellation released the slot with n_tokens=512. It did not complete prompt evaluation or return tokens/timing metadata. Server status was 500 at 3.0136142 s; client saw TimeoutError before headers. The earlier cold-run 499/tensor-load failure and current warm prompt-processing timeout are distinct. Qwen semantic/schema/slot accuracy cannot be judged from a null response. Byte sizes remain 20,069 total, 13,927 schema, 5,693 message content; all 14 service goals/34 variants/28 per-goal slots, zero examples, ctx4096/predict220/gpu0. No byte-to-token estimate or unsupported decoding throughput is reported.

Call 4: empty preload UTC 02:06:53.726328–02:06:58.383635. Call 5: guest NLU starts UTC 02:07:00.243698. Both client records correlate with the only two new Ollama POST rows. No hidden/background/concurrent Qwen POST occurred; no further inference will run under this exhausted budget. Memory available fell from 3,537,629,184 to 1,574,494,208 bytes; this is a capacity observation, not proof of why warm processing was slow.

Actual edits in this task: `runtime/local_ai.py`, `main.py`, existing `tools/runtime/agent_stabilization_smoke.py`, new `tests/agent/test_slm_readiness.py`, and the two prescribed reports. Earlier business/date/UI changes were preserved, not rewritten. No CI/dependency/model/commit/push changes. Complete hook audit, performance, root-cause classification, limitations and exact tests are in [CPU report](AGENT-CPU-SLM-REPORT.md). [Raw real trace](../reports/agent-warm-nlu-smoke.json) and [test output](../reports/warm-nlu-targeted.txt) retain measured evidence.

The readiness defect is addressed and deterministic contracts still pass. Real warm NLU remains TIMEOUT, semantic acceptance BLOCKED, with no REAL_MODEL_PASS. A viable larger timeout is not established by this incomplete run and the default remains unchanged. Staff business journey and context/RAG results in prior sections retain their deterministic status; this run does not reclassify them as real-model passes.
