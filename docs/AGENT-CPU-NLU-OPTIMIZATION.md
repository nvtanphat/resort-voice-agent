# WP11 — CPU command contract and safe NLU recovery

## Scope and repository evidence

This task used **zero real Qwen HTTP calls**. Historical acceptance remains 5/5 calls used, zero real-model NLU passes. No preload, live smoke, model download, training, full benchmark, full pytest, CI change, dependency upgrade, frontend change, commit or push was performed.

The actual starting HEAD was `079a256de93ef5169b6839513608e185028b25b6`, branch `phase4-handoff`, rather than the older supplied baseline `72e7496`. Starting working tree: one modified preference test and three untracked prior reports. During work HEAD advanced externally to `94609df9a3b670707d1d6ddaedf4baa971490b17` (the preference-fixture commit). This agent issued no Git mutation command; the existing modifications and reports were preserved. `CLAUDE.md`, `AGENT.md`, the refactor/E2E/CPU reports, relevant traces and source paths were inspected. `docs/AGENT-CORRECTNESS-AUDIT.md` exists but is empty (0 bytes), so it supplied no audit evidence.

Production/source SQLite stayed unchanged, SHA-256 `3d213efbf50b809b56290743935b5fa5c19ffdf45faa530747961678d32ffad7`. API tests used disposable databases/copies; import-time application composition was redirected to a separate temporary database. Because environment settings take precedence over explicit settings, that import-only DB environment variable was removed before constructing individual test apps.

## A. Root-cause boundaries and pipeline audit

| Finding | Verified evidence | Responsible boundary / change |
|---|---|---|
| Warm NLU missed its deadline | Historical [real trace](../reports/agent-warm-nlu-smoke.json): resident Qwen, 1,153 prompt tokens, cancellation before headers, client 3.046 s, API 4.281 s | CPU/deadline limitation observed in preceding task; no new real measurement |
| Schema contains repeated structures | `commands.command_schema`: 14 StartGoal and five CheckAvailability service alternatives repeat their slot object/array layout | Merge those alternatives while keeping per-command shapes and server validation |
| Timeout lost its identity | `semantic._chat` previously caught OSError/URLError and returned None; model_commands reported no_response | Request-local transport diagnostics distinguish timeout/unavailable/invalid response |
| NLU failure became an unrelated read | `understand_turn` proceeded to fallback/reference recovery and retained provisional knowledge route without verified commands | Explicit failure returns existing deterministic no-tool response path before retrieval/resolver/planner |
| Structurally invalid sibling swallowed valid commands | `commands_from_items` returned None for any bad item or boolean/slot shape | Reject that item only; enforce original list count before pruning |
| Untrusted field types could crash validation | List-valued type/goal could reach set/map membership | Reject invalid scalar/boolean types before registry access |
| Raw string bound could be bypassed with whitespace | `_text` checked normalized length only | Check raw length as well, matching schema bounds |

**Not proven:** schema processing is the cause of the historical timeout; compact grammar improves model latency or accuracy; CPU decoding throughput; a viable replacement timeout. Message content remains unchanged. Reduced HTTP/schema bytes must not be described as reduced prompt tokens or faster prompt evaluation.

Actual flow:

1. `routing.classify_dialogue` and the emergency gate protect safety without Qwen. A knowledge route is provisional, not proof of guest intent.
2. `FastRouter` uses reviewed embedding examples for bounded social/slot/draft-cancel routes; it does not invent service goals.
3. `ServiceSelector.understand` ranks catalog/training candidates and examples. Lazy catalog warming may run outside the SLM lane; no learned embedding/model loading was performed by WP11.
4. `engine.command_for_session` owns admission, one primary command-model attempt, pending-reply and verified-anchor inputs. Readiness/pinning refusal and occupied admission are separate outcomes.
5. `commands.model_commands` rebuilds candidate identity, accepted slots and descriptions from the signed registry. It appends **all enabled goals outside the shortlist**. The shortlist affects ordering, not authority/coverage.
6. `command_schema` constructs output grammar; `semantic._chat` sends the real payload through the existing loopback/no-redirect transport. Offline comparison substitutes only this model adapter and blocks the shared HTTP opener.
7. `commands_from_items` parses individual items, then `validate_commands` applies registry identity, allowed slots, normalized verbatim guest spans, pending-confirmation and preference checks. Required business slots remain enforced by `assess_service` and workflow payload validation: NLU may omit a slot so the Agent asks for it.
8. `_decision_from_commands` remains the sole command-to-route projection. `AgentState`, service candidates and the governed LangGraph loop retain server-owned capabilities, availability gates, session ownership and confirmation authority.

Domain-owned: enabled request kinds/services, accepted/required slots, service tool/department/risk/availability source, preferences, languages, facets, calendar grammar. They were not duplicated or changed. Protocol-owned: command shapes/counts, field bounds and boolean flags. Prompt content already excludes unused catalog authority metadata and includes zero few-shots in the baseline case; no extra schema copy was added to the system prompt. The builder previously called the AskInfo variant helper twice, but transmitted only one variant: that is small Python construction work, not duplicate model prompt text. No caching or broad prompt rewrite was justified by these timings.

## B. Before/after schema — offline measurements

Source: [comparison JSON with both actual payloads](../reports/command-schema-comparison.json), generated by `python tools/nlu/compare_command_schema.py`. Same guest input, registry, messages and inference options in both requests. The script never opens model transport.

| Metric | A: baseline | B: compact |
|---|---:|---:|
| Serialized HTTP payload bytes | 20,069 | 11,527 |
| JSON schema bytes | 13,927 | 5,385 |
| Message content bytes | 5,693 | 5,693 |
| Command schema alternatives, this state | 34 | 17 |
| Enabled service goals | 14 | 14 |
| Per-goal accepted slot definitions retained in prompt/server | 28 | 28 |
| Distinct canonical service slot names | 10 | 10 |
| Python schema construction median, ms per build | 0.036930 | 0.091500 |
| Static JSON Schema validity | PASS | PASS |
| Real Qwen calls | 0 | 0 |

Schema shrank **61.3%**; HTTP payload shrank **42.6%**; schema alternative count halved. Python construction increased about 0.055 ms per build because the compact transform groups/deep-copies two common layouts. Timing is the median of five batches of ten lightweight Python schema constructions, with samples retained in the JSON. It is **not model or API latency** and not an aggregate performance benchmark.

Option B is applied to `model_commands` after comparative/negative regression checks. `command_schema(..., compact=False)` retains the baseline default for comparison and existing callers. The compact transform derives from the same baseline builder, preventing a second schema definition or duplicated validator. It merges only StartGoal and CheckAvailability by type, preserving their different required/optional fields. CheckAvailability retains only the five registry goals with an availability source. It does not merge unrelated command types into a permissive all-fields object. Preference variants keep their own field/value/evidence rules.

The registry-derived goal enum and shared canonical slot enum preserve item, unit, quantity, room, preferred_time and requested_date. **The union is a decoder vocabulary, not goal-specific permission.** Per-goal accepted_slots remain in the unchanged model prompt and the existing server validator. For example, the compact grammar can represent a wake-up quantity; the server removes it because wake-up does not accept quantity. An unstated room is also removed. The goal survives for slot clarification, never for an incomplete business commit.

All baseline shapes remain valid under B, including conditional intent, availability, handoff, pending confirmation, multi-intent, source-span evidence and context flags. The supported protocol has 17 command types; Emergency remains the deterministic safety route and is not offered to Qwen in either schema. All 16 model command types remain available across their applicable pending/context states; an initial turn intentionally omits Confirm/slot-reply commands.

Static checking uses the existing Draft202012Validator and the same object/array/anyOf/const/enum/string/boolean/bounds keywords as the baseline. No new schema feature/dependency was introduced. Ollama accepts a JSON schema in its [structured-output format contract](https://docs.ollama.com/capabilities/structured-outputs); JSON Schema validity and fixture coverage **do not prove** that this installed Ollama/Qwen version compiles or follows the optimized grammar correctly. Live protocol/semantic correctness remains NOT_RUN.

## C. Code changes

| File | Change | Reason | Risk / boundary |
|---|---|---|---|
| `src/concierge_kiosk/agent/understanding/commands.py` | Optional compact transform, runtime selects compact; per-item parse isolation, scalar/boolean/raw-length guards | Reduce repeated output grammar; reject malformed sibling without losing valid clauses | Decoder goal-slot relationship becomes broader; existing registry filter remains authoritative |
| `src/concierge_kiosk/agent/understanding/semantic.py` | Scoped ContextVar diagnostics around unchanged four-argument chat adapter | Preserve timeout/unavailable/malformed/cancelled distinctions | Other semantic callers still receive None on failure; existing paraphrase tests pass |
| `src/concierge_kiosk/application/conversation/engine.py` | Per-turn outcome capture, early nlu_failure; additive RAG/ambiguity classification | Do not infer a new intent from transport failure or start recovery inference | With unavailable NLU, unclassified questions now get explicit recovery instead of implicit lexical knowledge reads |
| `src/concierge_kiosk/agent/understanding/routing.py` | Add failure metadata and localized no-tool response using existing fast path | Bounded clarification/retry instruction without proposal | Additive response fields; no new state machine or retry loop |
| `src/concierge_kiosk/agent/core/tool_contracts.py` | Include nlu_failure in fast-route prohibition on suggested actions | Prevent recovery response from carrying a business CTA | Strengthens existing contract |
| `locales/en.json`, `vi.json`, `zh.json`, `ko.json` | Two backend copy keys, retry/clarify | Appropriate text/voice explanation in every supported language | UTF-8 and response contract checked; no frontend edit |
| `tools/runtime/agent_stabilization_smoke.py` | Diagnostics understand const or enum goals and retain per-goal prompt contracts | Existing inspector must describe compact payload accurately | Live harness not run; no budget/accounting/readiness alteration |
| `tools/nlu/compare_command_schema.py` | Offline actual-payload comparison and bounded Python construction timing | Inspectable reproducible measurements | Model adapter mocked; shared HTTP opener forbidden |
| `tests/agent/test_cpu_nlu_contract.py` | Comparative coverage, negative cases, scoped failure isolation, mocked API/proposal regression | Prove schema/server/recovery invariants | Zero real inference; no production DB |
| `tests/agent/test_commands.py` | Read offered goals from const or enum | Preserve identical goal/order assertion under both formats | Assertion not weakened; baseline closure tests remain |
| `tests/agent/test_reference_fallback.py` | Explicit AskInfo fixture establishes the pool anchor | Unavailable NLU no longer authorizes implicit knowledge read | Original citation/map/explicit-destination/revocation/session-isolation assertions remain; neutral mock abstention is distinct from timeout |
| `tests/hardcode_allowlist.txt` | Move existing protected-numeric-grammar line 39 → 41 | Two new imports moved the existing literal | No added exemption or enlarged allowlist |
| `CLAUDE.md`, this report | Update schema/recovery documentation | Remove stale knowledge-on-model-failure description | Previous audit reports remain untouched |

No service registry, date/time workflow, business transition, API request model, SQL migration, runtime timeout, model option, UI or CI change was needed.

## D. Recovery behavior and tests

| Failure class | Evidence / decision | Result |
|---|---|---|
| MODEL_NOT_READY | Existing readiness/pinning permission gate refuses SLM | No chat, explicit recovery; not a fresh per-request ps observation |
| MODEL_BUSY | Existing app admission refuses another SLM | No competing chat, explicit recovery |
| NLU_TIMEOUT | Socket TimeoutError, wrapped timeout, or expired cumulative turn budget | No knowledge/tool/reference/planner retry; user may retry deliberately |
| NLU_UNAVAILABLE | Missing config/model, connection failure, no usable response or cancellation | No guessed intent, no service proposal |
| INVALID_MODEL_OUTPUT | Malformed output or no commands survive validation | Bounded clarification, no invented replacement command |
| UNSUPPORTED_INTENT | Existing out-of-scope fast route | Policy reply without tools |
| AMBIGUOUS_INTENT | Clarify command or ambiguous map result | Ask for more specific input, no arbitrary destination |
| NO_VERIFIED_EVIDENCE | Validated knowledge/planning intent reaches RAG but lacks evidence | RAG abstention, distinct from NLU recovery |

Partial valid streams keep their commands, including a valid knowledge clause beside an invalid service/confirmation/shape. Invalid service slots/guest-span mismatches are removed by the original registry validator. Original MAX_COMMANDS is checked **before** pruning; MAX_SLOTS and string bounds remain. Model confirmation still needs server pending-confirmation state; chat commands do not acquire DB commit authority.

The public response remains the existing AskResponse, speech plan and fast reply format. `failure_class`, `retryable` and `understanding_outcome` are additive rollout fields permitted by PublicResponse. Retryable is descriptive; no automatic retry is scheduled. Existing pending proposal/checkpoint ownership is preserved; failure does not clear, confirm or queue a service ticket. ContextVars are scoped/reset per attempt/turn, with a regression proving a failed session does not poison a valid command in another session.

Mocked API trace: [cpu-nlu-recovery.json](../reports/cpu-nlu-recovery.json). Anonymized guest asks to change quantity while a water proposal awaits confirmation. Mock header timeout → NLU_TIMEOUT, tool_route=nlu_failure, validated commands empty, sources/citations empty, no tool calls, no suggested action, business writes 0. SQLite proposal remains awaiting_confirmation with water payload; expected reply stays confirm. Speech-plan chunks remain available. This is **DETERMINISTIC_PASS**, not real-model evidence.

| ID | Validation | Result / evidence |
|---|---|---|
| N01 | Every registry-enabled goal | DETERMINISTIC_PASS: schema fixtures and inspector |
| N02 | All command types and state gates | DETERMINISTIC_PASS: baseline/compact comparative shapes; Emergency bypass |
| N03 | Every goal's service-specific slots | DETERMINISTIC_PASS: same fixtures under both schemas and server parsing |
| N04 | Invalid cross-goal slot | DETERMINISTIC_PASS: grammar may admit union name; server removes it |
| N05 | Item/unit/date fidelity | DETERMINISTIC_PASS: comparative fixtures + existing item/date workflow tests |
| N06 | Multi-intent, conditional, handoff/confirm/read/context | DETERMINISTIC_PASS: both schemas, existing Agent composition tests |
| N07 | Service outside shortlist | DETERMINISTIC_PASS: existing CPU contract/readiness coverage and full registry enum |
| N08 | Invalid JSON/envelope/list/field bounds | DETERMINISTIC_PASS: no proposal; original command limit enforced |
| N09 | Forged confirmation/capability | DETERMINISTIC_PASS: rejected command only; valid sibling kept |
| N10 | Header/wrapped/turn timeout | DETERMINISTIC_PASS: nlu_failure, no tools or resolver retry |
| N11 | Missing model/connection unavailable | DETERMINISTIC_PASS: explicit recovery |
| N12 | Model busy | DETERMINISTIC_PASS: admission denies transport |
| N13 | Guest evidence mismatch | DETERMINISTIC_PASS: invented room/item/value not admitted |
| N14 | Emergency | DETERMINISTIC_PASS: command adapter never invoked |
| N15 | Confirmation, idempotency, staff journey | DETERMINISTIC_PASS: existing change-confirmation/E2E suites |
| N16 | Context/RAG/session boundaries | DETERMINISTIC_PASS: final reference regressions, existing answerability tests, semantic paraphrase tests |

Execution evidence:

- Final core/schema/recovery/readiness/reference/hardcode group: **106 passed**, two existing warnings, 10.63 s — [output](../reports/cpu-nlu-targeted.txt).
- Existing compatibility group: **145 passed, one fixture failure**, 89.61 s — [output](../reports/cpu-nlu-regressions.txt). That failure was the pool fixture's implicit knowledge-on-unavailable assumption; its unchanged citation/map/isolation checks passed after explicit initial AskInfo setup in the final 106-test group. No failing production regression remains from this group. The entire group was not redundantly rerun after the one fixture correction.
- Strengthened raw-length/invalid-sibling subset: **13 passed**, 1.38 s — [output](../reports/cpu-nlu-negative.txt); also covered in the final group.
- Existing semantic paraphrase/verifier boundary: **2 passed**, 0.83 s — [output](../reports/cpu-nlu-semantic.txt).

Initial test setup errors were corrected before final checks: a warm-function blanket mock interfered with readiness unit tests; a test property lacked enabled services; a baseline-only const inspector needed enum handling. In the compatibility runner, env-prioritized shared DB produced session rate-limit failures; removing its import-only environment override restored independent app databases. No rate-limit/security policy was changed to pass tests.

All pytest batches blocked the common `_OPENER.open` before any real model connection; tests could replace it only with mocked responses. Import-time main composition used a temporary DB, then the DB env override was removed. Direct command tests additionally forbid model HTTP through an autouse fixture. No smoke script was executed. No full suite or dense-model test was run.

Final offline gates: compileall src/tools PASS; repin_configs --check PASS (no config changes); pinned agent-domain validator PASS (14 services/four languages); canonical schema validator PASS (366 facts, zero schema errors, 83 source artifacts); git diff --check PASS. Existing LF/CRLF notices are not whitespace failures. Production DB hash remained identical.

## E. Latency policy — options, not implemented timeout changes

Current defaults remain: command socket timeout 3 s, cumulative lazy SLM turn budget 8 s, reference resolver min(1.8 s, command timeout), planner 5 s, goal interpreter 2.5 s; voice uses configured lower caps. The socket timeout bounds first headers/idle reads, **not total streaming duration**. Shared budget clips new calls to remaining time and is checked cooperatively during streamed events. Socket blocking can delay a cancellation check until data/timeout; server work may continue briefly after close. Previous server logs demonstrated cancellation, not instantaneous cancellation latency.

| Policy option | UX / CPU / RAM implications | Measurement required before choosing |
|---|---|---|
| Keep 3 s + compact schema + explicit recovery (current) | Preserves responsiveness and caps; may still time out during unchanged textual prefill | Exact installed grammar support, warm TTFT, valid slot output, provider timings |
| Readiness-gated guest entry using existing startup/preload mechanisms | Separates cold load from guest deadline; bounded RAM retention; app admission prevents startup overlap | Actual residency, expiry behavior, cold duration and available memory; no per-guest preload loop |
| A separately justified finite text-NLU deadline within the turn budget | More chance of completion but slower text response and longer CPU occupation | Completed warm runs separating prompt/schema/decode/queue; no default change in WP11 |
| Preserve stricter voice cap and deterministic fast path | Emergency/static replies stay independent; voice can recover promptly | Voice TTFT, interruption, actual cancellation, STT/TTS contention under a new budget |
| A tighter end-to-end cancellation boundary | Potentially clearer turn deadline; native work cancellation may lag | Streaming/socket interaction and server stop latency; requires bounded adapter design rather than assuming socket timeout is total duration |

Fast path remains safety/static/verified deterministic routing. SLM path proposes structured commands within existing admission/time budgets. Failure path uses the same deterministic response machinery and never claims tool execution or authorizes business writes. No second fallback Agent or parallel state machine was introduced.

## F. Remaining gaps

- Real-model optimized NLU: **NOT_RUN**.
- Warm model latency for compact schema: **NOT_MEASURED**.
- Optimized prompt token count, grammar compilation/prompt/decode/queue timings: **NOT_MEASURED**.
- Aggregate intent/slot accuracy: **NOT_MEASURED**.
- Live dense RAG: **NOT_RUN**.
- Voice/browser E2E: **NOT_RUN**; API/speech-plan adapter compatibility is deterministic only.
- Model may emit more wrong goal-slot pairs under the shared vocabulary; the server still drops them, but the semantic quality trade-off needs real validation.
- Cold production residency is not newly polled for every guest. MODEL_NOT_READY denotes the existing readiness/pinning refusal; app admission/readiness and finite keep_alive behavior from the preceding stabilization remain intact.
- Output cap 220 can still truncate long compound commands; it was not increased without measurements.

## G. Future controlled validation — proposed, NOT EXECUTED

Only with a newly explicit inference budget: inspect configured exact digest, RAM and ps without inference; suppress startup/embedding/planner hooks and install accounting at the shared HTTP opener. If cold, spend at most one bounded explicit preload and confirm CPU residency. Then spend one real guest API command attempt for water/item/unit/quantity/room, with existing time/options and copied SQLite. Capture raw output, validated/dropped slots, review, zero pre-confirm writes, TTFT/HTTP/API timing and provider metadata actually returned. Stop on timeout, protocol mismatch, competing call or low memory; no retry. Preload consumes budget; a resident model saves that call. Current historical accounting and refusal-to-repeat guard must be reconciled with the newly authorized budget before any future execution.

That short run can establish a case-specific REAL_MODEL_PASS or optimized WARM_NLU_TIMEOUT. It cannot establish general accuracy or latency improvement against A. An optional A/B pair would require separately authorized extra calls and disclosure of order/prompt-cache bias. No additional inference was performed in WP11.
