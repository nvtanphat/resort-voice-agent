# WP16 — Router correctness & semantic coverage

Audit date: 2026-10-09. Branch `phase4-handoff`; inspected/final HEAD
`35a9ad1634c8c6a527f92022a0d91bbf3cd01650`, exactly the WP15 baseline. Changes remain
uncommitted. Initial untracked `docs/AGENT-CORRECTNESS-AUDIT.md` is preserved and excluded
from this work. Read CLAUDE.md, AGENT.md, WP12, WP13, WP14 observability, WP14 Cloud
acceptance and WP15 audit. No reset/stash/revert, CI edits, commit or push.

Result: two proven policy gaps fixed; original four B2 tests pass with contract-correct
scripted commands; two bounded Tier1 grammar/negation fixes; dataset expectations remain
in a review queue. This is offline correctness evidence, **not production-ready acceptance**.

## A. Root causes and evidence baseline

Before changing source, reused `tools/evaluation/audit_router_evidence.py` on all seven
WP15 datasets. `reports/wp16/before.json` reproduces WP15 counts exactly. Each original
B2 test was then run separately with `--runxfail`, producing four actual failures.
Labels, sample eligibility, slot-evidence rule and dataset SHA-256 stay identical in
`after-final.json`. No new evaluator, training examples or evaluation datasets.

Detailed case evidence: `reports/wp16/review-evidence.json`, including provenance, actual
slots, declared context and decisions. None of the oracle commands are Qwen predictions.
No server context was supplied in corpus probes; required-slot completeness is checked
later by existing service workflows, independently of goal grounding.

| Case / language | Expected goal / provenance | Supplied evidence | Gate before → after | First incorrect boundary / adjudication / fix |
| --- | --- | --- | --- | --- |
| CORE-VI-SERVICE-02 / vi | housekeeping; GOLD independent_core_benchmark, label_source independent_llm_generation | No slots/context; explicit housekeeping request | Reject → Accept | Domain concept coverage; proven WP15 gap, loanword also in existing GOLD training; add bounded loanword concept |
| NAT-0316 / vi | maintenance; synthetic_curated_behavioral_oracle | No slots/context; TV + lost signal + support/check request | Reject → Accept | Concept/action clause relationship; existing GOLD TV-signal examples corroborate; bounded device/symptom/request rule |
| HOLD-EN-04-03 / en | amenity_delivery; guest_intent_plus_verified_service_catalog | room_number=418 only; requested_item absent; no context | Reject → Reject | Missing oracle slot evidence, not established policy failure; do not broaden amenity authority |
| DEV-E3EC45F57A03 / vi | human_assistance; GOLD natural_challenge_curated, source NAT-0522 | No slots/context; wake-up wording | Reject → Reject | Label meaning mismatch; NEEDS_ADJUDICATION; no relabel or authority expansion |
| NAT-0089 / vi | housekeeping; synthetic curated oracle | No slots/context; scheduled housekeeping after DND period | Reject → Accept | Same reviewed loanword gap; acceptance grants proposal identity, not DND override or dispatch |
| NAT-0058 / vi | housekeeping; synthetic curated oracle | No slots/context; dirty bathroom and explicit repeat housekeeping request | Reject → Accept | Same reviewed loanword gap; completion of previous task does not negate new explicit request |
| WP12 frozen replay / vi | Expected amenity; historical raw model housekeeping + quiet | Exact frozen response and utterance; no replacement inference | 0/2 accepted → 0/2 accepted | First wrong boundary remains historical Qwen proposal; semantic safety rejects both |

Classification boundaries: context-dependent rows without owned live anchors are
unresolved context probes, not measured false rejections. Unsupported/invented goals and
preferences in protected regressions are correct safety rejections. Other unexplained
oracle rejections remain unadjudicated; no automatic verdict assigns all 228 remaining
natural rejections to policy defects. The **two reviewed policy-gap cases** both changed
from rejected to accepted; this does not establish a real-model false-rejection rate.

## B. Semantic coverage changes and policy risks

All authorizing vocabulary remains centrally owned by the schema-validated pinned domain:

* Vietnamese housekeeping adds the already approved English loanword `housekeeping`.
  Existing GOLD sources: TRAIN-642F4501B37A / GOLD-CLEAN_NOW-VI and
  TRAIN-6BABA7595B28 / SCN-HOSP-VI-04-08. No frozen sentence copied into policy.
* Optional per-service `symptom_requests`: Vietnamese maintenance binds objects `TV`,
  `tivi`, symptom `mất tín hiệu`, and existing request actions `kiểm tra`, `hỗ trợ`.
  Sources: TRAIN-388670A6ACC7 / SCN-HOSP-VI-10-08 and TRAIN-HUM-A42F15E9FBA8,
  both already marked GOLD in checked-in training. Their write-expectation annotations
  do not grant write authority. The rule requires device and symptom within an 80-character
  gap, in one bounded sentence and with an explicit request in that sentence or the one
  immediately following comma clause. More than two comma clauses, cross-sentence carry,
  conditional speech, negation, past/question evidence, delivery or competing service
  concepts in the request cannot use this rule. Bare symptom/device alone cannot authorize.
  No service-specific branch/goal literal is added to Python.
* Structural quote masking runs before clause grounding. ASCII paired quotes and Unicode
  opening/closing quotation punctuation cannot supply service or preference evidence;
  contractions remain text, unfinished quotation stays masked. English/Vietnamese
  `reported_speech_terms` are conservative exclusions, not authorizing translations.
  Vietnamese completed-action exclusions add `đã dọn`, `đã sửa`, `đã hoàn thành`.
* Schema validates the optional mappings and required object/symptom/action arrays;
  semantic validation restricts locales and actions to approved request actions.
  Valid, omitted, missing nested field, wrong type, unknown locale and unapproved action
  cases are tested. Existing 14 goals retain full concept coverage in all four languages.

Authorizing symptom metadata is enabled **only for Vietnamese**. No invented translations
are installed for EN/ZH/KO; extending this new symptom concept there is NEEDS_REVIEW.
Existing approved maintenance/housekeeping/delivery behavior and quote exclusions have
four-language regression coverage. Risk: local symptom binding can misassociate an object
or broaden action authority; negative device, clause, competing-goal, information, quote,
past, conditional and negation cases are tested, while uncertain cases continue clarification.
This finite matrix cannot prove absence of all unsupported acceptance.

Domain SHA-256 after repinning:
`8281b4a94b506c8d6a93a762f9e51b3d004ca5306c08d5c535fce7782486c5b0`.
Both `config/agent-domain.sha256` and `.env.example` updated via repo repin tool; generated
runtime/property/map/planning releases, model digests/timeouts and policy guest_confirm_all
are unchanged. Profile load and all configuration pins pass.

## C. Emergency safety

Tier1 still precedes selector/Qwen/learned Tier2. Tier2 remains logistic BGE-based with
current full/review thresholds 0.64/0.11 and l2=3e-5; no calibration change or BGE load.

Proven fixes: `scenario_ko_27` uses existing missing-child meaning with the optional
Korean particle `가` in `수가 없어요`; permit that particle in existing bounded patterns.
`CORE-VI-SAFETY_ESCALATION-01` says no smoke yet; local `chưa`/`không` immediately before
the smoke-observation verb now suppress only that match. Active smoke and a separate
sparking incident still trigger. No global negation rule hides emergency evidence.

| Suite | N / emergency labels | Before TP / FN / FP / TN | After TP / FN / FP / TN |
| --- | --- | --- | --- |
| Core | 280 / 20 | 9 / 11 / 2 / 258 | 9 / 11 / 1 / 259 |
| Production scenarios | 270 / 21 | 16 / 5 / 7 / 242 | 17 / 4 / 7 / 242 |

These are **Tier1 predictions against existing labels**. The hazard-related FP column
includes dataset/policy disagreements: it is not an adjudicated unsafe-escalation rate.
Tier2 learned performance and end-to-end emergency recall remain NOT_MEASURED.
The two changed cases are confirmed deterministic boundary corrections. Remaining rows
below require domain/safety adjudication or current Tier2 evidence; do not suppress explicit
electrical hazards merely to match low-severity dataset labels.

| ID / language | Expected → actual Tier1 | Classification / first boundary / disposition |
| --- | --- | --- |
| CORE-VI-EMERGENCY-01 / vi | emergency → non-emergency | Collapsed person, not answering; explicit urgent incident, VI coverage gap; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-05 / vi | emergency → non-emergency | Active seizure; explicit urgent incident, VI coverage gap; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-06 / vi | emergency → non-emergency | Visible flames rising from bin; explicit incident, VI coverage gap; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-08 / vi | emergency → non-emergency | Person fell into water and not resurfacing; urgent incident, VI coverage gap; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-09 / vi | emergency → non-emergency | Cries for help/breaking objects; ambiguous mechanism, safety review; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-13 / vi | emergency → non-emergency | Suspected person being electrocuted; safety coverage/review; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-14 / vi | emergency → non-emergency | Sudden speech/facial symptoms; emergency label, medical policy review; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-15 / vi | emergency → non-emergency | Fall/unable to stand; severity review needed; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-16 / vi | emergency → non-emergency | Severe allergic reaction/rapid swelling; medical policy review; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-17 / vi | emergency → non-emergency | Immobile person; emergency label, severity/context review; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-EMERGENCY-18 / vi | emergency → non-emergency | Rapid flooding and trapped person; explicit incident, VI coverage gap; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| CORE-VI-SAFETY_ESCALATION-15 / vi | non-emergency → emergency | Burnt electrical smell, unplugged; residual-risk/severity label review; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| scenario_vi_27 / vi | emergency → non-emergency | Child missing paraphrase; VI grammar coverage review; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| vi_missing / vi | emergency → non-emergency | Child cannot be located; VI grammar coverage review; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| scenario_vi_28 / vi | emergency → non-emergency | Collapsed/unresponsive guest; VI safety concept review; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| en_spark / en | non-emergency → emergency | Explicit socket sparks; existing emergency safety contract vs lower-severity dataset label; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| production_en_outlet_sparking / en | non-emergency → emergency | Explicit socket sparks; existing emergency safety contract vs lower-severity dataset label; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| scenario_en_26 / en | non-emergency → emergency | Explicit socket sparks; existing emergency safety contract vs lower-severity dataset label; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| ko_spark / ko | non-emergency → emergency | Explicit socket sparks; existing emergency safety contract vs lower-severity dataset label; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| scenario_ko_26 / ko | non-emergency → emergency | Explicit socket sparks; existing emergency safety contract vs lower-severity dataset label; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| scenario_zh_26 / zh | non-emergency → emergency | Explicit socket sparks; existing emergency safety contract vs lower-severity dataset label; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| scenario_zh_27 / zh | emergency → non-emergency | Child missing paraphrase; ZH grammar review; no new translation granted; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |
| zh_spark / zh | non-emergency → emergency | Explicit socket sparks; existing emergency safety contract vs lower-severity dataset label; first boundary: Tier1 grammar or label contract; NEEDS_ADJUDICATION, keep current policy |

No assertion that these sentences were intentionally delegated to Tier2: that cannot
be proven from a label alone. Current learned predictions are absent. Five authenticated
temporary-DB tests install unavailable model/index, timeout, low-memory and startup-not-ready
Tier2/NLU boundaries: recognized Tier1 bypasses them entirely (zero boundary invocations),
returns safe emergency guidance and queues the deduplicated alert. Existing Tier2 error,
not-ready-index, synthetic probability thresholds, review-confirmation and fallback tests pass.
These are dependency-isolation tests, not actual BGE failure simulations or quality measures.

## D. Context B2

Original utterances, source/map assertions and localization checks remain unchanged.
Fixtures now represent the guest's explicit information facet and anaphoric reference.

| Original case | Before | After | Root cause / minimum fixture correction |
| --- | --- | --- | --- |
| Localized VI route after hours | xfail; separate --runxfail failed | PASS | AskInfo hours facet initially missing; Navigate reference flag missing |
| English where is it? | xfail; separate --runxfail failed | PASS | Initial hours facet needed verified anchor; follow-up Navigate explicitly references it |
| Map-only answer follow-up | xfail; separate --runxfail failed | PASS | Follow-up AskInfo needs hours facet and explicit reference; initial Navigate unchanged |
| Cross-language map labels | xfail; separate --runxfail failed | PASS | Same hours/reference fixture correction; existing localization uses signed target-language map label |

Four markers removed only after actual --runxfail PASS; normal full follow-up module
then passes all 9 cases without xfail. Other pending rebuild markers are retained.
No production reference flag is fabricated. Runtime continues requiring live owned session,
authorized recent anchor, valid TTL/source revision and correct facet; no runtime memory
or ownership policy modified. Existing TTL, missing-evidence, latest-anchor, reference-fallback
and cross-session telemetry tests pass. **Scripted context/runtime PASS is not real Qwen
implicit-reference accuracy**, which remains unmeasured.

## E. Dataset contract review queue (unapplied)

10 of 63 semantic frames and 45 of 270 production scenarios expect positive
`expected_business_writes_before_confirmation`. This field denotes durable writes, not a
review proposal or audit event. Current guest_confirm_all requires 0 service-request writes
before owned explicit consent; proposal creation is a separate state, and verified write
receipt is issued only after confirmation. No observed unauthorized Agent write follows from
these old annotations. approval_path/staff-review metadata is a distinct axis from guest consent.

An unapplied proposed diff for each row is recorded below: set only the before-confirmation
write expectation to 0 for the current contract, and adjudicate post-confirmation expected
receipt separately. Do not globally relabel approval_path or historical dispatch behavior.
Authoritative frames can also feed scenarios/training; these descendants are not independent
holdout samples. Existing release/review rules do not clearly authorize unilateral gold edits,
so every proposal remains NEEDS_ADJUDICATION; source datasets and labels stay untouched.
Machine-readable old/new diff and identifiers: review-evidence.json/contract_review_queue.

| Source | ID | Old before-confirmation writes | Proposed current-contract value / review |
| --- | --- | --- | --- |
| frames/semantic_frames.jsonl | FRAME-AC_HOT | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-EXTRA_PILLOW | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-LIGHT_BROKEN | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-MULTI_TOWEL_CLEAN | 2 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-MULTI_TOWEL_LATE | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-SINK_LEAK | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-TOILET_BLOCKED | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-TOWEL_TWO | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-TV_SIGNAL | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-WATER_FOUR | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_light | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_clean | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_multi | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_towel | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_vi_leaking_sink | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_vi_31 | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_ac | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_clean | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_light | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_multi | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_towel | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_ac_not_cooling | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_clean_at_time | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_flooded_room_balance | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_leaking_sink | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_multi_towel_late | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_towel_direct | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_en_31 | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_en_32 | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_ac | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_clean | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_light | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_multi | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_towel | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_ac_not_cooling | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_clean_at_time | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_dnd_clean_balance | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_leaking_sink | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_multi_towel_late | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_towel_direct | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_ko_31 | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_ko_32 | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_clean_at_time | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_dnd_clean_balance | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_flooded_room_balance | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_leaking_sink | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_multi_towel_late | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_towel_direct | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_zh_31 | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_zh_32 | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_ac | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_clean | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_light | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_multi | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_towel | 1 | 0; NEEDS_ADJUDICATION; NOT_APPLIED |

## F. Before/after measurement

Same adapter, datasets, checksums and eligibility/slot definitions. No inference or
extra retrieval was performed. Accepted goals are not completed requests or complete slots.

| Corpus | Eligible denominator | Before accepted / rejected | After accepted / rejected |
| --- | --- | --- | --- |
| gold/vi_core.jsonl | 20 | 7 / 13 | 8 / 12 |
| gold/vi_dev.jsonl | 30 | 11 / 19 | 11 / 19 |
| gold/vi_test.jsonl | 45 | 14 / 31 | 14 / 31 |
| gold/vi_hard_negatives.jsonl | 0 | 0 / 0 | 0 / 0 |
| holdout/service_workflow.jsonl | 200 | 33 / 167 | 33 / 167 |
| challenges/natural.jsonl | 349 | 118 / 231 | 121 / 228 |
| end_to_end/scenarios/production.jsonl | 128 | 53 / 75 | 53 / 75 |

| Metric | Before | After / scope |
| --- | --- | --- |
| Natural oracle rejection proportion | 231/349 = 66.19% | 228/349 = 65.33%; NOT real-model false-rejection rate |
| Reviewed two policy-gap cases rejected | 2/2 | 0/2; same selected reviewed subset, not independent model trials |
| Unsupported action accepted, fixed 20 negative probes | 3/20 | 0/20; previous HEAD gate/policy replay vs current, not natural-corpus precision |
| WP12 wrong command acceptance | 0/2 | 0/2 |
| Unauthorized service writes, protected replay | 0 | 0; authorized confirmation still writes exactly once |
| Context original B2 | 4 strict-xfailed | 4 actual PASS |
| Real model accuracy / slots / F1 | NOT_MEASURED | REAL_MODEL_ACCURACY_NOT_MEASURED |
| Current BGE Recall@K / Tier2 recall | NOT_RUN | NOT_RUN |

Natural language denominators remain VI87, EN98, ZH58, KO106. VI acceptance 32→35;
EN35, ZH13, KO38 unchanged. Holdout denominator200 includes180 context-requiring rows;
the20 context-free oracle probes still reject, each locale has only5 such rows. They need
case-level slot/label adjudication; do not call this 100% real-model false rejection.

CPU gate latency below is a single bounded offline pass (nearest-rank percentiles), with
SDK disabled and no model, network, retrieval or transaction. It includes new safety work
and process/cache effects; no claim of production latency improvement.

| Corpus / samples | Before p50 / p95 ms | After p50 / p95 ms |
| --- | --- | --- |
| gold/vi_core.jsonl / 20 | 0.1422/0.3291 | 0.1946/1.0864 |
| gold/vi_dev.jsonl / 30 | 0.1289/0.2044 | 0.1830/0.2554 |
| gold/vi_test.jsonl / 45 | 0.1365/0.1878 | 0.1659/0.2582 |
| holdout/service_workflow.jsonl / 200 | 0.1239/0.3019 | 0.1410/0.3061 |
| challenges/natural.jsonl / 349 | 0.1049/0.2355 | 0.1144/0.2578 |
| end_to_end/scenarios/production.jsonl / 128 | 0.0358/0.1006 | 0.0408/0.1124 |

WP12 historical one-sample Qwen HTTP≈16.48s, prompt evaluation≈12.32s, 60 generated
tokens, full API≈17.48s remains historical only. CPU inference latency was not changed or
remeasured. No current Qwen/BGE/turn latency distribution or performance target is inferred.

## G. Safety regressions and test evidence

Final accepted groups used separate processes, sequential execution, unique PID basetemp,
copied/fresh SQLite and finite 150s subprocess deadlines. Tests reuse existing infrastructure.
Every group below passed, without skip/xfail/failure. Distinct selected modules total **410
passed**; diagnostic/repeated runs are not added to that count.

| Evidence log | Passed | Actual pytest duration / process wall | Target modules |
| --- | --- | --- | --- |
| coverage-final-evidence.txt | 56 | 3.89s / 7.11s | tests/agent/test_grounded_service_coverage.py |
| semantic-guarded.txt | 90 | 1.86s / 4.813s | tests/agent/test_semantic_authorization.py |
| context-guarded.txt | 9 | 7.68s / 11.078s | tests/agent/test_conversation_memory_followups.py |
| emergency-final-evidence.txt | 98 | 5.87s / 9.281s | tests/agent/test_emergency_safety.py tests/agent/test_emergency_two_tier.py |
| business-guarded.txt | 8 | 4.57s / 7.812s | tests/agent/test_change_confirmation.py tests/agent/test_workflow_truthfulness.py |
| observability.txt | 34 | 4.59s / 7.703s | tests/agent/test_observability.py |
| domain-gates.txt | 27 | 7.57s / 10.344s | tests/agent/test_domain_profile.py tests/agent/test_no_hardcode.py tests/agent/test_no_case_specific_rules.py |
| context-authority.txt | 24 | 6.23s / 9.5s | tests/agent/test_context_reference.py tests/agent/test_reference_fallback.py |
| governed.txt | 64 | 12.74s / 16.25s | tests/agent/test_understanding_layers.py tests/agent/test_commands.py tests/agent/test_command_agent_loop.py tests/agent/test_item_fidelity.py |

Evidence directory: `reports/wp16/`. Commands and RAM snapshots are in each `.run.json`.
Reproduce each bounded group with:

```powershell
python reports/run_wp16_checks.py coverage-final-evidence tests tests/agent/test_grounded_service_coverage.py -q
python reports/run_wp16_checks.py semantic-guarded tests tests/agent/test_semantic_authorization.py -q
python reports/run_wp16_checks.py context-guarded tests tests/agent/test_conversation_memory_followups.py -q
python reports/run_wp16_checks.py emergency-final-evidence tests tests/agent/test_emergency_safety.py tests/agent/test_emergency_two_tier.py -q
python reports/run_wp16_checks.py business-guarded tests tests/agent/test_change_confirmation.py tests/agent/test_workflow_truthfulness.py -q
python reports/run_wp16_checks.py observability tests --sdk tests/agent/test_observability.py -q
python reports/run_wp16_checks.py domain-gates tests tests/agent/test_domain_profile.py tests/agent/test_no_hardcode.py tests/agent/test_no_case_specific_rules.py -q
python reports/run_wp16_checks.py context-authority tests tests/agent/test_context_reference.py tests/agent/test_reference_fallback.py -q
python reports/run_wp16_checks.py governed tests tests/agent/test_understanding_layers.py tests/agent/test_commands.py tests/agent/test_command_agent_loop.py tests/agent/test_item_fidelity.py -q
python reports/run_wp16_checks.py after-final audit
```

The local evidence runners/artifacts are ignored reports files, not new production/evaluation
frameworks; SDK path refers to the previously installed offline test dependency. Before
changing source, individual B2 commands used the same controller, each full node ID and
`--runxfail -q`; see b2-before-vi/en/map/cross.txt.

Diagnostic caveat: the first global socket block also blocked Windows stdlib socketpair
(asyncio self-pipe), producing setup failures: semantic-final 1 failed, b2-final 9 errors,
emergency-final 10 failed, business 7 failed. These logs are retained. PTY early yielding
briefly overlapped two diagnostic groups; they are excluded from final counts/measurement.
The runner was corrected to permit only loopback connects called directly by stdlib
socket.socketpair; application/export HTTP remains blocked. Accepted runs awaited completion
with 30s tool waits and were sequential. No Agent logic was changed to hide these failures.

Additional actual checks: compileall src/tools PASS; repin_configs --check PASS;
tools/validate/agent_domain.py PASS; tools/validate/audit_data.py PASS (366 facts,83 source
artifacts,0 errors), git diff --check PASS. The data audit only reads existing dense-index
metadata; 1480 stored embeddings is not evidence of BGE execution in WP16.
Tracked SQLite hash equals HEAD blob `8194447e86fdfc11bc414a738cb46e2b2fd79a27` after checks.

Regression matrix:

| IDs | Evidence / result |
|---|---|
| R01–R02 | Frozen wrong housekeeping/quiet rejected in semantic and telemetry API tests;0 proposals/preferences/writes |
| R03–R05 | Reviewed natural housekeeping/device symptom and existing 4-language delivery positives accept |
| R06 | Existing missing requested_item clarification retained |
| R07–R09 | Information/negation/quoted+reported/past/conditional negatives fail closed |
| R10–R12 | Valid siblings, unsupported goal and all registry/out-of-shortlist semantic coverage retained |
| R13–R15 | New Tier1 grammar/negation positives+negatives; five unavailable dependency boundaries bypassed |
| R16–R19 | Original four B2 actual PASS; TTL/foreign session/current anchor/source evidence tests PASS |
| R20–R23 | Pending task, explicit consent, replay, staff review, cancellation/modification confirmation tests PASS |
| R24 | Disabled/failing exporter and emergency independent of telemetry PASS with fake/in-memory SDK export |
| R25 | Domain schema, nested optional metadata, invalid types/locales/actions and checksum gates PASS |

No golden labels, harness, model selection/timeout, memory ownership, confirmation authority,
business transaction ordering or idempotency implementation modified. Langfuse still observes
sanitized command acceptance/rejection and verified writes; export never determines state.

## H. Resource use

Real Qwen calls = **0**; BGE loads = **0**; reranker loads = **0**; Cloud ingestion = **0**.
Local HTTP opener is blocked before app import. Final test guard also blocks application
socket connect/connect_ex and heavy torch/transformers/sentence_transformers/onnxruntime
imports; each final log records zero attempted heavy imports and zero external connections.
Actual model transport tests substitute mocked streams; no retry/preload/live smoke.
Windows internal socketpair connects are explicitly counted as IPC, not model/export calls.

The controller samples GlobalMemoryStatusEx before each process and stops below1GiB available
RAM; deadline150s per child, no unlimited loops or parallel pytest in final runs. Snapshot
values below are available system RAM, **not peak process memory**; no peak-memory claim.

Final accepted process wall total: 83.891s. Available RAM snapshots: 6.57–6.84 GiB; system memory load 55–57%. No RESOURCE_BLOCKED condition observed. Before/after adapter wall: 2.484s / 2.344s.

## I. Changed files (WP16 only)

| File | Reason |
|---|---|
| config/agent-domain.json | Reviewed loanword/symptom metadata, conservative exclusions, two Tier1 grammar fixes |
| config/agent-domain.schema.json | Optional symptom/request and reported-speech mapping schemas |
| config/agent-domain.sha256, .env.example | Repin actual changed domain artifact |
| agent/understanding/intent_evidence.py | Generic quotation masking, bounded device-symptom request relationship, exclusions |
| core/domain_profile/validate/semantic.py | Locale/action consistency checks for new optional metadata |
| tests/agent/test_grounded_service_coverage.py |56 targeted positive/negative/config/dependency tests |
| tests/agent/test_conversation_memory_followups.py | Minimal corrected explicit command fixtures; original utterances/assertions preserved |
| tests/rebuild_pending.py | Remove only four now-PASS B2 markers |
| docs/AGENT-WP16-ROUTER-CORRECTNESS.md | Evidence, measured before/after, unapplied review queue |

Source paths above are under src/concierge_kiosk where abbreviated. Ignored reports/wp16
contains execution evidence; local runner/assembly scripts only support reproducibility.
Prior untracked AGENT-CORRECTNESS-AUDIT.md is not a WP16 change. No CI/dataset/frontend edits.

## J. Limitations and next review

Current real Qwen intent/slot/context accuracy **REAL_MODEL_ACCURACY_NOT_MEASURED**;
current BGE Recall@K and emergency Tier2 performance **NOT_MEASURED**. Real voice/browser
E2E not run; CPU inference latency unchanged. Langfuse **CLOUD_INGESTION_NOT_VERIFIED**;
WP16 forbids Cloud ingestion, and offline exporter/privacy PASS does not prove dashboard
acceptance. No inference budget from WP12 was consumed.

New symptom authority intentionally remains narrow (one reviewed device family, VI only,
same-sentence comma relationship). Other symptoms/languages, unquoted reported speech in
ZH/KO, ambiguous service references and remaining emergency grammar need domain review;
no assertion of complete multilingual robustness from scripted tests. Existing quote handling
does not claim coverage of every typographic quoting convention.

Next priorities: (1) adjudicate unresolved oracle slot/context/label cases and the55 write
expectations before optimizing rates; (2) review bilingual symptom/active-emergency evidence
with positive/negated/quoted counterexamples before adding authorizing meanings;
(3) separately measure frozen-digest model/reference and learned Tier2 predictions in a
future operator-authorized run, keeping raw model correctness apart from safe gate rejection.
No model change, verifier, judge, tuning or production-ready claim is warranted here.
