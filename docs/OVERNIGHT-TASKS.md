# Phiên làm việc dài không giám sát: bỏ hardcode còn lại, kiểm thật, báo cáo

Đối tượng đọc: agent lập trình (Codex). Người dùng đi ngủ; sáng mai đọc `reports/overnight/REPORT.md`.
Làm tuần tự theo mục 4. Không hỏi lại người dùng: khi mơ hồ, chọn phương án an toàn hơn, ghi lý do vào `reports/overnight/progress.md` rồi đi tiếp.

## 0. Đọc trước khi làm gì

1. `CLAUDE.md`, `AGENT.md` (đặc biệt mục "Không học đề" và luật đặt tên), `docs/COMPLETION-PLAN.md` mục 6 (kiến trúc đích 4 lớp, bảng HC-*).
2. Kiến trúc hiện tại của một lượt: lớp A khẩn cấp (regex + `emergency_gate.py` logistic) → lớp B `fast_router.py` (embedding, không SLM) → lớp C SLM sinh `Command[]` (`commands.py`) → server kiểm lại → tool/policy. `ServiceSelector` cung cấp few-shot theo kNN từ `datasets/training/agent/`.
3. Đã xong và KHÔNG làm lại: huỷ/đổi/trạng thái bằng command (T3), câu hỏi thông tin/chỉ đường/chỗ trống bằng command (T4), khẩn cấp hai tầng (logistic, hiệu chỉnh nhóm giữ ngoài), nhóm tình huống đã chuẩn hoá (`service_selector.normalized_situation_group`), ngưỡng router/fallback đã hiệu chỉnh.

## 1. Bối cảnh ngắn

Dự án: kiosk concierge khách sạn, 4 ngôn ngữ (vi/en/zh/ko), **tiếng Việt là ưu tiên**; chạy local CPU. Mục tiêu của đợt này: bỏ nốt các danh sách cụm từ trong `config/agent-domain.json` dùng để PHÂN LOẠI Ý ĐỊNH hoặc VÁ TRUY HỒI, thay bằng command/embedding/dữ liệu, mà điểm đánh giá thật không tụt. Baseline trung thực: `reports/phase4/baseline/api.jsonl` = **140/184** lượt đạt (bộ `datasets/evaluation/backend_plan/turn_cases.jsonl`, chạy bằng `tools/evaluation/run_backend_test_plan.py`). Budget từ khoá hiện tại: `tests/agent_domain_keyword_budget.json` (1132 chuỗi / 29 trùng tên tài sản); chỉ được GIẢM.

Còn lại trong config (số chuỗi, ước tính): `nlu.memory_vocabulary.followup_markers` 81, `facet_aliases` 68, `focus_aliases` 51, `subject_aliases` 50; `rag.document_domains.markers` 69; `rag.query_fillers` ~70; `rag.query_rewrites` ~64; `planning.constraints.constraint_terms` 39, `planning.categories.*` ~64, `planning.intent_cues`; `preferences.fields.*.recognition` ~29; `nlu.routing.bare_topic_terms`. Nhóm `nlu.authority.*` (94 chuỗi, an toàn ghi): **KHÔNG ĐỤNG VÀO**.

## 2. CẤM TUYỆT ĐỐI

- Thêm cụm từ/regex vào `config/agent-domain.json` để một câu cụ thể được hiểu đúng. Cấm kể cả "chỉ một cụm".
- Chép câu từ `datasets/evaluation/**`, `docs/BACKEND-TEST-PLAN.md`, hay từ bất kỳ báo cáo/probe nào vào `datasets/training/`. Câu hiểu sai → thêm 2–3 ví dụ KHÁC cách nói vào `datasets/training/agent/assistant_candidates.jsonl` (CANDIDATE).
- Tra cứu nguyên văn câu, đếm số từ để đoán ý định, `if language == ...`, so literal mã dịch vụ/entity trong `src/`.
- Nới/xoá/skip test; sửa test để "chấp nhận" hành vi sai; sửa `tests/agent/test_no_case_specific_rules.py`; TĂNG `tests/agent_domain_keyword_budget.json`.
- Đụng vào: `nlu.authority.*`, `agent/understanding/authority.py`, `emergency_gate.py`, `emergency_event_patterns`, các cổng xác nhận ghi, `domain/requests/`. Đây là lớp an toàn.
- Dữ liệu train: mỗi câu mới có `frame_id` RIÊNG (một tình huống = một nhóm); không nhân bản bằng tiền tố ("Làm ơn,", "Nhanh lên,").
- `git commit`, `git push`, đổi nhánh, `git stash`, `git checkout`, `git reset`. Không xoá `datasets/`, `data/`, `models/`.
- Tải model mới (Ollama chỉ có `qwen2.5:3b` và `bge-m3`), cài gói mới. Mạng chỉ được dùng để ĐỌC tài liệu nghiên cứu (mục 6), không tải/cài gì.
- Đặt tên file/hàm/báo cáo theo phiên bản/giai đoạn (final, v2, after, rerun, t2...). Báo cáo ghi đè đúng một đường dẫn ổn định; trước/sau để trong cùng file.
- Để server chạy sau khi dùng xong; chạy hai pytest cùng lúc (dùng chung `.pytest-tmp`).

## 3. Quy tắc vận hành (máy yếu, ổ C: gần đầy)

- Windows. Sửa file có tiếng Việt/Trung/Hàn bằng Python `encoding="utf-8"`, không bằng lệnh shell (shell hiển thị sai font nhưng file vẫn UTF-8 hợp lệ — đừng "sửa" mojibake).
- Ổ C: chỉ còn ~2 GB. Không ghi file lớn vào `%TEMP%`; pytest đã cấu hình `--basetemp=.pytest-tmp` (trong repo). Không tạo bản sao DB ngoài `data/concierge-test*.sqlite3`.
- Môi trường chạy test: KHÔNG `source .env.example` (`python -m pytest -q -W error::ResourceWarning -p no:cacheprovider`). Môi trường chạy server/eval: CÓ source `.env.example`.
- Server kiểm thật: copy `data/concierge.sqlite3` → `data/concierge-test.sqlite3` và `data/concierge-graph.sqlite3` → `data/concierge-test-graph.sqlite3`, xoá `*-wal`/`*-shm` của bản test; `set -a; . ./.env.example; set +a`; `CONCIERGE_DB_PATH=./data/concierge-test.sqlite3 CONCIERGE_BIND_PORT=8001 CONCIERGE_STAFF_TOKEN=demo-staff-token-2026 python -m concierge_kiosk`; chờ log "startup complete" rồi thêm 60 giây (indexes build nền). Tạo phiên `/api/session` bị giới hạn tần suất (HTTP 429 "Please retry later"): chờ 60 giây rồi thử lại; dùng lại một phiên cho nhiều câu độc lập. Tắt server sau mỗi lần dùng (`taskkill`), luôn kiểm cổng 8001 trống.
- Sau MỌI sửa dữ liệu train: `python tools/manifest/refresh_dataset.py` và `python datasets/schemas/validate_contracts.py` (phải PASS). Sau MỌI sửa `config/*.json` ghim hash: `python tools/config/repin_configs.py`, rồi `python tools/validate/agent_domain.py` với `PYTHONPATH=src`. Hiệu chỉnh lại khi dữ liệu train đổi: `python tools/nlu/calibrate_service_fallback.py` rồi `python tools/nlu/calibrate_emergency_gate.py` (mỗi lệnh tự ghi ngưỡng vào config; sau đó repin).
- Chuỗi kiểm sau mỗi bước (gate): `python -m compileall -q src tools`; `python tools/config/repin_configs.py --check`; `python datasets/schemas/validate_contracts.py`; `python -m pytest -q -W error::ResourceWarning -p no:cacheprovider` (FULL, báo số); `tests/agent/test_no_case_specific_rules.py`. Test xanh là điều kiện CẦN; điểm bài thi thật (mục 5) mới là điều kiện ĐỦ.
- Ngôn ngữ: kiểm sâu bằng tiếng Việt (bắt buộc), en/zh/ko mỗi ngôn ngữ chỉ 3–5 câu "không tụt".

## 4. Các bước (tuần tự; mỗi bước có checkpoint)

### Checkpoint (áp dụng cho mọi bước từ S1)

Trước khi sửa: sao chép các file sắp sửa vào `.cache/overnight-backup/<tên-bước>/` (giữ cấu trúc thư mục). Sau khi sửa: chạy gate + đo (mục 5). **Giữ** thay đổi chỉ khi (a) mọi gate xanh và (b) số đo không tụt vượt ngưỡng mục 5. Ngược lại: phục hồi các file từ bản sao lưu, ghi nguyên nhân vào `progress.md`, chuyển bước kế. Mỗi bước tối đa 3 vòng sửa; hết 3 vòng chưa đạt → hoàn nguyên và ghi "NOT DONE".

### S0 — Hiện trạng và baseline hiện tại (≤ 40 phút)

1. `git status --short | wc -l` (ghi nhận, không động vào). Chạy gate đầy đủ; ghi số test.
2. Khởi động server kiểm thật (mục 3); chạy `python tools/evaluation/run_backend_test_plan.py --base http://127.0.0.1:8001 --db data/concierge-test.sqlite3 --output reports/backend-plan/api.jsonl`. Tính số lượt đạt/tổng, FAIL theo nhóm (CHAT, DLG, REF, PLAN, PREF, NEG, CMP, SVC...). Ghi vào `reports/overnight/summary.json`: `{"baseline": "140/184 (reports/phase4/baseline/api.jsonl)", "current": {...}, "by_group": {...}}`. Tắt server.
3. Tạo `reports/overnight/progress.md` (ghi đè sau mỗi bước: bước, trạng thái KEPT/REVERTED/NOT DONE, số đo, thời gian).

### S1 — Câu đọc ghép (hỏi giờ + chỉ đường; "A và B")

Vấn đề đã thấy: SLM từng tách "giờ mở cửa + chỉ đường" thành 2 `AskInfo` (mất `Navigate`), và "A và B" thành 1 `AskInfo` cả câu → không tìm thấy nguồn. Đã thêm 25 ví dụ `CAND-READMULTI-*` vào `assistant_candidates.jsonl` (gold_status CANDIDATE).
1. Kiểm trên server thật ≥ 14 câu đọc ghép tiếng Việt MỚI (7 dạng "giờ/giá X + đường tới X", 7 dạng "A và B"), + 3 câu/ngôn ngữ khác. Kiểm trùng tự động (exact + Jaccard ≥ 0.85) với `datasets/training/agent/*.jsonl`, `datasets/evaluation/**`, `docs/BACKEND-TEST-PLAN.md`.
2. In bảng: câu → `understanding_commands` → `tool_route` → số `sources` → có `map_guidance` không → trích câu trả lời. Phân biệt "không có dữ liệu thật trong khách sạn" (từ chối đúng) với "đáng lẽ trả lời được".
3. Nếu lỗi do lựa chọn few-shot/cách sinh command: thêm ví dụ khác cách nói (dữ liệu), không thêm luật. Nếu lỗi do ghép kết quả nhiều lần đọc (`presentation/synthesizer.py`, `agent/runtime/result.py`): sửa code và thêm test.
4. Hiệu chỉnh lại nếu dữ liệu đổi (mục 3).

### S2 — Hỏi tiếp bằng ngữ cảnh (HC-C3)

Bỏ `nlu.memory_vocabulary.followup_markers`, `facet_aliases`, `ambiguous_reference_markers`, `action_followup_terms` (cụm nhận diện "còn nó thì sao", "giờ mở cửa?", "đặt luôn đi").
- Thay bằng: đưa trạng thái hội thoại gần nhất (chủ đề/anchor đã giải quyết, câu hỏi chờ trả lời) vào prompt SLM (`commands.py`), để command `AskInfo`/`StartGoal`/`Navigate`/`Clarify` tự mang tham chiếu; `agent/memory/reference_resolver.py` + `conversation.py` giải quyết anchor. Facet (giờ/giá/vị trí) học từ ví dụ nhiều lượt.
- Dữ liệu: ≥ 30 ví dụ hỏi tiếp tiếng Việt MỚI (nhiều lượt, ghi ngữ cảnh trong ví dụ nếu schema cho phép; nếu chưa có trường ngữ cảnh, thêm trường tuỳ chọn `context` vào schema + `CommandExample` + prompt few-shot, có test).
- Đo: nhóm REF-* và DLG-* của bộ đánh giá + `tools/evaluation/` multi-turn probe nếu có. p50 lượt hỏi tiếp không tăng quá 300 ms.
- Xoá accessor/schema/validator/test `_clone_language` của các khoá đã bỏ. Hạ budget.

### S3 — Kế hoạch và sở thích (HC-C4, HC-C5)

Bỏ `planning.intent_cues`, `planning.constraints.constraint_terms` (39), `preferences.fields.*.recognition` (~29). Thay bằng command `Plan` (ràng buộc thời gian/sở thích là slot) và `SetPreference` (enum đóng theo `preferences.fields`; giá trị phải là chữ của khách hoặc giá trị enum hợp lệ do server kiểm).
- `agent/tools/planning.py`, `agent/memory/preferences.py`, `commands.py`. Sở thích không lan sang phiên mới.
- Dữ liệu: ≥ 20 câu tiếng Việt mới cho mỗi command `Plan` và `SetPreference` (đa dạng loại sở thích: ăn chay, dị ứng, trẻ em, xe lăn/di chuyển, không khí lãng mạn...). Câu phủ định ("tôi KHÔNG ăn chay") phải ra đúng giá trị/không lưu nhầm.
- Đo: PLAN-*, PREF-* của bộ đánh giá; test đơn vị: "phở không cần hành" khi đang có đề xuất không bị hiểu thành huỷ hay sở thích.

### S4 — Kiến thức khách sạn ra khỏi config (HC-D1)

Chuyển `nlu.memory_vocabulary.subject_aliases`, `focus_aliases`, `nlu.routing.bare_topic_terms`, `planning.categories.*`, `rag.document_domains.markers` (≈ 270 chuỗi) về dataset: alias thuộc `datasets/knowledge/canonical/` (aliases/`names_by_locale`) → `python tools/knowledge/build_domain_vocab.py` → `python tools/config/repin_configs.py` → accessor đọc `releases/domain-vocab.json`. Chỉ chuyển cái thực sự là tên/alias thực thể; thuật ngữ ngôn ngữ chung thì bỏ nếu retrieval không tụt.
- Làm TỪNG khoá một: đo retrieval trước/sau mỗi khoá. Đo: `python tools/evaluation/run_retrieval_eval.py --suite grounded|fact_holdout|compositional` (có rerank, Ollama chạy; chế độ lexical nếu quá chậm — ghi rõ) so với số hiện tại; ngưỡng: không tụt quá 0.5 điểm ở bất kỳ ngôn ngữ nào, 0 ca giá/giờ không evidence.
- Nếu cần rebuild kiến thức: làm theo mục "Knowledge rebuild" trong `CLAUDE.md` (dừng server trước; `--require-learned`).

### S5 — Vá truy hồi (HC-D2)

`rag.query_fillers` (~70) và `rag.query_rewrites` (~64): bỏ **từng luật**, chạy retrieval eval sau mỗi luật hoặc theo cụm nhỏ; luật nào bỏ mà tụt → thay bằng alias dữ liệu (S4) hoặc giữ lại và ghi lý do cụ thể (câu nào, số đo tụt bao nhiêu). Lưu bảng từng luật vào `reports/overnight/retrieval-rules.json` (luật → trước → sau → quyết định). Mục tiêu ≥ 60% luật được bỏ mà không tụt; không ép bỏ luật đang cần.

### S6 — Dọn code và tài liệu (T9)

1. `python -m vulture src/concierge_kiosk tools --min-confidence 60`: bỏ qua route FastAPI/Pydantic; xoá hàm/lớp chết thật sau các bước trên (xoá cả test chỉ kiểm hàm đó). Xoá accessor/schema/validator của mọi khoá config đã bỏ.
2. Bỏ file thừa còn sót (không tham chiếu): kiểm bằng grep trước khi xoá. Không xoá `datasets/`, `data/`, `models/`, `reports/phase4/`.
3. Cập nhật `CLAUDE.md` (mục Turn flow/Configuration cho đúng với code), `docs/nlu-robustness.md`, `docs/COMPLETION-PLAN.md` (đánh dấu HC-* đã xong/bỏ). Chạy `tools/validate/audit_data.py`.
4. Nếu đụng `frontend/src`: `cd frontend && npm run build`, `node --check` các file `web/*.js` theo CI.

### S7 — Đo cuối và báo cáo (T10)

1. Gate đầy đủ (mục 3). Server kiểm thật; chạy lại `run_backend_test_plan.py` (ghi đè `reports/backend-plan/api.jsonl`); cập nhật `summary.json` (trường `current` → giữ vào `previous`, `final` mới) cùng bảng theo nhóm.
2. `python tools/nlu/perturb.py && python tools/nlu/robustness_report.py` (đọc báo cáo mới và `previous` cùng file; không tụt quá 1 điểm ở ngôn ngữ nào); `python tools/evaluation/evaluate_command_understanding.py --output reports/tool-eval/command-understanding.json` và bản `--fallback-only`; ba suite retrieval; `python tools/evaluation/probe_real_behavior.py --base http://127.0.0.1:8001 --suite emergency` (30 câu khẩn cấp vi MỚI + 30 nhiễu vi MỚI, kiểm trùng).
3. Viết `reports/overnight/REPORT.md` (tiếng Việt): bảng bước → KEPT/REVERTED/NOT DONE; điểm bài thi 140/184 → hiện tại/cuối theo nhóm và theo ngôn ngữ; số chuỗi config và budget trước/sau; số test; mọi lần hoàn nguyên và nguyên nhân; danh sách hạng mục còn dở xếp theo ưu tiên; mọi lệnh đã chạy để người đọc tái lập.

## 5. Ngưỡng giữ thay đổi (mỗi bước)

- Full pytest xanh (không skip thêm), `test_no_case_specific_rules` xanh, budget **giảm hoặc bằng**, `validate_contracts` PASS, `repin_configs --check` PASS.
- Bài thi `run_backend_test_plan.py`: số lượt đạt **không thấp hơn** số của S0 (trừ dao động ≤ 1 lượt trên ca có SLM không tất định — chạy lại ca đó 3 lần, lấy đa số). Không ca NEG nào mới FAIL; không ca khẩn cấp/ghi (WR-*, SAFE-*) nào mới FAIL.
- Retrieval: không tụt > 0.5 điểm/ngôn ngữ; command accuracy/selector recall: không tụt > 1 điểm.
- Mọi số đo kèm bảng ≥ 10 câu tiếng Việt MỚI (kiểm trùng tự động) chạy với model thật; bảng phải có cả câu SAI.

## 6. Khi bị kẹt

- **Gặp vấn đề chưa rõ cách giải (ví dụ: hỏi tiếp mất ngữ cảnh, câu ghép bị gộp, bỏ một luật truy hồi thì tụt điểm, sở thích phủ định bị lưu nhầm, router/ngưỡng không đạt): TRƯỚC KHI tự nghĩ cách vá, nghiên cứu cách các dự án và bài báo tương tự đã giải.** Tìm trên web: dự án mã nguồn mở (Rasa CALM, NVIDIA NeMo Guardrails, LangGraph, DSPy, Haystack, LlamaIndex, SetFit...), bài báo (arXiv, ACL Anthology: dialogue state tracking, coreference/ellipsis trong hội thoại, intent detection ít dữ liệu, query rewriting cho RAG, conformal/threshold calibration...), blog kỹ thuật của các nhóm làm sản phẩm thật. Đọc ít nhất 2–3 nguồn, chọn cách phù hợp với ràng buộc của dự án (chạy local CPU, model nhỏ, không hardcode, dữ liệu tiếng Việt ít). Ghi vào `reports/overnight/research.md`: vấn đề → nguồn (link) → cách họ làm → áp dụng thế nào ở đây → vì sao chọn/không chọn. Phương án nào trái với mục 2 (CẤM) thì loại, dù nguồn khuyên dùng.

- Server không lên / Ollama không trả lời: kiểm `curl http://127.0.0.1:11434/api/tags`; thử lại 3 lần; vẫn lỗi → dừng các bước cần model, làm bước chỉ cần code/dữ liệu, ghi vào `progress.md`.
- Test lỗi không liên quan thay đổi của bạn: chứng minh bằng cách chạy riêng test đó trên bản sao lưu; ghi lại, không "sửa" bằng cách nới test.
- Hết thời gian: dừng ở ranh giới bước (không để dở nửa chừng): hoàn nguyên bước đang làm, viết `REPORT.md` với phần đã xong.

## 7. Kết thúc

Tắt mọi server; xoá `.cache/overnight-backup/` KHÔNG (giữ để người dùng đối chiếu); `git status --short` ghi vào `REPORT.md`; không commit. Dừng.
