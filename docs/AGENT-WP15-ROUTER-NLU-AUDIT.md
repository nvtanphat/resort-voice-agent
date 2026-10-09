# WP15 — Router/NLU quality audit

Ngày 2026-10-09, `phase4-handoff`, HEAD `aa0c4d8c937899133562fcda63244f317c45b09e`
đúng requested WP14 baseline. `CLAUDE.md`, `AGENT.md`, WP12/WP13/WP14 và source đã đọc.
Preserve untracked correctness audit. Không commit/push/CI change, dataset/label changes,
Qwen preload/inference, BGE load/benchmark hoặc full pytest.

**REAL_MODEL_ACCURACY_NOT_MEASURED** cho current HEAD. Có historical Qwen evidence riêng,
nhưng không đủ current multilingual accuracy distribution. Cloud là
**CLOUD_INGESTION_NOT_VERIFIED**, offline integration PASS; xem báo cáo WP14.1.

## 1. Actual architecture review

```text
classify_dialogue: deterministic emergency → protected safety response
  non-emergency provisional knowledge
  → embedding EmergencyGate (if configured/ready)
  → owned pending-confirmation Layer A
  → FastRouter / nearest reviewed embedding example
  → ServiceSelector candidates + reviewed examples
  → one bounded Qwen compact Command[] proposal
  → structure/registry/verbatim slots → WP13 semantic gate
  → command-to-route projection / reference context
  → governed LangGraph / policy / typed tool contracts
  → proposal → explicit guest confirm → authoritative workflow
```

| Layer | Input/output/evidence | Threshold/fallback/failure | Latency/authority/evaluation coverage |
|---|---|---|---|
| Emergency Tier1 | Guest text + language → emergency or provisional knowledge; profile safety patterns | No numeric confidence; misses may proceed Tier2 | Deterministic classifier measured below; independent of Qwen; emergency alert workflow riêng |
| Emergency Tier2 | Cached BGE query vector + reviewed examples → logistic probability, emergency/emergency_check/None | Full0.64/review0.11, L2=3e-5; index/model unavailable passes through Tier1 | Current learned run NOT_RUN; historical group-held-out calibration; no normal service write authority |
| FastRouter | Query + owned pending field/draft → validated social/slot/cancel command | Similarity0.87, other-label margin0.08; else abstain to NLU | **Embedding router**, không deterministic lexical router; can't start service; mocked unit tests ≠ current accuracy |
| ServiceSelector | Catalog + configured GOLD/CANDIDATE training examples + query vector → top5 candidates, two few-shots | Best catalog/example similarity; uncatalogued reserve floor(5/3)=1; not ready gives empty candidates/full registry | BGE Recall@K NOT_RUN; structural coverage below; candidate set không grant authority |
| Qwen NLU | Compact WP11 schema, full enabled goals, candidates/shots, context/pending slots → proposed Command[] | One bounded call, actual outcome timeout/unavailable/invalid/semantic failure; no retry to hide failure | Current model accuracy NOT_MEASURED; custom transport model metadata; commands never grant writes |
| Structural/WP13 | Parsed type/goal/slots + original guest evidence + owned context → accepted commands/reasons | Registry/type checks, literal slot grounding; policy concepts/action/affirmation in bounded clause; negation/past/question/conflict deny | Actual gate probes below; accepted intent can still need required slots; no implicit confirmation |
| Route projection | Validated commands → one bounded route | Read+action/multiple starts=>multi_task; Confirm/Handoff/change/availability/read precedence | Single `_decision_from_commands`; scripted command tests; malformed/unsupported goals never arbitrary tool |
| Context/reference | Owned live public source anchors + model refers_to_context/context index → rewritten query | TTL, source revision/ownership, unique anchor; no flag/no live anchor=>no inheritance; language labels từ signed map | B2 investigation below; no model-created write authority; snapshot() deliberately returns no implicit anchor |
| LangGraph/tools | Validated commands/state/typed actions → verify/plan/execute observations | Policy deny/require confirmation, budgets, no-evidence recovery; typed contract failure discards payload | Existing trajectory/task harness & E2E tests reused; actual service persistence only explicit authorized confirm |

Explicit NLU failure outcome returns `nlu_failure` immediately, **không** fallback knowledge,
reference retry hoặc planner để che failure. Nearest-example fallback còn tồn tại cho no-outcome
paths/test profile; không mô tả nó như automatic live timeout recovery. CLAUDE prose có mô tả
legacy fallback rộng hơn code hiện tại; audit lấy engine branch thực tế làm authority.
Schema giữ tất cả enabled goals dù shortlist thiếu goal; candidate miss vẫn có thể bias prompt,
nhưng không phải hard exclusion và chưa có current Recall@K để quy lỗi cho selector.

## 2. Dataset inventory

Counts đọc trực tiếp JSONL; hashes/labels/unique utterances lưu `reports/wp15/router-audit.json`.
Đây là inventory + offline gate adapter, không evaluator mới; reuse `_rows` eligibility của
existing `evaluate_command_understanding.py` và actual production gate/classifier functions.
Không chạy evaluator CLI vì initialization của nó query Ollama tags/build learned vectors.

| Existing dataset | Rows | vi/en/zh/ko | Labels / limitations |
|---|---:|---|---|
| gold/vi_core | 280 | 280/0/0/0 | Independent core, route/service labels, split test; some slots absent |
| gold/vi_dev | 60 | 60/0/0/0 | Dev curated natural-challenge source family |
| gold/vi_test | 80 | 80/0/0/0 | GOLD test, small VI-only holdout; labels cần adjudication |
| gold/vi_hard_negatives | 80 | 80/0/0/0 | Guard/negative routes; no eligible service rows in existing loader |
| holdout/service_workflow | 200 | 50/50/50/50 | Simulated expert fresh holdout, reviewed; 180 rows declare context requirements, some hotel signoff required |
| challenges/natural | 595 | 203/159/100/133 | Naturalistic synthetic curated oracle; 349 enabled service rows |
| frames/semantic_frames | 63 frames | Each declares four languages | Symbolic frames, **không 252 natural utterances** |
| end_to_end/scenarios/production | 270 | 30/80/80/80 | Synthetic route/slots/workflow labels; current confirmation contract conflicts cần review |
| end_to_end/edge_cases | 108 | 12/32/32/32 | Synthetic edge expectations |
| end_to_end/service_actions | 240 | 60/60/60/60 | Typed service/tool contract labels, not model predictions |
| journeys/production | 32 journeys | 8/8/8/8 | Multi-turn assertions, not 32 single turns |
| journeys/vi_multi_turn | 123 journeys | 123/0/0/0 | VI-only, correlated journey families |
| backend_plan/turn_cases | 118 sequences | Language not top-level | Nested turns/repeats; not 118 independent natural NLU cases |
| retrieval/grounded | 220 | 55/55/55/55 | Verified fact labels, retrieval coverage only |
| retrieval/grounded_fact_holdout | 88 | 22/22/22/22 | Fact holdout, not NLU intent holdout |
| retrieval/grounded_seen | 1156 | 289/289/289/289 | Seen facts, not independent holdout |
| retrieval/compositional | 576 | 144/144/144/144 | Multi-fact targets, correlated generated families |
| voice_text/vi_asr_robustness | 350 | 350/0/0/0 | Synthetic ASR perturbations, no live voice acceptance |
| simulation/counterfactual_events | 8068 events | 2667/2940/896/1565 | Synthetic operational events, not guest utterances/predictions |
| simulation/hospitality_events | 50000 events | 16679/11215/10042/12064 | Generated business simulation, excluded NLU scoring |
| simulation/stay_episodes | 12000 episodes | 3997/2699/2406/2898 | Generated stay metadata, excluded NLU scoring |

Gold/dev/test/hard-negative rows have unique phrases inside each file; exact normalized phrase
overlap with current natural file=0. However source_family/source_id/concept provenance is shared,
nên không cộng các suites để tuyên bố independent unseen situations. Human hotel signoff chưa
có ở mọi synthetic label. Không dùng symbolic complete-registry test để claim natural robustness.

Phát hiện label/contract risk: 10 frames (8 service,2 multi_step) và45 production scenarios
(31 service,14 multi_step) ghi business writes trước confirmation >0, trong khi pinned property
policy là guest_confirm_all. Đây là mismatch cần review, không bằng chứng current Agent đã
unauthorized write. Không sửa labels. Ví dụ `DEV-E3EC45F57A03` là wake-up call wording nhưng
service label human_assistance; strict gate rejection không mặc nhiên là wrong behavior.

## 3. Metrics thực sự đo được

### 3.1 Deterministic Tier1 safety classifier

Mẫu số n là rows có utterance/language/expected_route. Emergency-positive=
expected_route emergency; TP là actual Tier1 emergency match, FP emergency trên other routes,
FN là emergency label Tier1 không bắt. **FN này không phải end-to-end missed emergency**:
Tier2 BGE chưa chạy. Other routes ở Tier1 là provisional, không chấm full route accuracy.

| Suite | n | Emergency positives | TP | FP | Tier1 FN |
|---|---:|---:|---:|---:|---:|
| vi_core | 280 | 20 | 9 | 2 | 11 |
| vi_dev | 60 | 0 | 0 | 0 | 0 |
| vi_test | 80 | 0 | 0 | 1 | 0 |
| vi_hard_negatives | 80 | 0 | 0 | 0 | 0 |
| service holdout | 200 | 0 | 0 | 8 | 0 |
| natural | 595 | 0 | 0 | 1 | 0 |
| production scenarios | 270 | 21 | 16 | 7 | 5 |

For production Tier1 binary confusion: TP16/FN5/FP7/TN242; binary accuracy
258/270=95.56%, positive recall16/21=76.19%, FP rate7/249=2.81%.
Core Tier1 recall9/20=45%, FP2/260=0.77%.
Suites không emergency positives không cung cấp emergency recall. Profile safety dominance
có thể cố ý override lower-severity labels; FP disagreement phải adjudicate trước tuning.

### 3.2 Current FastRouter và ServiceSelector

**FastRouter accuracy/false positives/abstention, ServiceSelector Recall@K/candidate miss:
NOT_RUN** với current learned embedding. Không load BGE hoặc hash stand-in để claim learned quality.
Không biến unit test mocks thành accuracy distribution.

Structural loader có **2027** reviewed configured examples: vi1048/en330/zh325/ko324.
Tất cả14 enabled goals có training examples. Catalog projection hiện cover9 goals;
uncatalogued5 là amenity_delivery, maintenance, facility_request, dining_request, tour_request.
human_assistance còn nhận generic fallback của catalog rows không có one-to-one mode.
Selector top5 dành một slot cho generic uncatalogued candidates; đây là coverage constraint
có thể đo lại, **chưa chứng minh candidate miss**. WP12 failure dùng zero examples/full
registry và không BGE; không có bằng chứng selector là first incorrect boundary trong case đó.

Historical `reports/nlu/command-calibration.json` (1971 training examples, BGE group-held-out):
router103/103 acted đúng, recall103/270=38.15% trên router-target turns; VI32/111=28.83%,
EN23/53=43.40%, ZH29/53=54.72%, KO19/53=35.85%. Acted precision103/103=100%.
Đây không phải full-dataset accuracy/overall abstention. Historical service fallback112/113
acted correct=99.12%, action recall112/885=12.66%; info false action1/345=0.29%.
Current training loader count tăng56; report không có current fingerprint/holdout trace.
Historical precision1.0 trên class không có predictions là convention, không quality proof.

Historical EmergencyGate calibration:1971 examples (203 positives), group-held-out;
full recall including review1.0, confident recall0.9557, full FPR0.0051, review FPR0.0458.
Positive/negative counts VI122/904, EN27/291, ZH27/287, KO27/286. Không remeasure Tier2;
không cộng các rates với Tier1 run mới để chế end-to-end safety accuracy.

### 3.3 Current Qwen, slots, tools và end-to-end

Intent accuracy/macroF1, current Qwen confusion, slot exact match/P/R/F1:
**REAL_MODEL_ACCURACY_NOT_MEASURED**. Không actual predictions trên current multilingual
gold/holdout để làm mẫu số. Oracle commands dưới đây không phải extracted predictions.
Transport timeout/unavailable tests xác minh failure class/behavior, không accuracy cases.

Mocked/scripted tool/route tests và actual LangGraph/business flow chạy nhưng không lấy
passed-test fraction làm tool selection accuracy/task success/unauthorized execution rate
trên natural corpus. Existing `grade_journey` được reuse trong acceptance test với actual
synthetic response: task_success=True, route_accuracy1/1; chỉ **một scripted turn**.
Existing trajectory/task/tool evaluation tests được chạy riêng, không evaluator/judge mới.
Dataset release gates và harness giữ nguyên, chưa chạy natural E2E benchmark.

## 4. Confusion analysis và historical predictions

Current Tier1 binary confusion có valid labels/predictions, lưu per-suite trong JSON.
Không có current Qwen natural-corpus confusion matrix.

Historical WP12 có một verified completed inference: expected amenity_delivery → raw
housekeeping **1**, và một unsupported quiet preference. Đây là một off-diagonal cell,
không distribution hoặc macroF1; không gộp với current mocked replay để cải thiện accuracy.
Other WP12 LIVE-02/03 NOT_RUN; không tính là incorrect/passed model predictions.

Supplemental `reports/nlu/command-price-real-behavior.json` chứa12 old labeled command-type
records (3/language), summary12/12: Cancel→Cancel4, Modify→Modify4, AskStatus→AskStatus4.
Report thiếu current HEAD/digest/provider trace và không kiểm tra service intents/slots.
Đây là **historical coarse-type summary, provenance incomplete**, không current Qwen PASS;
không trộn mẫu số với verified WP12 service case hoặc tạo current multilingual macroF1.
`real_behavior_verification.json` còn dùng subjective status cho abstentions; loại khỏi
Qwen scoring vì không có raw Qwen prediction/ground-truth contract tương ứng.

## 5. Semantic Gate: coverage và false acceptance/rejection

Adapter cung cấp label-supplied `StartGoal`, chỉ thêm slot khi expected value verbatim trong
guest text, rồi gọi actual `command_supported`. Đây là **oracle goal authorization coverage**,
không raw Qwen output/extraction hoặc full pipeline validation. Accepted không nghĩa required
slots đầy đủ; missing item/room/time vẫn do existing service assessment hỏi lại.

Mẫu số eligible=existing evaluator enabled-service rows. Context-free subset loại **mọi** row
có declared context_requirements, kể cả business prerequisites. Reject của goal-only probe
có thể được sửa bởi correctly extracted object slot; vì vậy bảng là potential label/gate
disagreement, **không unconditional real-model false-negative rate**.

| Suite | Eligible | Oracle accepted / rejected | Context-free rejected / n |
|---|---:|---|---|
| vi_core | 20 | 7 /13 | 13/20 (65.00%) |
| vi_dev | 30 | 11/19 | 19/30 (63.33%) |
| vi_test | 45 | 14/31 | 31/45 (68.89%) |
| natural | 349 | 118/231 | 231/349 (66.19%) |
| production scenarios | 128 | 53/75 | 75/128 (58.59%) |
| service holdout | 200 | 33/167 | 20/20;180 contextual rows không đủ server context |

Natural per-language: VI32 accepted/55 rejected out87; EN35/63 out98;
ZH13/45 out58; KO38/68 out106. Holdout: VI12/38 out50, EN16/34 out50,
ZH2/48 out50, KO3/47 out50. Context-free holdout mỗi language chỉ5cases, không đủ
thống kê rộng; 45/contextual each chưa nghiệm thu request execution.

Root coverage examples:

- `CORE-VI-SERVICE-02`: guest nhờ housekeeping ghé lau giúp, label housekeeping. WP13
  Vietnamese concepts chỉ dọn phòng/dọn dẹp/vệ sinh phòng/làm sạch; clause có request action
  nhưng không registered concept, nên oracle goal bị reject. **Verified policy coverage gap**
  đối với nhãn này, không Qwen timeout.
- `NAT-0316`: TV báo mất tín hiệu, hỗ trợ kiểm tra. Maintenance concepts/action không đồng
  thời trong bounded clauses; tình huống symptom-based không được authorized. **Verified
  rule boundary**, valid natural-intent interpretation cần adjudication trước mở rộng authority.
- `HOLD-EN-04-03`: explicit transformer delivery nhưng probe không có requested_item evidence
  slot trong label. Unknown object-slot path có thể authorize nếu model extract verbatim
  transformer; **incomplete oracle slot evidence**, không kết luận proven false rejection.
- `DEV-E3EC45F57A03`: wake-up wording label human_assistance; **label ambiguity/mismatch**,
  không đổi label để lấy PASS.

Frozen WP12 **wrong intents rejected2/2, falsely accepted0/2** ở fully enabled property
acceptance flow. Không proposal, preference persistence hoặc service write; clarification đúng.
Không coi safe rejection là NLU accuracy PASS: raw output vẫn housekeeping/quiet sai.
Separate natural multilingual valid amenity delivery4/4 và housekeeping paraphrases4/4
được existing semantic tests kiểm tra; falsely rejected0/8 trong **tám curated positive
fixtures**, không đại diện corpus rate. Complete-registry symbolic tests chỉ contract coverage.
No exhaustive adjudicated wrong-intent corpus để ước lượng global semantic false acceptance.

## 6. Context B2 investigation

Chạy nguyên four existing strict-xfails với `--runxfail` để thấy actual failure; **4 failed**,
không sửa markers/assertions. Normal targeted suite giữ **4 xfailed**. Diagnose bằng existing
copied-DB fixture; chỉ cung cấp missing valid command fields trong **new separate probes**.

| Existing B2 | Original failure | First boundary / classification | Controlled probe outcome |
|---|---|---|---|
| Localized route after VI hours | grounding no_evidence, không map_verified | Navigate fixture thiếu refers_to_context; query bare pronoun không prefix live anchor. Fixture/contract + reference resolution | Script AskInfo hours then Navigate with flag: anchor1, map verified, SUPPORTED |
| English where is it? | map unavailable | Original first hours turn thiếu scripted AskInfo/facet => lexical no_evidence/anchor0; then missing reference flag | With AskInfo facet hours + flag: extractive source, anchor1, map verified |
| Cross-language map labels | route navigation nhưng map unavailable | Missing reference flag prevents localized_map_query receiving referred anchor; không chứng minh translation bug | VI anchor preserved, EN target map verified with flag |
| Follow-up after map-only answer | evidence UNSUPPORTED | Map-only first turn creates anchor1; second bare AskInfo lacks scripted reference + hours facet. Reference/fixture, không missing anchor lifecycle | With explicit AskInfo hours+flag: SUPPORTED |

Intermediate probes chỉ thêm reference flag còn English anchor0 (first no_evidence) và map-only
follow-up PARTIALLY_SUPPORTED (no hours facet). Bổ sung exact existing command fields làm
cả four probes đạt; không production phrase rule, source mutation hoặc relaxed assertion.
Snapshot implicit anchor vẫn None theo contract; live recent_anchors/TTL/source-revision
reauthorization là path actual context. Session-isolation tests hiện có pass; current
real-Qwen ability chọn correct reference/facet còn NOT_MEASURED.

## 7. Latency: measured / historical / not measured

| Stage | Evidence | Status / scope |
|---|---|---|
| Tier1 classifier | Current offline dataset function calls | Executed; adapter không thu stage distribution, latency NOT_MEASURED |
| FastRouter/BGE selector | No learned run | NOT_RUN/NOT_MEASURED; không hash substitute claim |
| Semantic gate, natural349 oracle calls | `router-audit.json` | Offline p50=0.1240ms, p95=0.3483ms; current Python gate only |
| Semantic gate, holdout200 oracle calls | Same JSON | Offline p50=0.1323ms, p95=0.3084ms; không model/API latency |
| LangGraph/model transport boundaries | Actual SDK acceptance spans | Recorded synthetic per-observation latency, không production distribution |
| Retrieval/rerank | Lexical copied-DB tests; no learned reranker/BGE | Functional regression executed; representative latency NOT_MEASURED |
| Overall natural turn p50/p95 | No current model corpus run | NOT_MEASURED |
| Historical WP12 model HTTP | `wp12-authorized-real-nlu.json` | 16.484s, **one** completed diagnostic sample |
| Historical prompt evaluation | Same provider timings | 12.32045s /1153 tokens |
| Historical generation | Same provider timings | 4.115385s /60 generated tokens |
| Historical total provider | Same provider timings | 16.4786517s |
| Historical full API | Same LIVE-01 record | 17.484s; wrong intent despite completed inference |

WP12 diagnostic socket20s/turn30s không production deadline, không đề nghị đổi timeout từ
one sample. Preload5.797s riêng, không NLU task case. Không sử dụng remaining two WP12 HTTP calls.
Prompt schema optimization có offline bytes evidence nhưng single wrong sample không chứng minh
schema gây lỗi/prefill distribution. Mock transport counts/timings không real CPU performance.
Historical calibration/sample speedups không coi là production latency improvement của WP15.

## 8. Root causes và prioritization

| Issue | Evidence | Severity | First incorrect boundary | Recommended direction |
|---|---|---|---|---|
| Completed wrong service/preference | WP12 raw output, current replay rejects2 | High quality risk, safety gate works | Qwen proposal; not candidate selection (BGE not used) | Collect raw-vs-validated outcomes in existing evaluator before tuning |
| Narrow semantic concept/clause coverage | Oracle corpus disagreement, CORE-VI-SERVICE-02 | High potential false-rejection pressure | WP13 policy evidence matching | Reviewed semantic policy coverage with negation/privacy/write guards retained |
| Label/slot/context ambiguity | Wake-up/human label,180 contextual holdout rows, missing item evidence | High measurement risk | Dataset contract / supplied oracle input | Adjudicate existing labels/context, report denominator exclusions |
| B2 expected implicit context | Four unchanged xfails; corrected command probes4/4 | Medium, current model coverage unknown | Test command fields/first facet, before localization | Align fixtures to explicit command contract retaining assertions |
| Stale learned calibration |1971 historical vs2027 current examples | Medium measurement risk | Training/calibration provenance | Re-run existing selector/router measurement only with allowed resources |
| SDK metadata float latency loss |Real SDK exporter dropped float timings despite mock adapter preserving them; fixed with export regression |Medium observability risk |Export masking decoding SDK serialization |Decode numeric allowlist before typed sanitize; native span timestamps retained |
| Generic candidate reserve one for five capabilities | Static actual selector code/registry; no Recall@K | Hypothesis, not measured failure | Candidate shortlist allocation (possible) | Measure misses by goal before change; no router rewrite |
| Production writes-before-confirm labels mismatch |8 service frames/31 service scenarios>0 | High release-metric interpretation risk | Evaluation business expectation vs property confirmation policy | Review expectations, preserve guest_confirm_all; don't alter labels to improve score |

Không đủ evidence để quy mọi corpus rejection cho Qwen, khẳng định BGE candidate miss,
hoặc đổi model/hardware/timeouts. Policy rejection rate và model accuracy là hai mẫu số riêng.

## 9. Tối đa ba thay đổi ưu tiên cho WP16

1. **Existing evaluator outcome/provenance adapter + contract adjudication.** Components:
   command-understanding report/journal và existing labels/context review. Cơ sở: current report
   lưu validated goal, không tách raw wrong rejected/correct rejected; stale calibration và
   wake-up/human/write-before-confirm mismatches. Capture safe categorical raw intent/outcome,
   actual candidate rank, ground-truth association, missing-slot/context exclusions và current
   fingerprints. Risk: đổi reporting denominator; giữ existing release gates/schema compatibility.
   Verify metrics: eligible/excluded counts, valid-label raw confusion, Recall@K, rejected buckets;
   expected: mọi evaluated case có provenance và mutually distinct outcome, không fake accuracy.

2. **Reviewed WP13 semantic evidence coverage improvements.** Components:
   signed semantic policy/data builders và clause grounding, không Router mới/verifier LLM.
   Cơ sở: proven loanword/symptom-clause gaps và high oracle disagreement; review labels trước.
   Risk: widening service authorization/negation false acceptance. Verify on existing holdout,
   annotated positive/negative subsets, WP12 replay, required-slot/ownership/confirmation tests.
   Expected: lower adjudicated false-rejection count from measured baseline; frozen wrong
   commands remain0 accepted, unauthorized service writes remain0. Không promised target % khi
   chưa adjudicate đủ labels; không học special water phrase.

3. **Align existing B2 fixtures with explicit command/anchor contract.** Components:
   conversation-memory tests, model prompt/reference coverage checks; production fix chỉ nếu
   actual reference model fails. Cơ sở: original4fails, explicit-field four probes pass; English
   initial hours facet tạo verified source. Risk: accidentally hide initial NLU deficiency.
   Keep original source/locale/session assertions, measure reference/facet correctness separately
   from scripted downstream proof. Expected: four supported command-flow cases deterministic
   pass, no cross-session anchor use; current real-model follow-up acceptance vẫn separate.
   WP15 **không** remove xfail markers hoặc nới assertions.

Không tự implement fine-tuning/model replacement/new router, cloud judge hoặc heavy benchmark.

## 10. Test evidence, implementation scope và commands

Changes: add `tools/evaluation/audit_router_evidence.py` (small inventory/probe adapter),
`tests/agent/test_router_acceptance_audit.py` (real SDK offline + existing score harness + B2
probes), two reports; add three missing safe workflow status enums and decode SDK-serialized
numeric metadata before export allowlist. Export regression checks actual latency/provider values.
Không sửa evaluation harness/scripts hiện có, golden labels, production route/policy logic,
business ordering, idempotency, memory ownership, model/timeouts hoặc CI.

| Run | Actual result | Evidence |
|---|---|---|
| Existing observability suite, SDK available |34 passed | `reports/wp141-offline-tests.txt` |
| Four original B2, `--runxfail` diagnostic |4 failed,5 deselected (expected evidence), markers untouched | `reports/wp15-b2-diagnosis.txt` |
| New SDK/receipt/oracle + corrected B2 command probes |7 passed | `reports/wp15-context-probes.txt` |
| Combined14 targeted modules |326 passed,4 xfailed,0 failures;97.36s | `reports/wp15-targeted-regressions.txt` |
| Final SDK/privacy + new acceptance + existing task/tool eval tests, sau numeric-mask fix |52 passed,0 failures,0 skips;9.02s | `reports/wp15-final-adapters.txt` |
| Offline label/oracle/classifier adapter |Executed seven existing datasets;0 Qwen/BGE/Cloud requests | `reports/wp15/router-audit.json`, `reports/wp15-router-audit.txt` |
| compileall src/tools, repin configs check |PASS; hashes unchanged | Actual commands, no data rebuild |

Executed runner commands (source already installed; runner blocks live Ollama and selects temp DB):

```powershell
python reports/run_wp14_targeted.py --sdk tests/agent/test_observability.py -q -W error::ResourceWarning
python reports/run_wp14_targeted.py tests/agent/test_conversation_memory_followups.py --runxfail -k 'cross_language_followup or english_where_is_it or followup_after_map_only or where_is_it_after_hours' -q
python reports/run_wp14_targeted.py --sdk tests/agent/test_router_acceptance_audit.py -q -s -W error::ResourceWarning
python reports/run_wp14_targeted.py --sdk tests/agent/test_observability.py tests/agent/test_router_acceptance_audit.py tests/agent/test_semantic_authorization.py tests/agent/test_understanding_layers.py tests/agent/test_commands.py tests/agent/test_command_agent_loop.py tests/agent/test_conversation_memory_followups.py tests/agent/test_nlu_memory.py tests/agent/test_e2e_guest_journeys.py tests/agent/test_emergency_two_tier.py tests/agent/test_emergency_safety.py tests/ops/test_langgraph_durable_restart.py tests/agent/test_no_hardcode.py tests/agent/test_no_case_specific_rules.py -q -W error::ResourceWarning
python reports/run_wp14_targeted.py --sdk tests/agent/test_observability.py tests/agent/test_router_acceptance_audit.py tests/agent/test_task_eval.py tests/agent/test_tool_eval.py -q -W error::ResourceWarning
$env:PYTHONPATH='src;.'
python tools/evaluation/audit_router_evidence.py --output reports/wp15/router-audit.json
python -m compileall -q src tools
python tools/config/repin_configs.py --check
```

Tests tuần tự, each unique `--basetemp=reports/wp14-test-tmp-<PID>`. SQLite writes dùng fresh/copy
DB, shipped DB không mở để ghi. Counts giữa runs overlap, không cộng thành unique tests.
Existing pytest/Starlette deprecation warnings không accuracy failures. Không Cloud/real-model/
live voice/browser E2E acceptance, do đó **không tuyên bố production-ready**.
