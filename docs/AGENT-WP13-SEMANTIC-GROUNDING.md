# WP13 — Semantic authorization of structured commands

Date: 2026-10-09. Result: **DETERMINISTIC_PASS for the recorded wrong-command replay**. No new real-model measurement or production readiness claim.

## Environment and preservation

- Actual HEAD: `243de4d5be07ffbdfe2010acd8dd305a41380e6f`; branch: `phase4-handoff`. This differs from the earlier WP12 baseline supplied in the task.
- Windows / Python 3.12; targeted pytest only. No Ollama preload, chat/generate, generative verifier, model download, or dense embedding initialization was performed.
- Existing changes to `docs/AGENT-WP12-REAL-NLU-VALIDATION.md`, `tests/agent/test_nlu_diagnostic_protocol.py`, and `tools/runtime/real_nlu_diagnostic.py`, plus the untracked empty `docs/AGENT-CORRECTNESS-AUDIT.md`, were preserved. They predate this work and are not WP13 fixes.
- API tests use copied/temporary SQLite databases. The source `data/concierge.sqlite3` SHA-256 remains `3d213efbf50b809b56290743935b5fa5c19ffdf45faa530747961678d32ffad7`.
- WP13 Qwen inference accounting: **0 HTTP inference calls**. Test harness forbids the shared HTTP opener, mocks startup warming, and replays `_chat` responses. Readiness unit tests separately mock their transport. Historical 5 calls and WP12's 2 calls are not new WP13 calls.
- No production timeout, model options, CI, datasets, SQL schema, or frontend changes. No commit/push/reset/stash/revert.

## A. Root-cause boundaries

| Finding | First incorrect boundary | Evidence | Fix |
|---|---|---|---|
| Wrong registered housekeeping goal was accepted | Qwen goal selection, then `commands.validate_commands` accepted unsupported meaning | Actual `reports/wp12-authorized-real-nlu.json`; pre-fix regression accepted both commands and failed | Validate service intent evidence after structural/slot validation, before route projection |
| Empty StartGoal could become a review | Registry membership authorized routing; `state.build_initial_state` and slot extraction could recover room 502 without proving housekeeping intent | WP12 housekeeping review; absence of item/quantity/unit in raw output | Reject unsupported goal before candidates/tools; repeat same gate in engine and runtime state construction |
| Real quotation was mistaken for quiet preference evidence | `_preference_value` checked allowed value; quotation containment did not prove preference meaning | Full water request used as quiet evidence | Require pinned field/value meaning in a matching affirmative guest clause, reject negation and superseded preference mentions |
| Invalid meaning could coexist with valid sibling commands | Existing per-item validation had no semantic rejection | WP12 multi-task route contained both incorrect commands | Filter unsupported items individually; preserve independently valid service/AskInfo siblings |
| Closest-service instruction permitted plausible substitutes | Prompt instruction; contribution to Qwen's mistake is **not proven** | Existing prompt said “closest service_mode” despite useful descriptions already being present | Replace with explicit action/description support and prohibition of department/default substitution |

WP12's completed ~16.48 s model response was a semantic failure, not a transport timeout. Prompt evaluation (~12.32 s) belongs to that historical run. WP13 neither remeasures it nor establishes that compact schema caused the wrong intent.

## B. Semantic authorization design

`intent_evidence.command_supported` is a bounded validator for a proposed command, not a replacement NLU router. It never selects a different goal or manufactures a missing slot. Retrieval rank, registry membership, model confidence, enum membership, and the model's context flag supply no intent authority.

The existing checksum-pinned domain profile now owns `semantic_authorization`: localized action concepts, service concepts, reference/conditional markers, negation/question/past markers, preference meanings, object-slot ownership, and the handoff service binding. All 14 registry services and every preference field/value must have complete Vietnamese/English/Chinese/Korean coverage. The profile loader rejects missing/extra services, unsupported goal slots, missing languages, foreign preference values, invalid handoff binding, and terms longer than four space-delimited words. JSON Schema also bounds array sizes and string lengths. These are operator-owned, checksum-pinned concepts; this is not a claim of cryptographic signing or semantic training data.

Service evidence requires an affirmative request action and the proposed service's concept in the same bounded guest clause. Delivery can also be authorized by an explicit delivery action followed by a guest-grounded object slot owned by that service. Room, quantity, and time alone cannot authorize service identity. A delivered object with known conflicting service concepts does not authorize an incidental department/action noun or an unrelated delivery goal. Novel catalog-free objects remain eligible when their delivery relationship is grounded; no stock availability is inferred.

Read-only availability retains its separate contract and does not authorize a reservation. A terminal negative particle in an availability question can be interrogative rather than a cancellation; it remains read-only. Conditional local antecedents bind only within the current guest turn and provide no availability fact.

Reference evidence requires a server-supplied live, owned, revalidated topic plus an explicit guest reference/action. Pending continuation uses the server's current task goal and requested field. A model flag, checkpoint, retrieval candidate, expired anchor, or foreign-session pointer does not establish these inputs. Ambiguous or absent reference authority fails closed. The API does not accept arbitrary `context_topic` authority from guests.

Preferences require the correct field/value meaning in the verbatim evidence and in the relevant affirmative guest clause. More specific enum meanings prevent interpreting vegan wording as vegetarian. The final mention of a preference field controls corrections/negation; an earlier positive clause cannot override a later denial. Integer preferences additionally bind the proposed number to the nearest field mention, rejecting ambiguous associations. Existing guest review/explicit confirmation remains required before persistence.

The same predicate is used at parser validation, conversation command projection, and governed state construction. Direct runtime commands and the legacy semantic-service projection cannot bypass it. Complete rejection produces `unsupported_semantics` / `AMBIGUOUS_INTENT` and the existing localized clarification response, without resolver retry, planner, knowledge/navigation tool, candidate, proposal, or business write. Mixed lists keep valid siblings. Rejection precedes draft clearing; the replay preserves an existing awaiting-confirmation proposal and expected confirmation state.

`require_evidence=False` remains restricted to loading reviewed training fixtures. Retrieval fallback revalidates its proposals against the live guest turn with evidence enabled; training membership is not execution authority.

## C. Before/after recorded WP12 replay

Guest: `cho toi 3 chai nuoc suoi phong 502`.

```json
{"commands":[{"type":"StartGoal","goal":"housekeeping","slots":[],"conditional":false},{"type":"SetPreference","field":"quiet","value":"quiet","evidence":"cho toi 3 chai nuoc suoi phong 502"}]}
```

| Field | Before | After |
|---|---|---|
| Model goal | housekeeping | Raw replay unchanged: housekeeping |
| Semantic authorization | Incorrectly allowed | Rejected: unsupported_semantics |
| Preference quiet | Incorrectly allowed | Rejected: unsupported_semantics |
| Validated commands | Both | Empty |
| Route / response | multi_task / housekeeping review | nlu_failure / existing Vietnamese clarification |
| Wrong service proposal | Created | Prevented; no fabricated water proposal either |
| Business writes | 0 | 0 |
| Existing pending proposal | Not part of original scenario | Existing water payload and awaiting_confirmation status retained |
| Preference persistence | No justified authority | Empty preference memory |

The exact frozen raw response regression **failed before the fix** (`reports/wp13-before.txt`, 1 failed in 1.18 s). The post-fix API replay exercises the real parser, engine, response contract, and temporary SQLite with a mocked `_chat`; it explicitly fails if the governed tool runtime is invoked. Trace: `reports/wp13-replay.json`, including rejection reasons and anonymous session label. This is not REAL_MODEL_PASS and does not claim Qwen now predicts amenity_delivery.

## D. Regression evidence

Final acceptance: **314 passed, 4 existing strict xfailed, 0 failures/errors in 76.48 s**, exit code 0. A separate, disjoint readiness group passed **18 tests**: **332 passing tests across the two acceptance groups**, with the four unresolved xfails reported separately. No full suite or full benchmark was run.

| Check | Result | Evidence |
|---|---|---|
| Recorded replay before fix | FAIL, expected reproduction | `reports/wp13-before.txt` |
| Targeted agent / semantic / profile / business / context / RAG tests | DETERMINISTIC_PASS: 314 passed; 4 existing xfailed; 76.48 s; exit 0 | `reports/wp13-acceptance-isolated.txt` |
| Readiness, preload cancellation, shared-lane accounting | DETERMINISTIC_PASS: 18 passed, 0.91 s | `reports/wp13-readiness.txt` |
| Runtime + compact contract + semantic follow-up group | DETERMINISTIC_PASS: 145 passed, 4.28 s; overlaps main group | `reports/wp13-runtime-contract-final.txt` |
| Domain profile / pin / no-hardcode follow-up | DETERMINISTIC_PASS: 27 passed, 8.46 s; overlaps main group | `reports/wp13-profile-final.txt` |
| Compileall | PASS | Understanding, runtime, engine, domain-profile modules and new semantic tests |
| Domain profile validation | PASS | 4 languages, 14 services, 5 preference fields; profile SHA-256 `3ef5afa773bcff54ad10871a0e225df05f62c0f316313e39128e23df83d27161` |
| Official configuration pin check | PASS | `python tools/config/repin_configs.py --check` |
| Whitespace/diff validation | PASS | `git diff --check` |

The matrix includes S01 replay; S02 amenity and S03 housekeeping positives; S04 quiet review and S05/S06 unsupported meaning; S07 missing item clarification; S08 complete out-of-shortlist coverage; S09/S10 valid siblings; S11 invented slot removal; S12 owned pending reference; S13 expired/foreign context; S14 negation and corrections; S15 multiple-ticket selection; S16 emergency without NLU; S17 change confirmation; S18 natural delivery examples in four languages plus 14×4 domain coverage fixtures; S19 ambiguous wording/information-only service mentions; S20 existing knowledge/evidence boundaries. Symbolic registry coverage fixtures prove gate reachability, not model understanding or natural-language recall accuracy.

Four existing strict xfails in `tests/rebuild_pending.py` concern follow-up/context behavior (B2): cross-language map labels, English “where is it?”, follow-up after map-only answers, and localized routes after hours questions. They are unresolved existing gaps, **not PASS**; no xfail marker or expectation was added for WP13.

Intermediate runs exposed a genuine conditional-antecedent gap and fixtures that supplied unsupported semantics (vegan→vegetarian, a service goal against generic placeholder text, and “for 4 people” without a dining concept). The gate was extended for bounded conditional references; positive fixture inputs now actually express their tested intent, with existing slot/count/confirmation assertions retained and negative counterparts added. The old expectation of reviewing an unsupported diet preference was tightened to rejection, as required by WP13.

Two later overlapping pytest processes collided on the repository's configured `.pytest-tmp`: WinError 183 during setup and “unable to open database file.” Those errored runs are retained (`wp13-preference-final.txt`, `wp13-final-acceptance.txt`) and are not acceptance evidence. The local runner now provides a unique temporary `--basetemp`; the acceptance rerun is sequential. This changes no CI or repository pytest settings.

## E. Changes and offline cost

| Files | Reason / effect |
|---|---|
| `config/agent-domain.json`, `config/agent-domain.schema.json` | Bounded multilingual domain evidence and closed typed contract; no service removed |
| `config/agent-domain.sha256`, `.env.example` | Repin the changed domain profile; no timeout changes |
| `core/domain_profile/models.py`, `loader.py`, `validate/semantic.py` | Expose metadata; enforce complete registry/value/language coverage and bounded concepts |
| `agent/understanding/intent_evidence.py` | Shared pure service/preference semantic predicate with clause/action/slot/context/negation checks |
| `agent/understanding/commands.py` | Gate before projection; expose semantic rejection reasons/outcome; preserve valid siblings and raw numeric preference evidence; concise prompt correction |
| `agent/understanding/routing.py` | Use existing clarification text for invalid/ambiguous meaning |
| `application/conversation/engine.py` | Owned live context/task inputs; gate before clearing pending state, routing, or consequential execution |
| `agent/runtime/state.py`, `runtime.py` | Prevent direct command/legacy route bypass and tools/planner after total rejection |
| `tests/agent/test_semantic_authorization.py` | Exact replay, API pending preservation, all goals/languages, natural paraphrases, conflicting/novel objects, negation/corrections, direct runtime defenses |
| `tests/agent/test_command_semantics.py`, `test_cpu_nlu_contract.py` | Meaningful positive fixtures retain original structural/slot/multi-intent assertions |
| `tests/agent/test_domain_profile.py` | Negative metadata drift tests; newly added services must provide their domain evidence without Python registry changes |
| `tests/agent/test_set_preference_e2e.py`, `test_understanding_layers.py` | Correct semantic fixtures; unsupported preference rejection; explicit positive table evidence with ambiguous negative counterpart |
| `tests/agent/test_no_case_specific_rules.py` | Recognize the new pinned, loader-validated ontology as domain policy; existing phrase budgets and dataset leakage tests remain unchanged |
| This report | Root causes, evidence, boundaries and remaining gaps |

Local ignored evidence/helpers under `reports/`: `build_semantic_authority.py` (one-time metadata construction), `run_wp13_targeted.py`, `run_wp13_readiness.py`, `measure_wp13_offline.py`, replay JSON, offline measurement JSON and pytest logs. Frozen replay tests do not depend on the ignored original WP12 trace being shipped.

Offline same-input payload measurement (`reports/wp13-offline-contract.json`): current compact schema **5,385 B**, request **11,525 B**, messages **5,691 B**, 17 command alternatives, 14 service goals and 28 goal-slot definitions. The alternative per-goal baseline remains 13,927 B / 20,067 B / 34 alternatives under the same current prompt. Historical WP11 had 11,527 B and 5,693 message bytes; the prompt edit reduces two bytes. The full semantic ontology is **not copied into the model prompt**. All service descriptions and slot contracts remain available; candidate shortlist still only orders candidates.

Five batches of ten offline replay parse/validation executions measured median **0.11847 ms** per execution (warmed regex cache), including parsing, structural validation and semantic validation. This bounded Python measurement is not model latency or an accuracy benchmark. Compact schema construction was measured separately by the existing offline comparator; JSON Schema validity does not prove Ollama/Qwen output accuracy.

## F. Remaining limits and decision

- **Semantic safety gate: DETERMINISTIC_PASS within the bounded authoritative evidence contract.** This is compositional lexical/domain evidence, not complete natural-language entailment. Unknown paraphrases, informational mentions, weak references, or contradictory wording can require clarification. Sarcasm, quoted/reported speech, complex negation scope, overlapping service meanings (including tour variants), and arbitrary mixed-language grammar are not established as solved.
- Domain evidence is deployment-owned policy, not learned classification or a second model. Adding a service requires its localized evidence and a new config pin. Existing custom schema-v5 profiles without this required extension now fail validation; deploy profile/schema/pin together. There is no SQL migration or ticket data change.
- **Qwen intent accuracy: NOT_NEWLY_MEASURED.** Wrong raw WP12 output remains wrong; safety rejection is demonstrated, correction to a water goal is not.
- **Real multilingual NLU: NOT_RUN.** Deterministic multilingual fixtures pass only their stated evidence cases.
- **CPU/Qwen latency: NOT_MEASURED in WP13; production deadlines unchanged.** Python gate overhead is measured above. No inference speedup claim.
- **Dense RAG: NOT_RUN. Voice E2E/browser E2E: NOT_RUN.** Existing lexical RAG and API/business regressions do not prove these paths.
- Four strict pre-existing context xfails remain visible. No aggregate Agent accuracy or production-ready claim is made.

Decision: the recorded unsupported commands no longer become guest actions, while authorized requests retain existing clarification, confirmation, session ownership and idempotency boundaries. Future real-model validation needs a separately authorized inference budget; none of WP12's remaining calls was consumed here.
