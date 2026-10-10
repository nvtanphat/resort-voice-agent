# Kiểm toán intent router — 2026-10-10

Báo cáo so sánh với **working tree lúc bắt đầu**, trên branch `phase4-handoff`, HEAD `ccbca237f8c302aee1787301ca622a306185b153`. Không so với cây sạch tại HEAD vì có thay đổi của người dùng chưa commit. Snapshot byte gốc ở `.cache/intent-router/workspace.zip`; manifest ở `.cache/intent-router/workspace.json`. Không reset, checkout, commit, push hoặc tải model.

Số liệu máy đọc được: [báo cáo](../reports/intent-router.json), [nhật ký API và raw model](../reports/intent-router-cases.jsonl). Công cụ tái lập: [evaluate_intent_router.py](../tools/evaluation/evaluate_intent_router.py). Nhật ký giữ cả lượt đo bị dừng; chỉ hai stage có `complete=true` được dùng để so sánh.

## Phạm vi và cách chấm

- Model thật `qwen2.5:7b`, embedding thật `bge-m3`, `num_gpu=0`, ngân sách NLU 30 giây. Digest model, fingerprint source/config/training và hash DB có trong JSON.
- Một bộ 80 `vi_hard_negatives` và một bộ giữ riêng 25 câu: vi 16, en 3, zh 3, ko 3. Bộ giữ riêng được viết và khóa **trước khi sửa runtime**; SHA-256 `e53c5315502d9557eeaf578fff581a5c73efcb660deda401695f5f209cbdc5ac`. Không sửa câu hoặc nhãn sau khi xem kết quả; không dùng lỗi của bộ này để chỉnh runtime/training.
- Đúng ở bộ giữ riêng nghĩa là đúng multiset `StartGoal` và số lượt đọc mong đợi, không phát sinh `Handoff`, `Cancel`, `Modify`. Đây **chưa phải** kiểm chứng tất cả phòng, món, đơn vị, số lượng và giờ ở đầu ra workflow. Nhãn do agent viết, `unreviewed`; không phải nhãn người duyệt độc lập.
- Câu bẫy dùng nguyên oracle đã có ở `tools/evaluation/run_human_review_probe.py:oracle`. `non_action` bổ sung kiểm tra biên không tạo route hành động; không đổi nhãn dữ liệu. Không coi `UNAVAILABLE` là `UNSUPPORTED`.
- Scorer ban đầu so nhãn trực tiếp với `tool_route`, bỏ mất `evidence_status`, cho số 2/80 sai. Đã tính lại **summary** từ transcript gốc bằng oracle hiện hữu: 64/80. Giữ nguyên journal gốc và lưu `oracle_rescored.rescored_cases`, không viết lại kết quả để che lỗi scorer.
- Gọi API/session/CSRF, emergency, router, model, gate, LangGraph và workflow thật, với SQLite copy. Không gửi confirm. Đếm `service_requests` bằng SQL trước/sau từng lượt. Planner và semantic answer generation tắt để đo boundary NLU; không phải nghiệm thu toàn bộ RAG, browser hay voice.
- `p50/p95` là độ trễ API theo nearest rank; có retrieval và workflow. Ký tự là độ dài raw text được `_chat` trả về, gồm giá trị 0 khi không trả text; không phải tổng token/byte Ollama đã sinh trước khi hủy stream. `mean_output_characters` tính trên model calls, không tính fast path không gọi model.
- Stage sau warm prefix ngoài phần chấm và ghi riêng `scoring_readiness`. Stage trước chưa ghi trạng thái residency tại lúc bắt đầu; không suy diễn một A/B latency khởi động hoàn toàn đồng điều kiện từ các percentile này. Khởi động có thí nghiệm riêng bên dưới.
- Proposal trước được quan sát ở projection API và lệnh hiểu ý định. Stage sau kiểm thêm bảng `proposals` và `review_state`; phương pháp quan sát stage sau chặt hơn.

## Số đo trước/sau với model thật

<!-- ROUTER_METRICS -->
| Bộ / ngôn ngữ | n | Đúng trước → sau | p50 giây trước → sau | p95 giây trước → sau | Ký tự/model call trước → sau | Write trước confirm | Goal/proposal sai trước → sau |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bẫy vi | 80 | 64/80 → 65/80 | 8.302 → 10.252 | 11.988 → 12.878 | 95.8 → 112.7 | 0 → 0 | 0 → 0 |
| Giữ riêng vi | 16 | 8/16 → 9/16 | 17.028 → 15.346 | 30.160 → 20.651 | 211.1 → 183.0 | 0 → 0 | 1 → 1 |
| Giữ riêng en | 3 | 0/3 → 1/3 | 16.345 → 15.520 | 18.744 → 20.072 | 208.3 → 215.3 | 0 → 0 | 1 → 0 |
| Giữ riêng zh | 3 | 1/3 → 3/3 | 17.685 → 14.280 | 19.620 → 16.931 | 238.7 → 160.7 | 0 → 0 | 0 → 0 |
| Giữ riêng ko | 3 | 2/3 → 1/3 | 15.588 → 24.378 | 18.745 → 24.513 | 224.0 → 221.7 | 0 → 0 | 0 → 0 |

Hai stage đều hoàn chỉnh 105/105, source/config/training và tracked DB giữ nguyên trong stage. Tổng write trước confirm: **0/105 ở cả trước và sau**.

Phân loại quan sát ở bộ giữ riêng, chỉ từ raw text và command stream: số case thiếu goal mong đợi trong text trả về 7 → 2; có goal trong text nhưng không được nhận sau sinh 5 → 8; có extra goal 2 → 1. Không đủ trace reason để quy toàn bộ nhóm thứ hai cho riêng semantic gate, source validation hay schema parsing. Details theo case nằm ở `diagnostics` trong JSON; không chỉnh runtime theo các details này.

| Bộ / ngôn ngữ | Model calls trước → sau | Few-shot nhiều command / tổng few-shot trước → sau |
|---|---:|---:|
| vi_hard_negatives/vi | 65 → 64 | 2/130 → 64/128 |
| intent_router/vi | 16 → 15 | 2/32 → 16/30 |
| intent_router/en | 3 → 3 | 2/6 → 4/6 |
| intent_router/zh | 3 → 3 | 1/6 → 3/6 |
| intent_router/ko | 3 → 3 | 1/6 → 3/6 |

Ký tự trung bình giảm ở VI và ZH giữ riêng, tăng ở EN và câu bẫy. KO ít ký tự hơn nhưng chậm hơn và accuracy giảm; không diễn giải wire phẳng như cải thiện chung mọi ngôn ngữ.

Model calls không trả text: trước 1, sau 0. Không có failure reason ở từng call đủ để coi toàn bộ số này là grammar loop.
<!-- ROUTER_METRICS_END -->

Không quy phần cải thiện end-to-end cho riêng một thay đổi: P1–P5 dùng chung lượt đo, chưa có ablation từng thành phần.

## P1 — thiếu demonstration cho composition

**Nguyên nhân xác định:** bản gốc `service_selector.py:595,646` chọn nearest examples theo cả câu, đồng thời buộc mọi goal thuộc shortlist `offered`. `goal_ranking:570` gán cả ví dụ nhiều lệnh cho goal đầu tiên. Đếm corpus bằng loader thật: VI 12/1008 có nhiều `StartGoal`; en/zh/ko mỗi ngôn ngữ 3. Tần suất few-shot thực tế của lượt baseline được ghi trong JSON, không thay thế bằng số 0/34 từ đợt đo cũ của người dùng.

**Đã sửa:** [service_selector.py](../src/concierge_kiosk/agent/understanding/service_selector.py#L318) lập index cho từng source training đã validate, không gán cả turn cho goal đầu; [selection](../src/concierge_kiosk/agent/understanding/service_selector.py#L663) dùng registry đầy đủ cho eligibility, dành một vị trí cho composed example, ưu tiên cùng ngôn ngữ. Tập [training](../datasets/training/agent/compositional_requests.jsonl) thêm 27 câu, vi 12/en 5/zh 5/ko 5, nguồn `assistant_authored_compositional_training`, `CANDIDATE`, `unreviewed`. Sau sửa: nhiều `StartGoal` VI 22/1020, en 7/335, zh 7/330, ko 7/329. Không đọc evaluation trong loader runtime.

**Phần bù gỡ:** production `engine.command_for_session` không còn dựng/passing `request_parts` hoặc ranking trên các mảnh trước model. Helper `_request_parts`, tham số compatibility và contract test cũ vẫn giữ; không tuyên bố đã xóa API này.

**Kiểm chứng:** test eligibility ngoài top-k, tách goal ranking của compound training và cache reload đều pass. Accuracy, percentile, mean ký tự, writes và số few-shot composition thật: bảng chung phía trên.

## P2 — protocol dài, sinh lại slot server đã sở hữu

**Nguyên nhân xác định:** bản gốc `commands.py:531,568` dạy model trả `slots:[{name,text}]`; mô tả và ví dụ lặp room/count/unit/time. Output này cần server đọc lại. Prompt/output nhiều nhánh tăng công việc sinh JSON trên CPU. Số đo 17 lượt và hồi quy tuyến tính trong yêu cầu chưa được tái lập như một thí nghiệm riêng.

**Đã sửa:** [wire và parser](../src/concierge_kiosk/agent/understanding/commands.py#L462) dùng `{type,goal,text}` với `item` tùy chọn; không dạy sinh `slots` array. Source là span nguyên văn, được server mở rộng tới scope đầy đủ, validate rồi mới làm evidence. Slot số/phòng/đơn vị/giờ/ngày do server trích trên source; room có thể lấy từ cả turn. [Few-shot](../src/concierge_kiosk/agent/understanding/commands.py#L663) chuyển sang wire phẳng. Canonical command và parser legacy giữ để không làm hỏng contract của callers hiện hữu.

**Phần bù gỡ/giữ:** không còn lặp các slot này trong output instruction/example mới. **Chưa gỡ** `num_predict=384`, retry với `repeat_penalty=1.1`, hoặc guard 16 whitespace token: chưa có bằng chứng chúng thừa; test contract hiện hữu vẫn đòi hỏi transport recovery. Shallow JSON vẫn có outer `commands` array, không phải grammar một tầng. Không tuyên bố đã giải quyết hoàn toàn mọi grammar loop của model 7B.

Ollama hỗ trợ JSON mode và JSON schema qua `format`; runtime giữ JSON mode và validation server, không đổi model hoặc bypass gate. [Tài liệu Ollama](https://docs.ollama.com/capabilities/structured-outputs)

**Kiểm chứng:** wire không sinh slot, source không nguyên văn/quoted/trim negation bị loại, thời gian từng request được giữ riêng trong governed state. Ký tự và p50/p95 thật ở bảng chung; không suy diễn output luôn ngắn hơn khi số intent được nhận tăng.

## P3 — service identity phụ thuộc inventory và default

**Nguyên nhân xác định:** bản gốc `intent_evidence.py:244,396` dùng concept/action ontology hoặc top embedding toàn turn; selector chưa index action criteria và gán compound training về goal đầu. `config/agent-domain.json:83,289` mô tả amenity/facility bằng danh sách ví dụ và điều phối department.

**Đã sửa:** 14 description chỉ rõ công việc được thực hiện và cách phân biệt loose supply, setup, repair, cleaning, prepared order, reservation. [Config](../config/agent-domain.json#L83) không thêm tên món hay động từ. [Selector](../src/concierge_kiosk/agent/understanding/service_selector.py#L633) rank cả criteria và từng span training; [semantic gate](../src/concierge_kiosk/agent/understanding/intent_evidence.py#L542) dùng ranking của **source lệnh đã validate**, giữ nguyên minimum và modality guards. Cache key bao gồm criteria, training commands/spans và manifest embedding. Cache text chỉ giữ index texts, không lưu câu evaluation/khách vào corpus training.

**Bằng chứng development:** trên 6 câu training có model thật, protocol sinh đủ các lệnh; trước khi lập source index, gate loại 1 request VI và 1 KO ở mẫu này; sau source index 6/6 command stream được chấp nhận. Đây là training-fed development, **không phải** independent accuracy. Parser được sửa để nhận particle đã có trong grammar sau measure word, không thêm từ KO/VI vào config. Test generic grammar và multilingual slot/numeral pass.

**Phần bù gỡ/giữ:** không thêm inventory keyword để cứu món mới. Ontology direct evidence vẫn tồn tại để phục vụ deterministic paths và contract cũ; không tuyên bố mọi noun/verb ngoài vocabulary đều được hiểu. `default_for_kind` vẫn là business routing default, không dùng làm tiêu chí chọn service trong prompt mới.

**Số đo độc lập:** bảng chung phía trên. Chưa đo một taxonomy đồ vật chưa thấy có đủ nhãn và chưa làm ranking ablation cho cặp amenity/facility.

## P4 — hai hợp đồng so khớp dấu

**Nguyên nhân xác định:** bản gốc `intent_evidence.py:61,126` có `spans` bỏ dấu và `marker_spans` có dấu; caller tự chọn nên authority thay đổi theo vị trí gọi. Slices của string folded mất thông tin surface.

**Đã sửa:** [_EvidenceText và spans](../src/concierge_kiosk/agent/understanding/intent_evidence.py#L50) giữ surface NFKC/casefold và map offset kể cả Hangul decomposition; slices/trim/concatenation giữ surface. `spans` mặc định phân biệt dấu được gõ; `marker_spans` delegate về cùng implementation. Input thật sự không dấu giữ policy tương thích hiện hữu.

**Phần bù gỡ:** không còn hai implementation chọn semantics dấu khác nhau ở các call sites này. API `marker_spans` còn để tương thích, không thêm rule cho từng cặp từ.

**Kiểm chứng:** các cặp có dấu khác nghĩa, NFC/NFD và input không dấu được test bằng invariants riêng. Real-model gate/route số đúng, percentile, ký tự, writes: bảng chung; chưa có bộ minimal pairs độc lập đủ lớn cho cả bốn ngôn ngữ.

## P5 — chia punctuation thành task

**Nguyên nhân xác định:** bản gốc `intent_evidence.py:91,587` và `grounded_service.py:206` split dấu câu/connector trực tiếp; `state.py:364,451` phải gộp subset/superset item sau khi model đã chia vụn. Concept noun đơn lẻ chưa đủ bằng chứng vị ngữ.

**Đã sửa:** [predicate_ranges](../src/concierge_kiosk/agent/understanding/intent_evidence.py#L181) dùng một scope algorithm: strong sentence boundaries, chỉ cắt coordination khi hai bên có predicative evidence, giữ complement/list và các facet thông tin chung. Service noun đứng trong object/list không tự tạo predicate. [command_source](../src/concierge_kiosk/agent/understanding/intent_evidence.py#L228) mở rộng span model để không bỏ negation/report/condition. Grounded path và gate cùng dùng scope; state đọc slots từ source lệnh thay vì đoán source từ goal.

**Phần bù gỡ:** xóa `_item_lines` và item subset/superset merge. Chỉ còn dedup command trùng service, cùng source và cùng non-item slots. Cancel/Modify thiếu owned draft/ticket vẫn bị chặn: đây là business ownership guard, chưa có bằng chứng có thể bỏ.

**Kiểm chứng:** list/complement/đọc nhiều facet, một execution kèm một câu hỏi, tên service nằm trong compound object, độc lập hai giờ và không trim negation. Có một regression test runtime fail trong development; sửa structural scope rồi pass, không sửa test cũ. Một lượt sau bị dừng; journal đã ghi 42 câu bẫy vì source audit phát hiện noun-only predicate; chưa chạy câu giữ riêng, journal giữ nguyên và lượt này bị loại khỏi so sánh.

**Số đo thật:** bảng chung. Không tuyên bố đây là full syntactic parser; chưa kiểm chứng mọi dạng ellipsis/coordination.

## P6 — startup chiếm permit của khách

**Nguyên nhân xác định:** bản gốc `local_ai.py:207` lấy unowned permit warm-up; `admission.py:63` và `engine.py:359` chỉ có immediate admission. Guest bị từ chối dù warm-up sẽ release permit.

**Đã sửa:** [enter_guest_slm](../src/concierge_kiosk/runtime/admission.py#L84) đợi `Condition` chỉ khi chủ permit là startup, wake khi release, giữ một SLM job và deadline trong ngân sách lượt. [Engine](../src/concierge_kiosk/application/conversation/engine.py#L359) dùng bounded wait; deadline hết trả `turn_budget_expired`. Không đưa guest khác/STT vào hàng đợi này, không release permit native đang chạy.

**Phần bù gỡ:** bỏ immediate `try_enter_slm` của luồng NLU production khi admission hỗ trợ startup wait; còn fallback cho adapter legacy. Không nới budget hoặc cho inference cạnh tranh.

**Đo thật, n=1 mỗi arm:** dùng class admission gốc từ snapshot và class hiện tại, cùng prefix sau sửa 6778 ký tự, stop Qwen trước mỗi arm, CPU. Warm trước/sau 46,844/44,938 giây; guest trước bị từ chối ngay 0,000 giây; sau đợi 30,016 giây rồi **hết hạn**, chưa được admit. Immediate busy: 1/1 → 0/1. Guest admitted: 0/1 → 0/1. Với n=1, p50=p95 bằng thời gian admission tương ứng. Không sinh command guest, nên ký tự output guest=0; không mở business store, không đo service writes bằng SQL trong thí nghiệm này. Đây là admission experiment, không phải API/customer success experiment.

**Kết luận P6: NO-GO cho guest đến ngay lúc cold startup.** Đã sửa fairness/wait, chưa chứng minh xử lý được lượt ấy trong 30 giây; giới hạn warm-up lạnh còn hiện hữu.

## P7 — bằng chứng đánh giá và nhãn hẹp

**Nguyên nhân xác định:** `docs/TESTING.md:97,98` báo 79/80 và 11/11 từ đợt trước; bộ 11 câu cũ được dùng để development rồi được báo như accuracy. Đợt đó không có independent multi-language composition holdout trong phạm vi bằng chứng của task này. Oracle ở `tools/evaluation/run_human_review_probe.py:21` phân biệt route và `evidence_status`; scorer mới ban đầu đã bỏ mất phần thứ hai và được sửa tại `tools/evaluation/evaluate_intent_router.py:37,97`.

**Đã sửa:** khóa corpus trước edits; lưu toàn bộ raw model/API/SQL write observations; hash source/config/training/DB tại đầu và cuối stage; không xem chi tiết lỗi giữ riêng để tune; không đổi nhãn HARD-VI-018 hoặc oracle để coi `multi_task` là `knowledge_abstain`. Mỗi completed stage 105 cases, có breakdown ngôn ngữ. Source, câu và nhãn giữ riêng không nằm trong runtime/training. Stage bị ngắt được lưu riêng, không trộn số với stage hoàn chỉnh.

**Phần bù gỡ:** không dùng 11/11 training/development làm kết luận độc lập. Scorer sai được sửa bằng transcript, không sửa expected labels. Chưa có người độc lập duyệt tập mới; cỡ mẫu en/zh/ko mỗi 3 chỉ là smoke coverage.

**Số đo:** bảng chung. HARD-VI-018: trước route `multi_task`, sau `knowledge`; giữ nguyên nhãn `knowledge_abstain`. Chỉ đối chiếu với oracle, chưa có human adjudication để kết luận nhãn quá hẹp.

## Test và kiểm tra đã chạy

Mọi pytest đều qua `python tools/runtime/run_offline_tests.py --timeout 600 -- <files>`, không chạy full suite; lọc `-k "not voice"` ở cohort chứa test voice. Không sửa/xóa test sẵn có, không tăng keyword budget.

1. Baseline 11 file: `test_commands`, `test_command_cpu_contract`, `test_command_semantics`, `test_grounded_service`, `test_semantic_authorization`, `test_selector_cold_index`, `test_slm_readiness`, `test_item_fidelity`, `test_understanding_layers`, `test_no_hardcode`, `test_no_case_specific_rules`.

   ```text
   327 passed, 2 warnings in 19.61s
   ```

2. Development đầu: 8 file liên quan; có ba lỗi structural scope, đã sửa nguyên nhân, không sửa test. Argv từng file của lượt sớm này không được lưu riêng; chỉ giữ summary đã trả khi chạy, không suy đoán thêm tên file.

   ```text
   3 failed, 281 passed, 2 warnings in 6.65s
   ```

3. Cohort baseline cộng test contract mới sau lần sửa đó:

   ```text
   341 passed, 2 warnings in 21.16s
   ```

4. Runtime development: `test_intent_router_contract`, `test_agent_runtime`, `test_e2e_guest_journeys`, `test_change_confirmation`, `test_command_agent_loop`, `test_semantic_authorization`, `test_domain_profile`, không voice. Fail mới ở execution + question, đã sửa scope algorithm.

   ```text
   FAILED tests/agent/test_agent_runtime.py::test_service_and_information_clause_create_both_governed_requirements
   1 failed, 211 passed, 2 warnings in 61.68s (0:01:01)
   ```

5. Source-index/scope cohort: `test_intent_router_contract`, `test_agent_runtime`, `test_selector_cold_index`, `test_semantic_authorization`, `test_grounded_service`.

   ```text
   208 passed, 2 warnings in 5.97s
   ```

6. 20 file chọn lọc, gồm 12 file baseline/contract và `test_agent_runtime`, `test_e2e_guest_journeys`, `test_change_confirmation`, `test_command_agent_loop`, `test_domain_profile`, `test_autonomous_concierge`, `test_multilingual_conversation_e2e`, `test_pending_read_boundaries`:

   ```text
   FAILED tests/agent/test_autonomous_concierge.py::test_model_command_reenters_governed_service_flow
   FAILED tests/agent/test_autonomous_concierge.py::test_unmatched_schedule_activity_abstains_without_rag_fallback
   FAILED tests/agent/test_multilingual_conversation_e2e.py::test_where_question_reads_knowledge_once_per_turn
   3 failed, 478 passed, 8 deselected, 2 xfailed, 2 warnings in 93.77s (0:01:33)
   ```

   Ba fail này nằm đúng danh sách lỗi có sẵn do người dùng nêu. Test voice fail có sẵn không chạy. Resource guard: 3 socket attempts bị chặn, 0 kết nối thật, 0 heavy model loads.

7. Sau sửa quantity particle: contract/item/commands/semantic/no-hardcode/no-case-specific.

   ```text
   224 passed, 2 warnings in 15.59s
   ```

8. `tests/domain/test_service_slot_natural_multilingual.py`, `tests/data/test_numerals.py`:

   ```text
   6 passed, 1 warning in 0.80s
   ```

9. Sau sửa noun-only predicate, 10 file: contract, semantic, item fidelity, grounded service, agent runtime, commands, command semantics, understanding layers, no-hardcode, no-case-specific; không voice.

   ```text
   344 passed, 2 warnings in 19.01s
   OFFLINE_RESOURCE_GUARD {'real_socket_attempts_blocked': 0, 'heavy_import_attempts_blocked': 0} real_network_connections=0 heavy_model_loads=0
   ```

10. Cohort tích hợp trên code sau lượt model hoàn chỉnh: `test_e2e_guest_journeys`, `test_change_confirmation`, `test_command_agent_loop`, `test_domain_profile`, `test_autonomous_concierge`, `test_multilingual_conversation_e2e`, `test_pending_read_boundaries`, không voice:

    ```text
    FAILED tests/agent/test_autonomous_concierge.py::test_model_command_reenters_governed_service_flow
    FAILED tests/agent/test_autonomous_concierge.py::test_unmatched_schedule_activity_abstains_without_rag_fallback
    FAILED tests/agent/test_multilingual_conversation_e2e.py::test_where_question_reads_knowledge_once_per_turn
    3 failed, 116 passed, 8 deselected, 2 xfailed, 2 warnings in 67.60s (0:01:07)
    OFFLINE_RESOURCE_GUARD {'real_socket_attempts_blocked': 3, 'heavy_import_attempts_blocked': 0} real_network_connections=0 heavy_model_loads=0
    ```

    Danh sách fail không tăng. Cohort này và 344 test scope/slot/gate ở mục 9 chạy sau lần sửa runtime cuối cùng.

11. Các lệnh kiểm tra ngoài pytest:

    ```text
    python -m compileall -q src tools
    compileall exit_code=0

    python tools/config/repin_configs.py --check
    configuration hashes are up to date

    python tools/validate/audit_data.py
    ALL STRUCTURAL + SEMANTIC + RUNTIME ARTIFACT AUDIT CHECKS PASSED!
    ```

    `repin_configs.py` được chạy khi sửa config; `--check` pass. Chỉ service descriptions đổi so snapshot; toàn bộ `semantic_authorization` và `nlu` nguyên byte dữ liệu tương đương, schema và runtime profile sources giữ nguyên công việc người dùng.

    `bandit -q -r src/concierge_kiosk tools -ll` trả exit 1: còn 1 Medium B608 ở `tools/runtime/replay_guest_transcript.py:149`, file không bị sửa trong task. B310 của harness mới đã sửa qua loopback inventory API helper, không thêm ignore/allowlist.

    Bandit trên tám file runtime sửa trong task và harness mới, với `-ll`:

    ```text
    bandit_changed exit_code=0
    ```

Các lần kiểm tra không thu được pytest verdict: một lần bị resource guard từ chối do RAM <1 GiB khi model đang resident; một lần bootstrap config description >240 ký tự trước khi pytest collection (đã rút description, không nới schema); một lần chỉ định nhầm `tests/agent/test_service_slots.py` không tồn tại, exit 4 (đã chạy các file đúng). Không gọi chúng là test pass. Các warnings hiện hữu về pytest/anyio/httpx không bị giấu.

## Những việc chưa kiểm chứng và GO/NO-GO

<!-- ROUTER_DECISION -->
**Toàn bộ thay đổi hiện NO-GO cho release; nhiệm vụ xử lý tận gốc P1–P7 chưa hoàn tất.** Không có commit/push hoặc release. Đặc biệt chưa đạt yêu cầu KO không hồi quy.

| Ngôn ngữ | Quyết định | Bằng chứng và giới hạn |
|---|---|---|
| vi | NO-GO | Giữ riêng 9/16, còn một goal/proposal sai; câu bẫy 0 write/0 proposal sai nhưng đúng nhãn 65/80. |
| en | NO-GO | 1/3 đúng; không thêm wrong goal/proposal ở mẫu này, chưa đủ coverage. |
| zh | NO-GO cho release | 3/3 trong smoke giữ riêng, tốt hơn 1/3 baseline; chỉ n=3, chưa kiểm toàn bộ slot và cold startup vẫn fail. |
| ko | NO-GO, có hồi quy | Đúng giảm 2/3 → 1/3; p50 tăng 15,588 → 24,378 giây. Không chỉnh theo các lỗi này để biến holdout thành development. |

Chưa làm/đo: independent human review; full slot fidelity trên real-model holdout; đủ mẫu multilingual/hard-negative ngoài VI; ablation P1–P5; loại hẳn closed ontology; proof để gỡ transport guards; successful cold-start guest API trong 30 giây; full suite/voice/browser; cloud tracing; deployment. Ba lỗi test có sẵn vẫn còn. Các thay đổi chưa được nghiệm thu để đưa vào production.

Chi tiết lỗi giữ riêng trong `diagnostics` chỉ dùng để báo cáo sau khi hoàn thành stage; runtime/training không chỉnh tiếp sau khi xem. Cần một bộ đánh giá độc lập mới được khóa trước vòng phát triển tiếp theo nếu muốn ước lượng tổng quát hóa sau các sửa tiếp theo.
<!-- ROUTER_DECISION_END -->
