# WP17 — Emergency safety routing

Ngày audit: 2026-10-09. Branch `phase4-handoff`, actual HEAD
`ef38471788c6beb8ee42e9aa205a552c0c1fc8f8` đã chứa WP16. Initial working tree chỉ có
untracked `docs/AGENT-CORRECTNESS-AUDIT.md`; giữ nguyên, không tính là WP17 change.
Đọc AGENT.md/CLAUDE.md, báo cáo WP16, actual Tier1/Tier2, pinned profile, authenticated
emergency answer, staff queue/submissions và existing tests. Không reset/stash/revert/commit/push.

Kết quả offline: sửa recognition boundaries có evidence, tái sử dụng emergency_check,
sửa incident context sau xác nhận và kiểm tra receipt trước khi thông báo queued.
**Không production-ready:** Tier2 thực tế, live model/voice/browser/Cloud chưa nghiệm thu.

## 1. Kiến trúc và đối chiếu fallback/handoff

[Rasa Fallback and Human Handoff](https://legacy-docs-oss.rasa.com/docs/rasa/fallback-handoff/)
mô tả hỏi xác nhận khi intent chưa chắc, phục hồi/fallback và handoff qua channel.
[Microsoft Copilot Studio handoff](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-hand-off)
mô tả Escalate/Transfer conversation cùng tích hợp kênh hỗ trợ. WP17 áp dụng nguyên tắc
clarification trước hành động chưa chắc và transfer có receipt; **không cài Rasa/Copilot,
không thay LangGraph, không copy transcript hay giả lập contact-center integration**.

Actual flow: deterministic classify_dialogue → existing understand_turn → full safety
answer hoặc emergency_check → existing authenticated queue_emergency_alert → staff queue.
Tier2 logistic BGE classifier vẫn dùng thresholds0.64/0.11, l2=3e-5. Nếu không có deterministic
evidence, Router/Tier2 hiện có tiếp tục xử lý; không đổi thresholds, model hoặc timeout.
Existing human-assistance/Handoff vẫn đi qua governed service review/confirmation, không có
đường mới ghi service ticket. Emergency alert là authority exception đã có, khác service write.

Ba mức:

* `emergency`: có active incident được policy nhận diện; hướng dẫn an toàn hiện có và queue
  escalation ngay, không chờ Qwen/BGE hay hỏi xác nhận bổ sung.
* `emergency_check`: dấu hiệu mơ hồ (tiếng kêu cứu, người nằm bất động chưa có evidence mất
  ý thức); guidance hiện có + câu xác nhận ngắn, SOS/staff-location guidance theo UI contract.
  Chưa confirmation → không durable alert. Confirm → existing emergency escalation.
* `knowledge` provisional: không có active/review evidence; đi Router thường. Đây không phải
  bảo đảm Tier2 sẽ luôn đánh giá non-emergency, vì learned predictions chưa được đo.

Full emergency luôn ưu tiên review. Deterministic review return trước learned gate và NLU,
nên vẫn hoạt động khi BGE/Qwen unavailable. Existing five unavailable boundary tests giữ
Tier1 độc lập. Tier2 not-ready/exception tests dùng mock, không load BGE.

## 2. Domain concepts, nguồn và privacy/safety boundaries

Không chép nguyên utterance vào runtime, không thêm service phrase shortcuts. Authorizing
regex/ngữ nghĩa đặt trong `config/agent-domain.json`, có JSON schema và semantic validation.
Optional `emergency_review_patterns`/`emergency_context_patterns` được load qua validated
nlu policy; config valid/omitted/missing locale/wrong type/missing nested field/bad regex tested.

Nguồn hiện có, không tạo training/dataset mới:

* VI collapsed/missing/fire: GOLD-COLLAPSED-VI, GOLD-CHILD_MISSING-VI, GOLD-FIRE-VI;
  SCN-HOSP-VI-49-09, SCN-HOSP-VI-50-09 và các WP16 proven incidents.
* EN/ZH/KO: checked-in multilingual_support GOLD-COLLAPSED/CHILD_MISSING/FIRE,
  source_family semantic_frame_multilingual_support. ZH added 倒下了 + 起不来 and
  孩子不见了 dùng actual existing GOLD wording; không tự dịch câu Vietnamese.
* EN anaphylaxis được kiểm tra ở [NHS](https://www.nhs.uk/conditions/anaphylaxis/).
* VI phản vệ có nguồn [Bộ Y tế, văn bản hướng dẫn](https://vbpl.moj.gov.vn/boyte/Pages/vbpq-toanvan.aspx?ItemID=128248&Keyword=60%2F2021%2FN%C4%90-CP);
  co giật/điện giật kiểm tra terminology ở [EVN health booklet](https://www.evn.com.vn/userfile/VH/User/tcdl/files/2021/1/camnangchamsocsuckhoeevn.pdf).
  Chỉ dùng nghĩa safety incident, không lấy treatment instructions từ booklet.
* ZH 過敏性休克 có nguồn [Hong Kong CHP](https://www.chp.gov.hk/tc/features/106953.html),
  KO 아나필락시스 có nguồn [KDCA](https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6684).

Nguồn public xác minh thuật ngữ, không biến regex thành chẩn đoán. Severe allergy + rapid
swelling được dùng như hazard-report để safety escalation, không xác nhận bệnh phản vệ.
Chỉ thêm các thuật ngữ có evidence; không tự sinh simplified ZH/KO seizure/electrocution
translations. Các cách nói chưa có nguồn/review vẫn NEEDS_REVIEW.

Bounded VI relationships: person/guest + fall/no response or unable to stand; active seizure/
electric shock; fallen into water + not resurfacing; spreading/rising flames + occupied place/
bin; child + explicit missing-location meaning; severe allergy + rapid swelling. Child looking
for a pool/object does **not** satisfy missing-child grammar. Review uses independently bounded
cry-for-help/immobile-person concepts. No generic dangerous-word-only auto-escalation.

Matching masks catalog names and structural quoted instructions, uses clause/match scope for
negation/history/information exclusions, and preserves same-sentence incident relationships
across conjunction/comma. Negated smoke cannot hide a separate choking/sparking incident;
cannot-breathe and missing-child negative morphology remains positive safety evidence.
Existing bilingual emergency meaning is recognized regardless of selected UI language.

Exclusion coverage is bounded, not universal natural-language interpretation. Quoted speech
alone cannot trigger tested Tier1 cases; an unquoted active incident still wins. Historical
and informational fixtures route normally. Clinical hints not reviewed do not silently gain
auto-escalation authority. No change to emergency contact copy/clinical instructions.

## 3. Recognition vs label disagreements: từng trường hợp

Before matrix được chạy trước sửa code bằng existing audit_router_evidence.py. Same dataset
hashes/eligibility/labels before và after; review.json lưu per-case provenance và decisions.
Baseline source replay đọc git HEAD (read-only), không checkout/revert source. GOLD labels ở
core có independent_llm_generation provenance; label không tự là human adjudication.

Các dòng bên dưới bao phủ historical FN/FP trong mọi dataset adapter, không chỉ hai suites
có emergency-positive labels. `knowledge` là Tier1 non-match, không whole-system verdict.

| Dataset / ID / language | Label → before → after | Adjudication / first incorrect boundary |
| --- | --- | --- |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-01 / vi | emergency → knowledge → emergency | Bất tỉnh/no response: thiếu bounded relationship; FIXED recognition |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-05 / vi | emergency → knowledge → emergency | Đang co giật: explicit active symptom; FIXED recognition |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-06 / vi | emergency → knowledge → emergency | Flames rising at bin/hallway: FIXED bounded fire evidence |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-08 / vi | emergency → knowledge → emergency | Fallen in water + not resurfacing: FIXED drowning relationship |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-09 / vi | emergency → knowledge → emergency_check | Cries/breaking objects: ambiguous cause; REVIEW, not clear loss of consciousness |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-13 / vi | emergency → knowledge → emergency | Active suspected electrical contact: FIXED; urgent risk must not await confirmation |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-14 / vi | emergency → knowledge → knowledge | Sudden speech/facial symptoms: NEEDS_REVIEW stroke ontology; NOT_HANDLED by deterministic policy |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-15 / vi | emergency → knowledge → emergency | Fall + cannot stand: reviewed collapsed family; FIXED |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-16 / vi | emergency → knowledge → emergency | Severe allergic reaction + rapid swelling: FIXED urgent hazard |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-17 / vi | emergency → knowledge → emergency_check | Immobile without verified unresponsiveness: REVIEW; confirmation before automatic alert |
| gold/vi_core.jsonl / CORE-VI-EMERGENCY-18 / vi | emergency → knowledge → knowledge | Fast flooding/trapped person: NEEDS_REVIEW compound flood ontology; NOT_HANDLED |
| gold/vi_core.jsonl / CORE-VI-SAFETY_ESCALATION-15 / vi | safety_escalation → emergency → emergency | Burnt electrical smell after unplugging: residual-risk/severity label disagreement; do not lower existing policy |
| gold/vi_test.jsonl / TEST-0CD27740B0E7 / vi | reopen → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| holdout/service_workflow.jsonl / HOLD-EN-09-03 / en | service → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| holdout/service_workflow.jsonl / HOLD-VI-09-01 / vi | service → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| holdout/service_workflow.jsonl / HOLD-VI-09-03 / vi | service → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| holdout/service_workflow.jsonl / HOLD-VI-09-05 / vi | service → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| holdout/service_workflow.jsonl / HOLD-ZH-09-01 / zh | service → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| holdout/service_workflow.jsonl / HOLD-ZH-09-03 / zh | service → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| holdout/service_workflow.jsonl / HOLD-ZH-09-04 / zh | service → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| holdout/service_workflow.jsonl / HOLD-ZH-09-05 / zh | service → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| challenges/natural.jsonl / NAT-0590 / vi | status → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| end_to_end/scenarios/production.jsonl / scenario_vi_27 / vi | emergency → knowledge → emergency | Missing child phrasing: FIXED bounded grammar |
| end_to_end/scenarios/production.jsonl / vi_missing / vi | emergency → knowledge → emergency | Child no longer located: FIXED, GOLD child_missing support |
| end_to_end/scenarios/production.jsonl / scenario_vi_28 / vi | emergency → knowledge → emergency | Collapsed/unresponsive guest: FIXED relationship |
| end_to_end/scenarios/production.jsonl / en_spark / en | safety_escalation → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| end_to_end/scenarios/production.jsonl / production_en_outlet_sparking / en | safety_escalation → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| end_to_end/scenarios/production.jsonl / scenario_en_26 / en | safety_escalation → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| end_to_end/scenarios/production.jsonl / ko_spark / ko | safety_escalation → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| end_to_end/scenarios/production.jsonl / scenario_ko_26 / ko | safety_escalation → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| end_to_end/scenarios/production.jsonl / scenario_zh_26 / zh | safety_escalation → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |
| end_to_end/scenarios/production.jsonl / scenario_zh_27 / zh | emergency → knowledge → emergency | Child disappeared: FIXED using existing GOLD Chinese wording |
| end_to_end/scenarios/production.jsonl / zh_spark / zh | safety_escalation → emergency → emergency | Existing active hazard vs non-emergency label: NEEDS_ADJUDICATION; no automatic relabel or safety downgrade |

Explicit socket sparks in production EN/KO/ZH are deliberately preserved as safety
emergencies: existing emergency regression contract expects that behavior. They remain
label-FP in the unchanged confusion matrix. Burnt smell after unplugging is residual-risk
review; no evidence warrants suppressing the emergency route wholesale.

Remaining auto-escalation FN: four core IDs09/14/17/18. Two have deterministic review handling
(09,17); they remain **FN in binary auto-escalation scoring**. IDs14/18 remain absent from
deterministic authority and require source-backed policy review / Tier2 evidence. No assertion
that Tier2 definitely handles them. Production labeled positives have no Tier1 FN after fixes;
this is limited to this dataset, not a production recall guarantee.

## 4. Confusion matrix và denominators

Binary positive = Tier1 branch `emergency` only. `emergency_check` counts negative here,
and is reported separately. TP/FN denominators are emergency label counts; FP/TN denominator
is the complement. Do not mix repeated language/semantic descendants as independent trials.

| Suite | N | Emergency labels | Before TP / FP / FN / TN | After TP / FP / FN / TN |
| --- | --- | --- | --- | --- |
| gold/vi_core.jsonl | 280 | 20 | 9 / 1 / 11 / 259 | 16 / 1 / 4 / 259 |
| gold/vi_dev.jsonl | 60 | 0 | 0 / 0 / 0 / 60 | 0 / 0 / 0 / 60 |
| gold/vi_test.jsonl | 80 | 0 | 0 / 1 / 0 / 79 | 0 / 1 / 0 / 79 |
| gold/vi_hard_negatives.jsonl | 80 | 0 | 0 / 0 / 0 / 80 | 0 / 0 / 0 / 80 |
| holdout/service_workflow.jsonl | 200 | 0 | 0 / 8 / 0 / 192 | 0 / 8 / 0 / 192 |
| challenges/natural.jsonl | 595 | 0 | 0 / 1 / 0 / 594 | 0 / 1 / 0 / 594 |
| end_to_end/scenarios/production.jsonl | 270 | 21 | 17 / 7 / 4 / 242 | 21 / 7 / 0 / 242 |

Core: TP9→16, FN11→4, FP1→1, TN259→259. Production: TP17→21, FN4→0,
FP7→7, TN242→242. No increase in label-FP on measured suites; this does not prove universal
false-escalation safety. Two core cases enter review; auto-escalation and review are not merged
to claim100% recall. Same core counts can differ from WP15 due to the already committed WP16
negation fix. Baseline here is actual ef384717 HEAD, not the older WP15 rates.

Existing semantic oracle counts stay unchanged on all seven suites (natural121/349 accepted).
No Qwen precision/recall/F1 inferred from oracle probes or pass counts. Learned Tier2 quality,
current actual BGE Recall@K and whole-system emergency recall remain NOT_MEASURED.

## 5. Human escalation, receipts, idempotency và isolation

Existing Workflows queues alerts atomically for active owned sessions/property; priority100,
open/acknowledged status, exact same-session details dedup within60s. Staff ack/resolve and
timed escalation stay intact. No transaction-order/idempotency/business authority changes.

Verified integration bug: confirmation path previously forwarded the affirmation as incident
details. Pending review now retains bounded incident text under the server's session key;
confirmation forwards that original text to existing queue. Clear/decline or a new full
incident removes pending detail; full emergencies preempt stale review. Repeating review +
confirmation for the same incident produces the **same durable alert ID** under existing
dedup policy. No fake parent-child tracing or raw pending text exported to Langfuse.
Pending state remains process-local as before; this is not a new conversation framework.

The response now accepts queued success only from a validated workflow receipt (nonempty ID,
open/acknowledged status, priority100). None/empty/resolved fake receipts, SQLite queue failure
return queued=false and localized unconfirmed guidance. No claim that public responders were
dispatched or staff acknowledged merely because an alert entered a queue. Direct full-incident
repeat retains existing ID; domain emergency dedup/priority/staff-transition tests pass.

Langfuse34 tests, frozen WP12 invalid housekeeping/quiet, semantic gate90 tests, original B2,
owned-confirmation/change replay and five unavailable Tier2/NLU-boundary tests remain PASS.
Tracing remains metadata-only and optional; default disabled; no Cloud export.

## 6. 55 mismatched service-write labels — review queue, không sửa gold

Re-read actual datasets:10 of63 semantic frames,45 of270 production scenarios have positive
expected_business_writes_before_confirmation. Contract guest_confirm_all requires persisted
service receipt only after explicit consent. Draft/proposal and audit bookkeeping are distinct
from that write. Emergency alert exception is a separate workflow, not permission to relax
service confirmation. The mismatch proves an evaluation contract conflict, not unauthorized
database writes by the Agent. Source-family descendants are not independent holdout examples.

Machine-readable queue: reports/wp17/review.json/write_label_review_queue; old values,
proposed before-confirmation value0, source path and NEEDS_ADJUDICATION, applied=false.
Post-confirmation/staff-review expectations require separate review; no blanket approval_path
relabel. All dataset hashes unchanged and no gold label modified. Full IDs:

| Source | ID | Existing before-confirmation writes | Review |
| --- | --- | --- | --- |
| frames/semantic_frames.jsonl | FRAME-AC_HOT | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-EXTRA_PILLOW | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-LIGHT_BROKEN | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-MULTI_TOWEL_CLEAN | 2 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-MULTI_TOWEL_LATE | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-SINK_LEAK | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-TOILET_BLOCKED | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-TOWEL_TWO | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-TV_SIGNAL | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-WATER_FOUR | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_light | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_clean | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_multi | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_towel | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_vi_leaking_sink | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_vi_31 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_ac | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_clean | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_light | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_multi | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_towel | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_ac_not_cooling | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_clean_at_time | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_flooded_room_balance | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_leaking_sink | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_multi_towel_late | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_towel_direct | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_en_31 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_en_32 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_ac | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_clean | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_light | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_multi | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_towel | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_ac_not_cooling | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_clean_at_time | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_dnd_clean_balance | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_leaking_sink | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_multi_towel_late | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_towel_direct | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_ko_31 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_ko_32 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_clean_at_time | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_dnd_clean_balance | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_flooded_room_balance | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_leaking_sink | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_multi_towel_late | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_towel_direct | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_zh_31 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_zh_32 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_ac | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_clean | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_light | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_multi | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_towel | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |

## 7. Targeted test evidence / resource guard

**365 distinct targeted tests PASS**,0 failure/skip/xfail in accepted groups. Repeated diagnostic
runs excluded from total. Existing infrastructure only; no new evaluator/model/framework.
New module test_active_emergency_routing.py has44 regressions (routes, history/negation/
quotes, compound active incidents, four languages, review/receipt replay, invalid receipts,
profile validation). Tests use temporary/copied SQLite, unique basetemp and blocked model/
network boundary. Groups run sequentially, each child deadline150s; validation subprocess60s.
Controller checks available physical RAM, aborts below1GiB; no model/index/reranker load.

| Evidence log | Passed | Process wall seconds | Modules |
| --- | --- | --- | --- |
| emergency-final-reviewed.txt | 198 | 17.188 | tests/agent/test_emergency_safety.py tests/agent/test_emergency_two_tier.py tests/agent/test_grounded_service_coverage.py tests/agent/test_active_emergency_routing.py |
| protected.txt | 140 | 22.109 | tests/agent/test_observability.py tests/agent/test_semantic_authorization.py tests/agent/test_conversation_memory_followups.py tests/agent/test_change_confirmation.py |
| config-gates.txt | 27 | 11.375 | tests/agent/test_domain_profile.py tests/agent/test_no_hardcode.py tests/agent/test_no_case_specific_rules.py |

Accepted process wall total 50.672s; available RAM snapshots 5.57–6.65GiB, memory load 56–63%. Snapshot is not peak process memory. Every final test log: zero heavy-import attempts, zero real application network connections; stdlib Windows socketpair is permitted only for internal asyncio IPC.

Actual bounded commands/evidence:

```powershell
python reports/run_emergency_checks.py before audit
python reports/run_emergency_checks.py emergency-final-reviewed tests tests/agent/test_emergency_safety.py tests/agent/test_emergency_two_tier.py tests/agent/test_grounded_service_coverage.py tests/agent/test_active_emergency_routing.py -q
python reports/run_emergency_checks.py protected tests --sdk tests/agent/test_observability.py tests/agent/test_semantic_authorization.py tests/agent/test_conversation_memory_followups.py tests/agent/test_change_confirmation.py -q
python reports/run_emergency_checks.py config-gates tests tests/agent/test_domain_profile.py tests/agent/test_no_hardcode.py tests/agent/test_no_case_specific_rules.py -q
python reports/run_emergency_checks.py after-final audit
python reports/validate_emergency_changes.py
```

reports/wp17/before.json and after-final.json contain existing adapter results; review.json
contains per-case/label queue. Ignored local reports runners reuse WP16 blocked transport and
memory/controller infrastructure; no parallel pytest. validation.txt: compileall src/tools,
repin --check, domain validator, audit_data and git diff --check all PASS. Audit_data reads
stored embedding metadata, not a BGE invocation. Shipped DB blob remains
8194447e86fdfc11bc414a738cb46e2b2fd79a27, equal to HEAD. No secrets/credentials read or reported.
Model benchmark, full pytest, Ollama preload and Cloud ingestion were not run.

Real Qwen calls=**0**, BGE loads=**0**, reranker loads=**0**, Cloud ingestion=**0**.
Public documentation browsing is separate from application/model telemetry; it sent no guest
data. No model accuracy or production latency improvement is claimed.

## 8. Actual changes and limits

Changed: config/agent-domain.json (bounded concepts/review/context exclusions), matching schema,
agent-domain.sha256/.env.example repin; understanding/intent.py/routing.py (three-tier output
and active-context match scope); application/conversation/engine.py (deterministic review and
owned pending incident context); application/conversation/answers.py (receipt validation);
core/domain_profile/validate/nlu.py (new optional config consistency); new emergency tests and
this report. Source paths abbreviated under src/concierge_kiosk. No CI/frontend/dataset/
model/timeouts/Tier2 calibration/guest_confirm_all edits, no commit/push.

Domain final SHA256: `499e92bfa09b87066cd7a3428f3e4184a27b9187d88458e257fa2ca354d76d24`.
Remaining work: source-reviewed stroke/flood-compound coverage for core14/18; review escalation
severity labels (sparks/burnt smell); new multilingual variants require native/source review.
Do not manufacture translations or suppress hazardous incidents to optimize FP counts.

Emergency context exclusions are finite grammar, not a complete speech/reporting/tense parser;
not every quotation style or mixed historical-current sentence is proven. Learned Tier2 may
still disagree on ordinary/quoted inputs; its calibration/real performance were not evaluated.
Pending emergency state remains process-local, with baseline session lifetime/confirmation
contract; no distributed/restart persistence or new TTL policy is claimed. Raw incident text
stays only in existing owned application/workflow context, not observability metadata.

**REAL_MODEL_ACCURACY_NOT_MEASURED; TIER2_PERFORMANCE_NOT_MEASURED;
CLOUD_INGESTION_NOT_VERIFIED.** Real voice/browser/operator staff-delivery E2E not accepted.
No claim of production-ready or guaranteed100% emergency recall.
