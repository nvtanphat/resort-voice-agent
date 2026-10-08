# Reset hardcode: xoá sạch keyword/hardcode, làm lại cho đúng

Đối tượng đọc: agent lập trình (Codex). Làm tuần tự, không hỏi lại người dùng. Kết quả cuối: `reports/reset/REPORT.md`.

## 0. Vì sao làm thế này

Hơn một ngày gỡ keyword từng chút: điểm bài thi 140 → 146/184, budget 1473 → ~1013, mỗi bước tốn hàng giờ đo. Nút thắt thật là **model SLM yếu** (qwen2.5:3b, BFCL v3 58.0) chứ không phải số keyword; Qwen3-4B non-thinking đạt 76.0 và giỏi multi-turn hơn rõ (Qwen3 technical report). Vì vậy: **đổi model trước, rồi xoá hẳn toàn bộ hardcode ý định/kiến thức/vá truy hồi trong một lần, rồi phục hồi điểm bằng command + dữ liệu**, không gỡ từng cụm.

Đọc trước: `CLAUDE.md`, `AGENT.md`, `docs/COMPLETION-PLAN.md` mục 6.1 (kiến trúc 4 lớp: A an toàn deterministic, B router embedding, C SLM sinh command, D dữ liệu/truy hồi).

## 1. Phân loại config — cái gì XOÁ, cái gì GIỮ

### XOÁ HẲN (khoá + accessor + schema + validator + code dùng nó + `_clone_language` trong `tests/agent/test_nlu_memory.py`)

Phân loại ý định bằng cụm từ:
- `nlu.routing.greeting_terms`, `thanks_terms`, `courtesy_particles`, `bare_topic_terms`, `language_switch_terms`, `switch_command_patterns`, `korean_target_first_pattern`, `sequence_pattern`
- `nlu.memory_vocabulary.*` toàn bộ: `action_followup_terms`, `followup_markers`, `ambiguous_reference_markers`, `pending_question_start_patterns`, `facet_aliases`, `facet_search`, `facet_fact_types`, `subject_aliases`, `focus_aliases`
- `nlu.discourse_terms.*`
- `nlu.slots.room_reference_terms`
- `planning.constraints.activity_priority_cues`, `planning.constraints.preferred_window`, `planning.constraints.budget` (nếu chỉ dùng để đoán ý; nếu là đơn vị/ngữ pháp số thì giữ và ghi lý do)
- `nlu.normalization.noise_terms`, `phrase_terms` (nếu đo robustness không tụt quá 1 điểm/ngôn ngữ; ngược lại giữ và ghi lý do)

Kiến thức khách sạn nằm sai chỗ (phải đến từ `datasets/`):
- `planning.categories.*`, `rag.document_domains.markers`, `voice.service_names` (lấy tên dịch vụ từ registry/catalog `names_by_locale`)

Vá truy hồi:
- `rag.query_rewrites.*`, `rag.query_fillers.*`, `rag.concrete_facets`, `rag.explicit_topic_patterns.*`, `rag.explain_patterns.*`

### GIỮ (ngữ pháp ngôn ngữ, an toàn, văn bản hiển thị, chính sách — KHÔNG đụng)

- An toàn: `nlu.intent.emergency_event_patterns`, `emergency_text`, `emergency_contacts`, `completion_claims`, `negation_patterns`; `nlu.authority.*` (cổng an toàn ghi — để sau, KHÔNG xoá đợt này); `security.*`.
- Cổng xác nhận lớp A: `nlu.routing.confirmation_terms`, `affirm_terms`, `deny_terms`.
- Văn bản hiển thị/i18n: `nlu.routing.static_text`, `nlu.slots.slot_labels`, `clarification_text`, `ready_text`, `ui.*`, `tools.*` (mô tả tool).
- Ngữ pháp số/giờ/slot (kiểm slot là chữ của khách): `nlu.numerals`, `nlu.clock`, `nlu.time_expressions`, `nlu.slots.*` còn lại, `planning.constraints.*_patterns`, `nlu.qualifier_patterns`.
- Registry & chính sách: `services`, `request_kinds`, `request_kind_routes`, `public_catalog_kinds`, `preferences.fields` (enum), `memory_policy`, `nlu.service_selector`, `planning.max_*`.
- Kỹ thuật truy hồi/giọng nói không phải kiến thức: `rag.token_stopwords`, `rag.tokenization`, `rag.compound_terms`, `rag.cross_language_fallback_order`, `rag.opening_hours`, `rag.grounding_budgets`, `voice.*` còn lại (rendering số/giờ, dấu câu, filler STT).

Khoá nào không có trong hai danh sách: quyết định theo nguyên tắc "đoán ý định hoặc chứa kiến thức khách sạn → xoá; ngữ pháp/an toàn/hiển thị → giữ", ghi lý do vào báo cáo.

## 2. CẤM

- Thêm lại bất kỳ cụm từ/regex nào vào config dưới tên khác hoặc vào code (string literal, list trong `src/`), kể cả "tạm thời".
- Chép câu `datasets/evaluation/**`, `docs/BACKEND-TEST-PLAN.md`, câu probe/báo cáo vào training. Mỗi ví dụ train mới: `frame_id` riêng, CANDIDATE, kiểm trùng (exact + Jaccard ≥ 0.85) với training + evaluation + test plan.
- Tra cứu nguyên văn câu, đếm số từ để đoán ý định, `if language == ...`, so literal mã dịch vụ/entity trong `src/`.
- Nới/xoá/skip test an toàn hoặc sửa test để chấp nhận hành vi sai. Test chỉ kiểm cụm từ đã xoá thì được xoá cùng (ghi danh sách).
- Đụng: `emergency_gate.py`, `emergency_event_patterns`, `authority.py`/`nlu.authority.*`, cổng xác nhận ghi, `domain/requests/`.
- `git commit/push/checkout/stash/reset`, đổi nhánh. Xoá `datasets/`, `data/concierge*.sqlite3` gốc, `models/`.
- Đặt tên theo phiên bản (final, v2, after...). Báo cáo ghi đè một đường dẫn ổn định.

## 3. Vận hành

- Windows; sửa file có tiếng Việt/Trung/Hàn bằng Python `encoding="utf-8"`. Ổ C: rất ít chỗ: không ghi file lớn ra `%TEMP%`.
- Test: KHÔNG source `.env.example`: `python -m pytest -q -W error::ResourceWarning -p no:cacheprovider`. Server/eval: CÓ source `.env.example`; DB copy `data/concierge-test*.sqlite3`, port 8001, `CONCIERGE_STAFF_TOKEN=demo-staff-token-2026`; chờ "startup complete" + 60 s; `/api/session` 429 → chờ 60 s. Tắt server sau mỗi lần dùng.
- Sau sửa config: `python tools/config/repin_configs.py`; sau sửa dữ liệu train: `tools/manifest/refresh_dataset.py` + `datasets/schemas/validate_contracts.py`, rồi `tools/nlu/calibrate_service_fallback.py`, `tools/nlu/calibrate_emergency_gate.py`, repin.
- Đo nhanh khi thử: `run_backend_test_plan.py --group <nhóm liên quan>`; bài thi đầy đủ 184 ca chỉ khi chốt một bước. Ca SLM dao động: chạy 3 lần lấy đa số.
- Gặp vấn đề chưa rõ cách giải: nghiên cứu dự án/paper tương tự trước (Rasa CALM, LangGraph, dialogue state tracking, coreference/ellipsis, query rewriting cho RAG...), ghi `reports/reset/research.md` (vấn đề → nguồn → cách họ làm → áp dụng). Mạng chỉ để đọc, và để `ollama pull` đúng các model ở R2.
- Sau MỖI bước cập nhật `reports/reset/progress.md` (bước, trạng thái, số đo, thời gian).

## 4. Các bước

### R0 — Sao lưu + dọn rác (≤ 45 phút)

1. Tạo `D:/AgentAI/backups/stay-agent-<YYYYMMDD-HHMM>.tar.gz` (toàn repo trừ `models/`, `frontend/node_modules/`, `.git/`, `.pytest-tmp/`, `.cache/`); kiểm mở được. Không tạo được → DỪNG.
2. Xoá rác: `scratch/`, `scratch_*.txt` ở gốc, `.cache/overnight/`, `.pytest-tmp/`, `__pycache__/`, `data/concierge-test*`, `data/primary-backup-*/`, `data/vectors-*-test/`, file trung gian lớn trong `reports/overnight/` (giữ REPORT/progress/steps/summary/research/bảng *.md), báo cáo có hậu tố phiên bản. Grep tham chiếu trước khi xoá.
3. Đo baseline: gate đầy đủ + bài thi đầy đủ → `reports/reset/baseline.json` (điểm theo nhóm, theo ngôn ngữ, budget, số test).

### R1 — Phân tích lỗi (không sửa code) (≤ 60 phút)

Với mỗi ca trượt trong bài thi baseline: câu khách, `understanding_commands`, route, trả lời, kỳ vọng; gán MỘT nguyên nhân: (a) model hiểu sai/thiếu command, (b) code xử lý sai, (c) thiếu dữ liệu khách sạn (từ chối đúng), (d) bài chấm quá chặt (chỉ ghi, không sửa), (e) khác. Xuất `reports/reset/error-analysis.md` (bảng ca + bảng đếm nguyên nhân × nhóm + top 5 cụm lỗi).

### R2 — Đổi model (≤ 3 giờ)

1. Kiểm ổ D: ≥ 10 GB trống; `ollama pull qwen3:4b` và `qwen3:8b`.
2. Qwen3 phải chạy chế độ KHÔNG suy nghĩ: thêm tuỳ chọn chung (vd `slm.think: false` trong runtime profile src + schema + `core/settings.py` + test), truyền vào mọi request Ollama (`runtime/local_http.py`, `commands.py`, planner). Không `if` theo tên model.
3. Thử bằng biến môi trường (không sửa config lúc thử) cho [qwen2.5:3b, qwen3:4b, qwen3:8b]: `evaluate_command_understanding.py --model <m>`; bài thi đầy đủ; độ trễ p50/p95 GPU và CPU (`num_gpu=0`, 20 lượt tiếng Việt). Ghi digest nếu server bắt khớp.
4. `reports/reset/model-comparison.md`: model × nhóm ca, tổng /184, theo ngôn ngữ, command accuracy, p50/p95 GPU/CPU, RAM; ca đổi kết quả. 
5. CHỌN model: ưu tiên qwen3:4b nếu tổng ≥ baseline + 5, không tụt NEG/SAFE/EMG/WR, CPU p50 ≤ 5 s/lượt; nếu qwen3:4b không đạt mà qwen3:8b đạt (kể cả độ trễ) thì chọn 8b; không cái nào đạt → giữ qwen2.5:3b. ÁP DỤNG model đã chọn vào `config/runtime-profiles/src/base.json` (+ digest), `.env.example`; `python tools/config/build_runtime_profiles.py`; repin; bài thi đầy đủ xác nhận → đây là **mốc điểm M** cho R3.

### R3 — Xoá sạch hardcode (≤ 4 giờ)

1. Xoá TOÀN BỘ danh sách "XOÁ HẲN" ở mục 1 trong một lần: khoá config, accessor (`agent/understanding/domain_nlu.py`, `core/domain_profile/*`), schema, validator, mọi code đọc chúng, test chỉ kiểm cụm từ đó. Kiến thức (`planning.categories`, `document_domains.markers`, `voice.service_names`) chuyển sang đọc từ dataset/registry/`releases/domain-vocab.json` (bổ sung alias vào `datasets/knowledge/canonical/` nếu thiếu → `tools/knowledge/build_domain_vocab.py` → repin).
2. Chỗ code từng dựa vào cụm từ → thay bằng: command của SLM (`ChitChat`, `SwitchLanguage`, `AskInfo`/`Navigate` mang tham chiếu, `Clarify`), router lớp B (embedding), ngữ cảnh hội thoại đưa vào prompt SLM, `reference_resolver` dựa trên anchor đã lưu. Không thay bằng heuristic độ dài câu.
3. Chạy gate + bài thi đầy đủ → ghi điểm "ngay sau xoá" (được phép tụt tạm).
4. Hạ `tests/agent_domain_keyword_budget.json` đúng số mới (mục tiêu ≤ 650).

### R4 — Phục hồi điểm (đến khi ≥ mốc M, tối đa 5 giờ)

Lặp theo `error-analysis` mới (chạy lại phân tích lỗi trên kết quả R3): mỗi vòng chọn cụm lỗi lớn nhất →
- (a) model hiểu sai: thêm ví dụ train khác cách nói (vi trước; nhiều lượt nếu là hỏi tiếp — thêm trường ngữ cảnh tuỳ chọn cho ví dụ nếu cần, có schema + test), sửa mô tả command/dịch vụ trong prompt; hiệu chỉnh lại.
- (b) code sai: sửa code + test.
- (c)/(d): ghi nhận, không sửa.
Đo nhanh nhóm liên quan; khi đạt, chạy bài thi đầy đủ. **Điều kiện xong R4:** tổng ≥ M, NEG/SAFE/EMG/WR/SEC/STF không tụt so với baseline R0, full pytest xanh, budget ≤ mục tiêu. Hết giờ mà chưa đạt: GIỮ kết quả xoá (không thêm lại keyword), ghi rõ điểm thiếu và cụm lỗi còn lại.

### R5 — Dọn và tài liệu (≤ 60 phút)

`python -m vulture src/concierge_kiosk tools --min-confidence 60` → xoá code chết (bỏ qua route FastAPI/Pydantic); `tests/hardcode_allowlist.txt` bỏ dòng thừa; cập nhật `CLAUDE.md` (Turn flow, Configuration: không còn nhắc khoá/hàm đã xoá), `docs/COMPLETION-PLAN.md` (HC-* đã xong), `docs/nlu-robustness.md`; xoá `docs/OVERNIGHT-TASKS.md` sau khi chuyển nội dung còn giá trị. `tools/validate/audit_data.py`, `tools/validate/agent_domain.py`.

### R6 — Báo cáo

Gate đầy đủ + bài thi đầy đủ + `tools/nlu/perturb.py && tools/nlu/robustness_report.py` + 3 suite retrieval + `probe_real_behavior.py --base http://127.0.0.1:8001 --suite emergency`. `reports/reset/REPORT.md` (tiếng Việt): bảng bước → trạng thái; điểm 140 → baseline R0 → M → sau xoá → cuối, theo nhóm và ngôn ngữ; model chọn + lý do + độ trễ CPU; số chuỗi config trước/sau và danh sách khoá đã xoá; số test; mọi lần hoàn nguyên; việc còn dở theo ưu tiên; đường dẫn backup; `git diff --stat | tail -1`; `ollama list`; dung lượng trống C:/D:. Tắt server. Dừng.
