# Kế hoạch hoàn thiện: clean code và loại bỏ hardcode theo trường hợp

Cập nhật: 2026-10-07. Lộ trình tổng của sản phẩm vẫn là `plan.md`; tài liệu này là luồng việc **làm sạch và chống hardcode** để đưa dự án tới trạng thái phát hành. Danh mục ca kiểm thử và cách chạy nằm ở [BACKEND-TEST-PLAN.md](BACKEND-TEST-PLAN.md); ở đây chỉ trỏ tới ID ca test.

## 0. Mục tiêu và nguyên tắc

**Xong khi:**
1. Không còn logic theo trường hợp trong `src/`: không so sánh literal mã dịch vụ/loại thực thể, không nhánh theo ngôn ngữ, không ngưỡng "đếm từ" để đoán ý định.
2. `config/agent-domain.json` chỉ còn tài nguyên ngôn ngữ, policy và an toàn; đổi khách sạn không phải sửa file này.
3. Hiểu câu (dịch vụ, hỏi thông tin, huỷ/đổi, hỏi tiếp, xã giao, sở thích, kế hoạch) đến từ **command của SLM + ví dụ đã duyệt + embedding**, không từ danh sách cụm từ.
4. Training không chứa câu của bộ đánh giá hay của kế hoạch test; có test chặn tự động.
5. Số đo trên bộ đánh giá độc lập không thấp hơn baseline trung thực (đo sau khi gỡ dữ liệu nhiễm, §2).
6. Mã nguồn đạt ngưỡng clean code ở §5.

**Nguyên tắc:**
- **Ngôn ngữ ở config, khách sạn ở dataset, ý định học từ ví dụ.**
- **Giữ deterministic ở chỗ an toàn bắt buộc**: khẩn cấp, cổng xác nhận ghi, kiểm slot là chữ của khách, ngữ pháp số/giờ.
- **Câu bị hiểu sai → thêm ví dụ khác cách nói, không thêm cụm từ, không chép câu test.**
- **Mỗi thay đổi có số đo trước/sau** trên bộ đánh giá không dùng để huấn luyện.
- **Có nhánh "không chắc"**: dưới ngưỡng thì hỏi lại hoặc rơi về đường an toàn, không đoán.

## 1. Hiện trạng (kiểm 2026-10-07)

### 1.1 Báo cáo của Codex — đã xác minh

| Nội dung báo cáo | Kết quả xác minh |
|---|---|
| Lỗi secret rỗng trong `.env.example` đã có fallback dev | ✅ Đúng: source `.env.example` → secret dài 42 ký tự (`core/settings.py`) |
| Đã bỏ `catalog_service_goal_in_text` | ✅ Đúng; nhưng thay bằng tra cứu nguyên văn `exact_commands` (xem 1.4) |
| Regex prompt injection đã ra config | ✅ Đúng (`security.prompt_injection_patterns`); allowlist giảm 1 dòng |
| `venue_slot`, `applies_to_slot` thay hardcode dining | ⚠️ Một phần: tour dùng `venue_slot.name = "note"` (nhét tên tour vào ghi chú) |
| Ma trận API 102/102, ghi 12/12, journey 25/25, pytest 536 pass | ⚠️ Không thể dùng làm bằng chứng chất lượng: 41 câu train được thêm từ chính đề test + có đường tra cứu nguyên văn (1.4) |
| FAIL còn lại: robustness 1/40 (vi bỏ hết dấu), fact-holdout 2/12 (ko/zh), compositional 4/12 (sai thuộc tính), fallback-only 0/1 mỗi ngôn ngữ | Ghi nhận → §3 |
| Chưa chạy: INF-5, ROB/DLG TTL/staff ETA/Piper, pass^5, pip-audit, Bandit, `npm ci && build`, smoke trình duyệt, khởi động lại từ `.env.example` | Ghi nhận → §3 |

### 1.2 Danh sách keyword trong code

- 49 danh sách cụm từ/regex được export từ `agent/understanding/domain_nlu.py`; **3 danh sách không còn dùng**: `ROOM_REFERENCE_TERMS`, `READ_ROUTE_TERMS`, `READ_INFO_MORE_TERMS`.
- Phụ thuộc nhiều nhất: `agent/understanding/routing.py` (15 danh sách), `intent.py` (6), `agent/tools/service_slots.py` (6), `agent/tools/numerals.py` (5), `authority.py`, `read_tasks.py`, `engine.py`, `agent/memory/heuristics.py` (4 mỗi file).
- 51 chỗ so khớp chuỗi con kiểu `term in normalized`.
- Heuristic đếm từ đoán ý định: `engine.py:788,790,796` (≤3 từ, ≤8 ký tự, ≤4 từ), `service_slots.py:225` (>4 từ), `numerals.py:214` (≤48 ký tự). Giới hạn độ dài đầu vào ở `rag/retrieval/*` (500 ký tự) là kiểm tra hợp lệ, giữ.
- `agent/tools/planning.py` dùng `REQUEST_CHANGE_TERMS` (từ khoá huỷ/đổi) làm tín hiệu lập kế hoạch — sai mục đích.

### 1.3 `config/agent-domain.json` — 2.631 chuỗi

| Nhóm | Số chuỗi | Ví dụ khoá | Xử lý |
|---|---:|---|---|
| **Giữ** (ngôn ngữ, policy, an toàn) | ~1.130 | `services`, `nlu.intent.emergency_event_patterns`, `nlu.slots.relative_time_terms`, `voice.number_rendering.*`, `rag.token_stopwords`, `nlu.routing.static_text`, `affirm/deny/confirmation_terms`, `security.*` | giữ, chỉ dọn trùng |
| **Kiến thức khách sạn** | 267 | `rag.document_domains.markers` 69, `memory_vocabulary.focus_aliases` 51, `subject_aliases` 50, `planning.categories.*` 64, `routing.bare_topic_terms` 16 | chuyển về dataset (HC-D1) |
| **Vá truy hồi** | 147 | `rag.query_fillers` 83, `rag.query_rewrites` 64 | bỏ dần, thay bằng retrieval/alias (HC-D2) |
| **Phân loại ý định bằng cụm từ** | ~1.087 | `followup_markers` 82, `facet_aliases` 68, `request_change_terms` 55, `info_only` 50, `cancel_terms` 47, `greeting_terms` 43, `authority.*` 94, `constraint_terms` 39, `courtesy_particles` 37, `availability_terms` 33, `preferences.*.recognition` 29… | thay bằng command/embedding (HC-B*, HC-C*) |

So với commit gần nhất: +77 chuỗi, trong đó nhiều cụm lấy từ câu trong kế hoạch test: `cancel_terms.vi` "không cần" (**rủi ro hành vi**: "phở không cần hành" khi đang có đề xuất có thể bị hiểu là huỷ), `followup_markers` "tối thì sao", `action_followup_terms` "đặt luôn", `planning.intent_cues` "gợi ý/muốn đi/lên lịch", `planning.categories.tour` "đi chơi", `mobility` "xe lăn", lời cảm ơn thêm vào cả `greeting_terms` lẫn `thanks_terms` mới, regex `query_rewrites` mới `\blàm sao\b`.

### 1.4 Training bị nhiễm đề test

- File mới `datasets/training/agent/backend_plan_gap_closure.jsonl` (37 câu, gắn `reviewed_router_training`/`GOLD` dù do agent viết): 25 câu trùng hoặc chứa câu trong kế hoạch test.
- Thêm vào file gốc (so với nhánh `dev-full-local`): `vi_gold` +3 ("Trả phòng muộn", "Lúc nãy dọn phòng chưa sạch", "Mang thêm khăn tắm"), `multilingual_support` +1 (câu ghép khăn + taxi của CMP-1). Các dòng khác không đổi.
- `ServiceSelector.exact_commands` (`service_selector.py:612`): câu khách trùng nguyên văn câu train → trả nhãn có sẵn, bỏ qua mô hình. Kết hợp hai điểm trên, nhiều ca test pass vì có đáp án sẵn.
- Không có câu train trùng `datasets/evaluation/` (ngoài simulation).

### 1.5 Dữ liệu

Chi tiết ở §4 (DAT-1..11). Tóm tắt: không có ví dụ xã giao; 4 dịch vụ 0 ví dụ; en/zh/ko ≈ 1/7,5 vi; thiếu nhãn cho chỉ đường, hỏi chỗ trống, hỏi tiếp, phủ định, phàn nàn; alias property lẫn thuật ngữ dịch vụ; bộ gold và multi-turn chỉ có tiếng Việt.

### 1.6 Chất lượng mã

| Chỉ số | Hiện trạng |
|---|---|
| Hàm > 120 dòng | 15+; lớn nhất `build_conversation_engine` 672 dòng, `build_answer_services` 599, `_execute_turn` 411, `register_guest_routes` 384, `grounded_answer` 316, `retrieve` 299, `Settings.validate` 294 |
| Module lớn | `engine.py` 1.143 dòng, `answers.py` 797, `settings.py` 762, `service_selector.py` 650, `service_actions.py` 641 |
| Độ phức tạp (ruff C901 > 15) | 62 hàm |
| Code chết (vulture ≥ 80%) | 13 mục, gồm `main.py:251` code sau `return`, biến không dùng ở `authority.py:65`, `service_actions.py:115`, `recognition.py:21`, `tts.py:35`, `incremental.py:18`; 3 danh sách keyword không dùng |
| Lặp | Danh sách tên slot ở 5 nơi; lời cảm ơn ở 2 khoá config |
| Test chặn hardcode | 1 test vô hiệu: `test_service_goal_is_not_selected_from_an_embedded_alias` truyền mã dịch vụ làm request kind → taxi bị tắt trong test |

---

## 2. Giai đoạn 0 — Dừng "học đề" và lấy baseline trung thực (làm ngay)

| ID | Việc | Chi tiết | Xong khi |
|---|---|---|---|
| G0-1 | Luật cho agent | Thêm vào `AGENT.md`: không thêm cụm từ vào `agent-domain.json` hay câu vào `datasets/training/` để làm pass một ca test; câu bị hiểu sai → ví dụ **khác cách nói** qua quy trình §4.4; mọi thay đổi số chuỗi trong `agent-domain.json` phải ghi lý do | Luật có trong `AGENT.md` |
| G0-2 | Tách đề khỏi bài học | Chuyển câu của [BACKEND-TEST-PLAN.md](BACKEND-TEST-PLAN.md) thành `datasets/evaluation/backend_plan/cases.jsonl` (ID ca test, input, kỳ vọng); script chạy đọc từ file này | Tất cả ca có trong file eval |
| G0-3 | Gỡ dữ liệu nhiễm | Xoá `backend_plan_gap_closure.jsonl` khỏi training (giữ bản sao để tham khảo khi viết DS-*); gỡ 4 dòng thêm vào `vi_gold`/`multilingual_support`; refresh manifest/contracts | 0 câu train trùng/chứa câu đề |
| G0-4 | Gỡ đường tra cứu nguyên văn | Bỏ `ServiceSelector.exact_commands` và chỗ gọi (`reviewed_commands` trong `engine.py`) | Không còn nhánh "câu trùng train → nhãn" |
| G0-5 | Gỡ cụm vá mới trong config | Hoàn tác các cụm liệt kê ở 1.3 (ưu tiên "không cần" trong `cancel_terms.vi`); gộp lời cảm ơn về một khoá | Diff `agent-domain.json` so với HEAD chỉ còn thay đổi hợp lệ (injection, venue_slot, applies_to_slot, slot label, static_text) |
| G0-6 | Test chặn tái phát | (a) không chép đề: câu train trùng/chứa câu trong `datasets/evaluation/**` → fail; (b) ratchet số chuỗi nhóm "phân loại ý định" + "vá truy hồi" trong `agent-domain.json` chỉ được giảm; (c) không có tên/alias thực thể trong `agent-domain.json`; (d) `src/` không so sánh literal với mã dịch vụ hay `entity_type`; (e) sửa test vô hiệu ở 1.6 dùng request kind thật | 5 test có trong `tests/`, chạy trong CI |
| G0-7 | Baseline trung thực | Chạy bộ đo §6.3 + toàn bộ ca test, lưu `reports/baseline-2026-10/` | Có baseline; mọi giai đoạn sau so với nó |

Kỳ vọng: sau G0-3..5 một số ca test sẽ FAIL trở lại. Đó là hiện trạng thật; xử lý ở giai đoạn 2–4 bằng dữ liệu và kiến trúc, không bằng cụm từ.

---

## 3. Giai đoạn 1 — Ổn định để demo

| ID | Mức | Việc | Vị trí | Kiểm bằng |
|---|---|---|---|---|
| BUG-1 | — | ✅ Đã sửa: fallback secret dev | `core/settings.py` | INF-1..3 |
| BUG-2 | Cao | Timeout hiểu câu 3,0 s, câu ghép cần ~3,4 s GPU warm → tăng timeout hoặc rút gọn output; đo CPU | `config/runtime-profiles/src/base.json:143` | CMP-1..4 × 3, GPU và CPU |
| BUG-3 | Trung | `restaurant_name` từ client chưa kiểm với dataset | `api/shared/contracts.py`, prepare | WR-9 |
| BUG-4 | Thấp | "nhà hàng ở đâu?" kèm handoff thừa | navigation/answers | KB-2 |
| BUG-5 | ⚠️ | Đề xuất chờ lưu theo ngôn ngữ, đổi ngôn ngữ có thể mất | `agent/memory/task_memory.py` | DLG-5 |
| BUG-6 | ⚠️ | Số lượng tiếng Việt ở `details` và `payload` | `agent/tools/service_slots.py` | SVC-3, SVC-4 |
| BUG-7 | CI | Thêm kiểm `source-sha256` bundle + smoke Playwright `/`, `/staff` | `.github/workflows/ci.yml` | INF-4 |
| BUG-8 | Repo | Commit `presentation/turn.py`, `frontend/build_offline.cjs` | git | `git status` |
| BUG-9 | Demo | Huỷ ticket test trong DB demo | `data/concierge.sqlite3` | STF-1 |
| BUG-10 | Trung | Tiếng Việt bỏ hết dấu không vượt ngưỡng fallback (robustness 1/40) | `service_selector` fallback | KB-7, `robustness_report.py` — xử lý gốc bằng DS-4 (biến thể không dấu) |
| BUG-11 | Trung | Fact-holdout 2/12 (ko/zh), compositional 4/12 (đúng thực thể, sai thuộc tính) | `rag/retrieval/*`, `rag/grounding/*` | `run_retrieval_eval.py`; phân tích từng miss, sửa ở truy hồi theo `fact_type`/`fact_context`, không thêm rewrite |
| BUG-12 | Trung | Fallback không model (`--fallback-only`) hiểu "mang adapter" thành knowledge | `ServiceSelector.fallback_goal` | `evaluate_command_understanding.py --fallback-only` — xử lý gốc bằng DS-3/DS-4 |
| BUG-13 | Kiểm | Chưa chạy: INF-5, ROB-*, DLG-10, STF-6, VOC-*, pass^5, `pip_audit`, `bandit`, `npm ci && build`, smoke trình duyệt, khởi động lại từ `.env.example` | — | Gate CI (BACKEND-TEST-PLAN §2) + nhóm test tương ứng |

Xong giai đoạn khi: BUG-2..9, BUG-13 đóng; INF, SEC, CHAT, KB, SVC, WR, EMG pass trên baseline trung thực (BUG-10..12 được phép còn mở, xử lý ở giai đoạn 2–4).

---

## 4. Giai đoạn 2 — Dữ liệu (song song với giai đoạn 3)

### 4.1 Hiện trạng

Training `datasets/training/agent/` (không tính file nhiễm): 995 ví dụ — vi 712, en 95, zh 94, ko 94; concept có ví dụ: vi 122, en 66, zh 65, ko 65.

Route (vi/en/zh/ko): service 311/44/44/44 · status 73/7/7/7 · knowledge 70/12/12/12 · request_change 54/8/8/8 · emergency 46/6/6/6 · clarification 36/4/4/4 · knowledge_abstain 25/5/5/5 · escalation 17/2/2/2 · reopen 16/1/1/1 · non_action 16/0/0/0 · multi_step 12/3/2/2 · policy_guard 12/2/2/2 · privacy_guard 12/1/1/1 · safety_escalation 12/0/0/0.

Dịch vụ (vi/en/zh/ko): human_assistance 80/11/11/11 · maintenance 57/8/8/8 · housekeeping 39/5/5/5 · amenity_delivery 36/6/6/6 · transport_request 30/4/4/4 · dining_reservation 29/5/5/5 · spa_reservation 16/2/2/2 · food_order 14/2/2/2 · late_checkout 13/1/1/1 · tour_reservation 7/1/1/1 · wake_up_call, facility_request, dining_request, tour_request: 0.

Knowledge: 119 thực thể có tên và alias đủ 4 ngôn ngữ; 14 mã dịch vụ có alias 4 ngôn ngữ. Evaluation: retrieval, `service_actions`, `holdout/service_workflow`, `challenges/natural` đủ 4 ngôn ngữ; `gold/*`, `journeys/vi_multi_turn` chỉ vi.

### 4.2 Vấn đề

| ID | Mức | Vấn đề | Chặn bước |
|---|---|---|---|
| DAT-1 | Cao | Không có route xã giao (chào, cảm ơn, tạm biệt, đổi ngôn ngữ) | HC-B1 |
| DAT-2 | Cao | 4 dịch vụ 0 ví dụ | HC-D3, BUG-12 |
| DAT-3 | Cao | en/zh/ko mỏng; nhiều lớp 1–2 ví dụ/ngôn ngữ | mọi HC-* |
| DAT-4 | Cao | Thiếu nhãn: chỉ đường, kế hoạch, hỏi chỗ trống/điều kiện, hỏi tiếp, phủ định, phàn nàn, sở thích | HC-C2..C5 |
| DAT-5 | Trung | `request_change` chưa tách `cancel`/`modify`; `safety_escalation`, `non_action` không có en/zh/ko | HC-C1 |
| DAT-6 | Trung | Nhãn slot `time` thay `preferred_time` (`TRAIN-92066A0E72FF`, `TRAIN-AFE07248243E`) | — |
| DAT-7 | Trung | 5 câu train trùng `evaluation/end_to_end/journeys/production.jsonl` + 3 gần trùng | baseline |
| DAT-8 | Trung | Alias `property.furama_resort_danang` chứa thuật ngữ dịch vụ ("trả phòng muộn", "laundry", "wifi"…) | HC-D1 |
| DAT-9 | Thấp | 4/6 nhà hàng chỉ 1 alias/ngôn ngữ | HC-D2, SVC-9..11 |
| DAT-10 | Thấp | Chỉ 1 dòng có `commands` tường minh; multi_step mỏng | HC-C1, CMP-* |
| DAT-11 | Đo | Eval xã giao/hỏi tiếp/huỷ/phủ định chỉ có tiếng Việt | đo HC-B*, HC-C* |

### 4.3 Việc cần làm

| ID | Việc | Mục tiêu | Giải quyết |
|---|---|---|---|
| DS-1 | Sửa nhãn DAT-6; gỡ câu trùng/gần trùng khỏi training | 0 trùng | DAT-6, DAT-7 |
| DS-2 | Route xã giao: greeting, thanks, goodbye, chitchat, language_switch; kèm hard negative "chào + yêu cầu" (nhãn = yêu cầu) | ≥ 16 câu/route/ngôn ngữ | DAT-1 |
| DS-3 | Ví dụ cho 4 dịch vụ trống, đủ và thiếu slot | ≥ 12 vi, ≥ 8/ngôn ngữ khác | DAT-2 |
| DS-4 | Nâng lớp mỏng; thêm biến thể không dấu/gõ sai qua `tools/nlu/perturb.py` (chỉ trên train) | ≥ 8 câu/lớp/ngôn ngữ | DAT-3, BUG-10 |
| DS-5 | Nhãn mới: navigation, availability/conditional, followup (có ngữ cảnh lượt trước), cancel vs modify, phủ định, phàn nàn, set_preference, plan | ≥ 8 câu/nhãn/ngôn ngữ; phủ định và phàn nàn ≥ 16 vi | DAT-4, DAT-5 |
| DS-6 | Trường `commands` tường minh cho multi_step, request_change, clarification | mọi dòng các route này | DAT-10 |
| DS-7 | Chuyển thuật ngữ dịch vụ khỏi alias property sang `aliases_by_service_code` | 0 thuật ngữ dịch vụ trong alias property | DAT-8 |
| DS-8 | Alias nhà hàng/tiện ích từ nguồn đã xác minh | ≥ 3 alias/ngôn ngữ | DAT-9 |
| DS-9 | Bộ eval đa ngôn ngữ độc lập cho xã giao, hỏi tiếp, huỷ/đổi, phủ định, sở thích | ≥ 20 câu/nhóm/ngôn ngữ | DAT-11 |

### 4.4 Quy trình thêm ví dụ

1. Viết câu và 2–3 cách nói tương đương (người viết hoặc LLM sinh rồi người duyệt), nhãn đủ (`expected_route`, `service_code`, `expected_slots`, `commands`), đủ 4 ngôn ngữ nếu được.
2. Đi qua pipeline có sẵn (`humanization_batch`, `source_family`, `source_registry.jsonl`); `gold_status` chỉ là `GOLD` khi người đã duyệt.
3. Không lấy câu từ `datasets/evaluation/`; test G0-6(a) phải xanh.
4. `python datasets/schemas/validate_contracts.py`, `tools/manifest/refresh_dataset.py`, `tools/validate/schemas.py`, `semantics.py`, `agent_domain.py`; sửa alias → knowledge rebuild (CLAUDE.md).
5. `python tools/nlu/calibrate_service_fallback.py --model "ollama://bge-m3" --manifest models/embeddings/bge-m3.ollama.manifest.json`.
6. Đo §6.3, so baseline.

---

## 5. Giai đoạn 3 — Clean code (song song với giai đoạn 2, trước giai đoạn 4)

Làm sạch cấu trúc **không đổi hành vi** trước, để các bước bỏ hardcode sau đó chỉ chạm module nhỏ, dễ review.

| ID | Việc | Cách làm | Xong khi |
|---|---|---|---|
| CC-1 | Test đặc tả hành vi trước khi tách | Ghi lại output hiện tại của bộ ca test API (golden snapshot theo ID ca) để so sau mỗi refactor | Snapshot trong `reports/baseline-2026-10/` |
| CC-2 | Tách `application/conversation/engine.py` | `_execute_turn` (411 dòng) → các bước có tên: chuẩn bị ngữ cảnh, hiểu câu, chọn route, chạy agent, chiếu kết quả; `build_conversation_engine` (672) → factory nhỏ + các capability ở module riêng | Không hàm nào > 80 dòng; snapshot không đổi |
| CC-3 | Tách `answers.py` (`build_answer_services` 599, `grounded_answer` 316) | Theo capability: knowledge, navigation, planning, abstention | Như CC-2 |
| CC-4 | Tách `api/guest/routes.py` (`register_guest_routes` 384), `api/voice/streaming.py` | Mỗi nhóm route một hàm đăng ký | Như CC-2 |
| CC-5 | `core/settings.py` (`validate` 294, `load_settings` 212) | Tách theo khối cấu hình (security, slm, rag, voice), validator riêng | Như CC-2 |
| CC-6 | Gom tên slot về một nguồn | Registry `accepted_slots`/hằng chung; `engine.py`, `service_actions.py`, `submissions.py`, `ServicePayload`, `api-contracts.ts` dùng chung | 1 định nghĩa |
| CC-7 | Xoá code chết | 13 mục vulture, 3 danh sách keyword không dùng, `main.py:251` | vulture ≥ 80% sạch |
| CC-8 | Gate lint | Thêm `ruff` (C901 ≤ 15 cho code mới, ratchet số vi phạm hiện có 62 chỉ được giảm), `vulture --min-confidence 80` vào CI | CI chạy và xanh |

Quy tắc: mỗi CC một PR nhỏ; chạy pytest + snapshot CC-1 + gate CI (BACKEND-TEST-PLAN §2); không gộp refactor với thay đổi hành vi.

---

## 6. Giai đoạn 4 — Bỏ hardcode theo kiến trúc đích

### 6.1 Kiến trúc đích (4 lớp)

| Lớp | Vai trò | Thành phần | Trạng thái |
|---|---|---|---|
| A. An toàn deterministic | Khẩn cấp, cổng xác nhận ghi, kiểm slot là chữ của khách, ngữ pháp số/giờ | `classify_dialogue` (chỉ emergency), `validate_commands`, `numerals.py`, `emergency_event_patterns` | Giữ; chỉ dọn |
| B. Router nhanh bằng embedding | Xã giao, trả lời slot ngắn khi server đang hỏi, huỷ đề xuất đang chờ — không gọi SLM | kNN bge-m3 trên ví dụ train + ngưỡng hiệu chỉnh (conformal) | Mở rộng từ `ServiceSelector.fallback_goal` |
| C. SLM sinh command | Mọi ý định còn lại: `StartGoal`, `SetSlot`, `CorrectSlot`, `Cancel`, **`Modify`**, **`Clarify`**, `AskInfo`, `Navigate`, `Handoff`, `ChitChat`, **`SetPreference`**, **`Plan`** | `commands.py` với JSON schema đóng; few-shot lấy theo kNN cho **mọi loại command**; mô tả dịch vụ/slot bằng ngôn ngữ tự nhiên trong registry | Mở rộng |
| D. Dữ liệu và truy hồi | Tên/alias thực thể, dịch vụ; truy hồi dense + rerank | `datasets/knowledge/canonical/`, `domain-vocab.json`, `rag/retrieval/` | Chuyển từ config về |

Luồng một lượt: A (khẩn cấp thắng) → B (nếu chắc chắn) → C (một lần gọi SLM) → server kiểm lại → policy/tool. Dưới ngưỡng ở B → sang C; C không hợp lệ → `Clarify` hoặc đọc kiến thức (không đoán).

### 6.2 Các bước

| ID | Bỏ / chuyển | Thay bằng | Code | Cần dữ liệu | Kiểm bằng | Qua khi |
|---|---|---|---|---|---|---|
| HC-D1 | Kiến thức khách sạn trong config (267 chuỗi: `subject_aliases`, `focus_aliases`, `bare_topic_terms`, `planning.categories.*`, `rag.document_domains.markers`, `facet_search`) | Alias dataset → `domain-vocab.json`; accessor đọc vocab | `domain_nlu.py`, `normalization.py`, `agent/memory/heuristics.py`, `agent/tools/planning.py`, `rag/documents.py` | DS-7, DS-8 | `run_retrieval_eval.py` (3 suite), KB-*, REF-*, PLAN-* | Recall@5/grounded không tụt > 0,5 điểm/ngôn ngữ |
| HC-D2 | `rag.query_rewrites`, `rag.query_fillers` (147 chuỗi) | Bỏ từng luật; tụt thì thêm alias dữ liệu; xử lý BUG-11 bằng truy hồi theo `fact_type` | `rag/grounding/relevance.py`, `core/domain_profile/*` | DS-8 | `run_retrieval_eval.py` có/không rerank | Như HC-D1; mỗi luật có báo cáo trước/sau |
| HC-D3 | `exact_catalog_service_goal` (khớp nguyên câu với alias); `venue_slot` của tour trỏ `note` | Quyết định giữ/bỏ khớp alias nguyên câu dựa trên số đo; slot địa điểm riêng cho tour/spa | `service_selector.py`, registry, `agent-domain.json` | DS-3, DS-8 | SVC-*, NEG-*, `--fallback-only` | NEG = 0 FAIL; SVC-13 có tên tour đúng slot |
| HC-B1 | `greeting_terms` 43, `thanks_terms`, `courtesy_particles` 37, `language_switch_terms` 16 | Router nhanh lớp B với ví dụ xã giao | `routing.py`, `engine.py` | DS-2, DS-9 | CHAT-1..9 cả 4 ngôn ngữ | CHAT = 0 FAIL |
| HC-B2 | Heuristic trả lời slot ngắn (`engine.py:777-800`: ≤3 từ, ≤8 ký tự, ≤4 từ, `IMPERATIVE_PATTERNS`, `ACTION_FOLLOWUP_TERMS`), `service_slots.py:225`, `cancel_terms` 47 | Khi server đang hỏi slot: `SetSlot`/`Cancel` trong schema SLM (đã có) + router lớp B cho câu rất ngắn | `engine.py`, `service_slots.py`, `routing.py` | DS-5 | DLG-1..10, NEG-1 | DLG không thêm FAIL; "phở không cần hành" không huỷ |
| HC-C1 | `request_change_terms` 55, `request_change_reference_terms` 23, `request_status_terms` 16; `Cancel` lấn `StartGoal` | Command `Cancel` + `Modify` (+ `request_id` tham chiếu) từ SLM; xử lý nhiều command cùng lượt | `service_actions.py` (manage_request), `engine.py` (`_decision_from_commands`), `commands.py` | DS-5, DS-6 | DLG-6, DLG-13, DLG-14, NOREQ-*, CMP-4 | 0 huỷ nhầm; độ chính xác huỷ/đổi ≥ baseline |
| HC-C2 | `info_only` 50, `read_intent.*` ~84, `availability_terms` 33 | `AskInfo` vs `StartGoal` vs kiểm chỗ trống (`conditional`) từ SLM với few-shot mọi loại command; fallback kNN; SetFit nếu kNN không đủ | `intent.py` (`is_information_question`), `read_tasks.py`, `service_selector.py`, `commands.py` | DS-4, DS-5 | NEG-*, CHAT-3..5, CMP-3, `robustness_report.py` | NEG = 0 FAIL; recall dịch vụ ≥ baseline |
| HC-C3 | `followup_markers` 82, `facet_aliases` 68, `ambiguous_reference_markers` 16, `action_followup_terms` 15 | Ngữ cảnh hội thoại trong prompt + `reference_resolver`; command `Clarify` khi mơ hồ; facet (giờ/giá/vị trí) từ ví dụ | `agent/memory/conversation.py`, `heuristics.py`, `reference_resolver.py` | DS-5, DS-9 | REF-1..8, `api_multiturn_probe.py` | REF không thêm FAIL; p50 lượt hỏi tiếp tăng ≤ 300 ms |
| HC-C4 | `planning.intent_cues`, `planning.constraints.constraint_terms` 39; `planning.py` dùng `REQUEST_CHANGE_TERMS` | Command `Plan` (ràng buộc thời gian/sở thích là slot) | `agent/tools/planning.py`, `commands.py` | DS-5 | PLAN-1..3, PREF-4 | PLAN = 0 FAIL |
| HC-C5 | `preferences.fields.*.recognition` (cụm nhận diện sở thích) | Command `SetPreference` với enum đóng theo `preferences.fields` | `agent/memory/preferences.py`, `commands.py` | DS-5 | PREF-1..6 | PREF = 0 FAIL; không lan sang phiên mới |
| HC-C6 | `authority.explicit/restricted/tentative_terms` 94 | Làm cuối (an toàn ghi); thu gọn sau khi HC-C1, HC-C2 ổn và có bộ test "nói chắc/dè dặt" | `agent/understanding/authority.py` | DS-5 | WR-*, SAFE-5, DLG-11, DLG-12 | 0 ghi sai, 0 bypass xác nhận |

Thứ tự gợi ý: HC-D1 → HC-D3 → HC-B1 → HC-C1 → HC-B2 → HC-C2 → HC-C3 → HC-C4 → HC-C5 → HC-D2 → HC-C6.

Mục tiêu cuối: `agent-domain.json` còn khoảng 1.100–1.200 chuỗi (nhóm "giữ"); nhóm "kiến thức khách sạn", "vá truy hồi" và "phân loại ý định bằng cụm từ" về 0 hoặc chỉ còn ngoại lệ có ghi lý do.

### 6.3 Đo trước/sau cho mỗi bước

```bash
python tools/evaluation/evaluate_command_understanding.py
python tools/evaluation/evaluate_command_understanding.py --fallback-only
python tools/nlu/perturb.py && python tools/nlu/robustness_report.py
python tools/evaluation/run_retrieval_eval.py --suite grounded      --output reports/<ID>/after/grounded.json
python tools/evaluation/run_retrieval_eval.py --suite fact_holdout  --output reports/<ID>/after/fact_holdout.json
python tools/evaluation/run_retrieval_eval.py --suite compositional --output reports/<ID>/after/compositional.json
python tools/evaluation/run_human_review_probe.py --lang vi --output reports/<ID>/after/vi.json   # và en, zh, ko
python tools/evaluation/run_tool_eval.py --tasks <tasks.jsonl> --base-url http://localhost:8000 --output reports/<ID>/after/tool.json --repeats 5
```
Cộng nhóm ca test ghi ở cột "Kiểm bằng". Laptop: bản rút gọn; bộ đầy đủ chạy trên máy thuê.

Ngưỡng qua (đề xuất, chỉnh theo độ nhiễu baseline):
- Khẩn cấp: recall 100% trên bộ đã duyệt.
- Command accuracy / selector recall: không tụt > 1 điểm ở bất kỳ ngôn ngữ nào.
- Retrieval grounded / fact_holdout: không tụt > 0,5 điểm; 0 ca giá/giờ không evidence.
- Ca test: không thêm FAIL so với baseline G0-7.
- pass^5: không thấp hơn baseline.
- p50 lượt text: không tăng > 10%.

---

## 7. Giai đoạn 5 — Phát hành

| ID | Việc |
|---|---|
| REL-1 | Gate CI đầy đủ (BACKEND-TEST-PLAN §2 + CC-8 + G0-6) xanh |
| REL-2 | Bộ đo §6.3 đầy đủ trên máy thuê, chế độ CPU (`num_gpu=0`), lưu `reports/release/<version>/` |
| REL-3 | Toàn bộ ca test BACKEND-TEST-PLAN, kể cả [CONFIRM] trên DB copy, STF-*, VOC-* |
| REL-4 | `reports/release/<version>/signoff.json` theo `plan.md` §0.4 (kỹ thuật, nghiệp vụ, an toàn, release) |
| REL-5 | Cập nhật `CLAUDE.md`/`docs/` cho kiến trúc mới (lớp B, command mới, nơi đặt dữ liệu) |

---

## 8. Tham khảo

Kiến trúc hiện tại gần như trùng Rasa CALM (SLM sinh command, chỉ đưa top-k dịch vụ vào prompt, nghiệp vụ deterministic): hướng đi đúng, việc còn lại là dùng command thay cụm từ ở các chỗ còn sót.

| Nguồn | Ý tưởng | Dùng ở |
|---|---|---|
| Rasa CALM — [arXiv:2402.12234](https://arxiv.org/abs/2402.12234), [rasa-calm-demo](https://github.com/RasaHQ/rasa-calm-demo), [Command Generator](https://rasa.com/docs/pro/customize/command-generator) | Chuỗi command (`StartFlow`, `SetSlot`, `CorrectSlot`, `CancelFlow`, `Clarify`, `ChitChat`, `KnowledgeAnswer`, `HumanHandoff`); flow retrieval top-k | Lớp C; HC-C1, HC-C2, HC-C3 |
| Rasa command-generator model — [HF](https://huggingface.co/rasa/cmd_gen_llama_3.1_8b_calm_demo) | Fine-tune model nhỏ chỉ để sinh command, đo F1 theo loại | Sau cùng, nếu `qwen2.5:3b` chưa đủ |
| NeMo Guardrails — [architecture](https://docs.nvidia.com/nemo/guardrails/latest/architecture/README.html) | kNN lấy top-5 ví dụ đưa vào prompt sinh ý định | Few-shot mọi loại command (lớp C) |
| Parlant — [docs: guideline matching](https://parlant.io/docs/engine-internals/guideline-matching), [journeys](https://parlant.io/docs/engine-internals/journeys) | Ghép hướng dẫn/hành trình theo ngữ cảnh mỗi lượt thay vì luật cứng; journey có trạng thái | Tham khảo cho policy và đa lượt (HC-C3, HC-C6) |
| DSPy — [stanfordnlp/dspy](https://github.com/stanfordnlp/dspy) | "Biên dịch" prompt và few-shot từ train set theo metric, thay chỉnh tay | Tối ưu prompt/few-shot lớp C trên train, đánh giá trên eval |
| semantic-router — [aurelio-labs/semantic-router](https://github.com/aurelio-labs/semantic-router) | Route = câu ví dụ, định tuyến bằng embedding + ngưỡng, ~ms | Lớp B (HC-B1, HC-B2) |
| CICC — [arXiv:2403.18973](https://arxiv.org/abs/2403.18973) | Ngưỡng conformal; tập ứng viên > 1 → hỏi lại | Ngưỡng lớp B, `Clarify` |
| SetFit — [huggingface/setfit](https://github.com/huggingface/setfit), [arXiv:2209.11055](https://arxiv.org/abs/2209.11055) | Head phân loại nhỏ, 8–16 ví dụ/lớp, đa ngôn ngữ | HC-C2 nếu kNN không đủ |
| D3ST — [arXiv:2201.08904](https://arxiv.org/abs/2201.08904); Show, Don't Tell — [arXiv:2204.04327](https://arxiv.org/abs/2204.04327); SGP-TOD — [arXiv:2305.09067](https://arxiv.org/abs/2305.09067) | Mô tả intent/slot bằng ngôn ngữ tự nhiên; ví dụ tốt hơn mô tả; prompt theo schema | Mô tả dịch vụ/slot trong registry (lớp C) |
| Constrained decoding — [arXiv:2501.10868](https://arxiv.org/abs/2501.10868) | Đánh giá sinh JSON theo schema; model nhỏ cần ràng buộc grammar | Giữ JSON schema đóng khi thêm command mới |
| OpenAI customer-service agents demo — [openai-cs-agents-demo](https://gittrend.io/repo/openai/openai-cs-agents-demo) | Triage + handoff + guardrail liên quan/jailbreak | Tham khảo tổ chức agent, không dùng keyword |
| τ-bench — [sierra-research/tau-bench](https://github.com/sierra-research/tau-bench), [arXiv:2406.12045](https://arxiv.org/abs/2406.12045) | Chấm theo trạng thái DB cuối, pass^k | Đánh giá DLG/STF và `run_tool_eval.py` |

Không tìm thấy dự án concierge khách sạn mã nguồn mở đủ tin cậy để tham khảo kiến trúc.

---

## 9. Quyết định cần chủ dự án chốt

| # | Câu hỏi | Ảnh hưởng |
|---|---|---|
| Q1 | Giữ khớp alias nguyên câu (`exact_catalog_service_goal`) như đường nhanh có kiểm soát, hay bỏ hẳn? | HC-D3 |
| Q2 | Đổi ngôn ngữ giữa lúc đang điền slot: giữ hay huỷ đề xuất đang chờ? | BUG-5 |
| Q3 | Có chấp nhận fine-tune/LoRA model sinh command (P1 trong `plan.md`) nếu lớp C chưa đạt ngưỡng? | lớp C |
| Q4 | Ai duyệt dữ liệu train mới (để gắn `GOLD`)? | DS-*, G0-1 |
| Q5 | Ngưỡng §6.3 có giữ như đề xuất không? | mọi HC-* |
