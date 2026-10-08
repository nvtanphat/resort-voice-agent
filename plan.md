# Kế hoạch: xoá thẳng hardcode + code lỏ, rồi vá lại bằng model/command/dữ liệu

## Context

Hai ngày gỡ keyword từng cụm: bài thi 140 → 146/184, budget keyword 1473 → 1132, mỗi bước tốn hàng giờ đo, Codex qua đêm chỉ giữ được 1 bước. Nguyên nhân: (1) model qwen2.5:3b yếu ở hội thoại nhiều lượt/câu ghép (BFCL v3: 58 vs Qwen3-4B 76), nên gỡ keyword là tụt; (2) gỡ từng cụm nhỏ thì phần còn lại vẫn kéo hệ thống về đường cũ. Quyết định của chủ dự án: **đổi model trước, rồi xoá thẳng toàn bộ hardcode ý định/kiến thức/vá truy hồi (kể cả `nlu.authority.*`), vá lại bằng thiết kế đúng**; Claude làm trực tiếp, commit từng mốc lên nhánh `phase4-handoff` (đứng tên nvtanphat, không dòng Co-Authored-By).

Mốc hiện tại: nhánh `phase4-handoff` @ `8f11fe1` (đã push), backup `D:\AgentAI\backups\stay-agent-20261008-0859-before-hardcode-reset.tar.gz`. Bài thi 146/184 (INF 7/7, CHAT 18/20, KB 11/16, SVC 17/22, NEG 5/7, CMP 5/15, DLG 5/15, REF 4/7, PREF 3/4, PLAN 3/3, SAFE/EMG/WR/STF/SEC/VOC/NOREQ đủ). 630 test.

## Nguyên tắc thiết kế (thay thế cho keyword)

| Việc keyword đang làm | Thay bằng | Kiểu Rasa CALM / nghiên cứu |
|---|---|---|
| Nhận diện hỏi tiếp (`followup_markers`, `ambiguous_reference_markers`) | Trạng thái hội thoại (anchor gần nhất: tiêu đề, entity, facet; câu hỏi đang chờ) đưa vào prompt `model_commands`; câu **không nhắc thực thể nào** (dò bằng tên/alias từ `releases/domain-vocab.json`) + có anchor → thừa kế anchor; ≥2 anchor khả dĩ → `model_reference_choice` (đã có) | Rasa CALM: command generator đọc transcript; dialogue state tracking |
| Facet giờ/giá/vị trí (`facet_aliases`, `facet_search`, `explain_patterns`, `concrete_facets`, `query_rewrites` → token `operating`) | Trường `facet` (enum đóng) trong `AskInfo`/`Navigate` do SLM điền; facet → `fact_type` (bảng ánh xạ cấu trúc, không phải cụm từ) → structured lookup có sẵn trong `rag/retrieval/engine.py` | Slot có schema đóng thay cho regex |
| Chủ đề/tiêu điểm (`subject_aliases`, `focus_aliases`, `explicit_topic_patterns`) | `entity_id`/`category` của anchor + dò thực thể bằng domain vocab (`core/domain_vocab.py`: `entity_terms`, `category_terms`) | Dữ liệu là nguồn sự thật |
| Câu trả lời slot (`pending_question_start_patterns`, `is_pending_answer`) | `SetSlot` qua router lớp B / schema SLM khi server đang hỏi (đã có) | CALM SetSlot |
| Authority (`tentative/explicit/restricted_terms`, `imperative_patterns`) | `explicit_intent` = có `StartGoal` không `conditional`; từ chối = `authority: deny` trong registry; mọi ghi vẫn qua cổng xác nhận khách (`hitl_mode=guest_confirm_all`) | Server kiểm lại command, model không cấp quyền |
| Kế hoạch (`planning.categories.*`, `activity_priority_cues`, `preferred_window`, `budget`) | Command `Plan` mang slot (khung giờ, ngân sách, sở thích, số ngày) là chữ của khách; danh mục lấy từ category domain vocab + topic trong release lịch; mã tiền tệ ISO giữ làm dữ liệu (`planning.currencies`) | |
| Truy hồi (`query_fillers`, `discourse_terms`, `query_rewrites`) | Bỏ; dựa vào FTS/BM25 + dense + rerank sẵn có; stopword chung giữ ở `rag.token_stopwords` | |
| Tên dịch vụ cho giọng nói (`voice.service_names`), `document_domains.markers` | Tên bản địa hoá trong registry/catalog (`names_by_locale`); domain lấy từ front-matter `domain:` (595/595 tài liệu đã có) + allowlist từ category vocab | |

**Giữ** (ngữ pháp/an toàn/hiển thị): `emergency_*`, `negation_patterns`, `completion_claims`, `confirmation/affirm/deny_terms`, `static_text`, `slot_labels`, `clarification/ready_text`, `numerals`, `clock`, `time_expressions`, `nlu.slots.*` pattern số/giờ/phòng/số người, `qualifier_patterns`, `security.*`, `services`, `tools.*`, `ui.*`, `voice.*` rendering, `rag.token_stopwords/tokenization/opening_hours.time_range_pattern/grounding_budgets`.

## Các bước (mỗi bước: gate → đo → commit; tụt nhóm an toàn thì sửa trước khi commit)

### B0 — Đẩy đủ để clone về chạy được (làm ngay, trước B1)
- Commit `plan.md` (kế hoạch này).
- DB kiến thức: tạo bản sạch bằng SQLite backup API (gộp WAL) từ `data/concierge.sqlite3` và `data/concierge-graph.sqlite3`; bỏ ignore đúng 2 file này + `data/vectors/` trong `.gitignore` (giữ ignore `data/concierge-test*`, `data/primary-backup-*`, `data/*-cache/`, `*-wal`, `*-shm`, `edge_test-graph.sqlite3`). Kiểm kích thước (< 100 MB/file).
- `models/` (3,3 GB) KHÔNG đẩy (vượt giới hạn GitHub/LFS miễn phí). Viết `SETUP.md` ở gốc: Python ≥ 3.11 + `pip install -e ".[test,ops,voice]"`; cài Ollama + `ollama pull qwen2.5:3b` và `ollama pull bge-m3`; `python tools/runtime/download_voice_models.py`, `python tools/runtime/download_reranker_model.py` (kiểm argparse trước khi ghi lệnh); `cd frontend && npm ci && npm run build`; `set -a; . ./.env.example; set +a; python -m concierge_kiosk`; mở `http://localhost:8000`; nhắc không `git switch dev`.
- Commit (đứng tên nvtanphat, không Co-Authored-By) + `git push origin phase4-handoff`.

### B1 — Đổi model sang qwen3:4b (non-thinking)
- `ollama pull qwen3:4b` (vào `D:\Ollama\models`).
- Thêm tuỳ chọn chung `slm.think` (runtime profile `config/runtime-profiles/src/base.json` + `config/runtime-profile.schema.json` + `core/settings.py` + test), truyền `"think": false` trong mọi request Ollama: `runtime/local_http.py`, `agent/understanding/commands.py::model_commands`, `agent/runtime/planner.py`, `agent/memory/reference_resolver.py`, `agent/orchestration/grounding.py`.
- Đo qwen2.5:3b vs qwen3:4b: `tools/evaluation/evaluate_command_understanding.py`, bài thi đầy đủ (`tools/evaluation/run_backend_test_plan.py`), p50/p95 CPU (`num_gpu=0`, 20 lượt vi).
- Chọn qwen3:4b nếu tổng ≥ 146, không tụt NEG/SAFE/EMG/WR, CPU p50 ≤ 5 s → đổi `primary_model` + digest, `.env.example`; `tools/config/build_runtime_profiles.py`; repin. Không đạt → giữ 3b, ghi lý do. **Commit.** Mốc điểm = M.

### B2 — Xoá code/config chết (không đổi hành vi)
- `domain_nlu.py`: `THANKS_TERMS`, `BARE_TOPIC_TERMS`, `LANGUAGE_SWITCH_TERMS`, `SWITCH_COMMAND_PATTERNS`, `KOREAN_TARGET_FIRST_PATTERN`, `ROOM_REFERENCE_TERMS`, `ACTION_FOLLOWUP_TERMS`, `GREETING_TERMS`, `CONFIRMATION_TERMS` export thừa, `READ_INTENT`.
- `routing.py:120` khối `SEQUENCE_PATTERN` (tasks luôn rỗng); `intent.py::normalize_intent_with_spans`; `relevance.py::question_type` + `_EXPLAIN_PATTERNS`; `_EXPLAIN` thừa ở `agent/orchestration/grounding.py:29`, `rag/retrieval/policy.py:191`; import `COURTESY_PARTICLES` ở `agent/tools/service_slots.py:20`.
- Config: `thanks_terms`, `switch_command_patterns`, `korean_target_first_pattern`, `sequence_pattern`, `room_reference_terms`, `action_followup_terms`, `greeting_terms`, `courtesy_particles`, `bare_topic_terms`, `language_switch_terms` (bốn cái cuối chỉ còn làm từ vựng sửa dấu: `normalization.py::_profile_terms` lấy từ training + domain vocab, như đã làm cho `info_only`).
- Mỗi khoá xoá kèm: loader (`core/domain_profile/loader.py`), model (`models.py`), validator (`validate/nlu.py`, …), schema `config/agent-domain.schema.json`, `_clone_language` (`tests/agent/test_nlu_memory.py`), test chỉ kiểm cụm từ đó. Gate. **Commit.**

### B3 — Hỏi tiếp + facet bằng trạng thái hội thoại (xoá `nlu.memory_vocabulary.*`, `discourse_terms`, `explicit_topic_patterns`, `explain_patterns`, `concrete_facets`)
- `commands.py`: `AskInfo`/`Navigate` thêm `facet` (enum từ khoá của bảng facet→fact_type, đặt ở `rag.facet_fact_types` — chỉ tên facet và fact_type, không cụm từ); prompt nhận `context` = {anchor title, entity, facet trước, câu hỏi đang chờ} từ engine.
- `agent/memory/heuristics.py`: bỏ `is_followup`, `needs_model_reference_resolution`, `is_pending_answer`, `question_facet`, `_focuses`, `_subjects`; thay bằng `mentioned_entities(query, language)` dựa trên `core/domain_vocab.py`. `conversation.py::_resolve_current/_retrieval_current/reference_query` dùng: thực thể nhắc trong câu → topic mới; không nhắc + có anchor → thừa kế; nhiều anchor → `reference_resolver.model_reference_choice`. Facet lấy từ command, không từ chữ.
- `rag/grounding/relevance.py`: `requested_facets`, `answerable`, `_facet_terms`, `concrete_facets_supported`, `has_explicit_topic` chuyển sang nhận facet/entity từ tham số; `application/conversation/answers.py` (`query_keys`, `structured_selectors`) dùng facet của command → structured lookup.
- `routing.py::is_location_question` → `facet == 'location'` hoặc command `Navigate`.
- `rag/retrieval/context.py`: bỏ lọc `DISCOURSE_TERMS`, dùng `rag.token_stopwords`.
- Dữ liệu: ví dụ nhiều lượt vi (≥ 40) có trường `context` trong `datasets/training/agent/assistant_candidates.jsonl` (schema `datasets/schemas/training/agent_candidate.schema.json` + `CommandExample` + few-shot). Gate; đo nhóm REF/DLG/KB/CMP/NEG. **Commit.**

### B4 — Truy hồi không vá (xoá `rag.query_rewrites`, `query_fillers`)
- Trước khi xoá: chuyển nhận diện giờ mở cửa (`is_opening_hours_query`, `evidence_relevant`, `candidate_relevant`) sang facet `hours` từ command + fact_type; giữ `rag.opening_hours.time_range_pattern` (ngữ pháp giờ).
- `relevance.py::normalized_query/_query_tokens/fts_query` bỏ rewrite/filler; dựa vào FTS BM25 + dense + rerank.
- Đo 3 suite `tools/evaluation/run_retrieval_eval.py` (grounded, fact_holdout, compositional) trước/sau; tụt > 0,5 điểm/ngôn ngữ → bổ sung alias dữ liệu (`datasets/knowledge/canonical/aliases.json` → `tools/knowledge/build_domain_vocab.py`), không thêm luật. **Commit.**

### B5 — Kế hoạch, authority, kiến thức về dữ liệu
- Planning: `agent/tools/planning.py`, `agent/tools/scheduling.py` — danh mục từ category domain vocab; `_TOPICS` lấy từ chính release lịch; khung giờ/ngân sách/ưu tiên từ slot của command `Plan`; `planning.currencies` (mã ISO) thay `budget` cho kiểm chi phí release. Xoá `planning.categories`, `activity_priority_cues`, `preferred_window`, `budget`.
- Authority: `agent/understanding/authority.py` — `evaluate_service_authority(commands, mode, …)`: deny theo registry, `explicit_intent` từ command; xoá `nlu.authority.*`; caller `application/service_actions.py:382,423`.
- `voice.service_names` → tên bản địa hoá trong `services` của registry (`domain/service_registry.py` + schema); `_canonical_service_review` đọc từ đó. `document_domains.markers` → xoá, allowlist domain = category vocab + `default`.
- `nlu.normalization.noise_terms/phrase_terms` → xoá nếu `tools/nlu/robustness_report.py` không tụt > 1 điểm/ngôn ngữ. Gate; đo PLAN/PREF/SVC/WR/SAFE/NEG. **Commit.**

### B6 — Phục hồi điểm theo phân tích lỗi
- Chạy bài thi đầy đủ; mỗi ca trượt gán nguyên nhân (model / code / thiếu dữ liệu khách sạn / bài chấm); sửa cụm lớn nhất trước bằng ví dụ train (khác cách nói, `frame_id` riêng, kiểm trùng) hoặc sửa code; hiệu chỉnh lại `tools/nlu/calibrate_service_fallback.py`, `calibrate_emergency_gate.py`.
- Xong khi: tổng ≥ M, NEG/SAFE/EMG/WR/SEC/STF không tụt, full pytest xanh. **Commit + push.**

### B7 — Dọn và tài liệu
- `python -m vulture src/concierge_kiosk tools --min-confidence 60` → xoá phần chết còn lại; `tests/hardcode_allowlist.txt`; hạ `tests/agent_domain_keyword_budget.json` về số thật (mục tiêu ≤ 400).
- Cập nhật `CLAUDE.md` (Turn flow, Configuration), `docs/COMPLETION-PLAN.md`, `docs/nlu-robustness.md`; xoá `docs/OVERNIGHT-TASKS.md`, `docs/HARDCODE-RESET.md` (đã thay bởi kế hoạch này). **Commit + push.**

## Verification

- Mỗi bước: `python -m compileall -q src tools`; `python tools/config/repin_configs.py --check`; `python datasets/schemas/validate_contracts.py`; `PYTHONPATH=src python tools/validate/agent_domain.py`; `python -m pytest -q -W error::ResourceWarning -p no:cacheprovider` (không source `.env.example`); `tests/agent/test_no_case_specific_rules.py`.
- Server thật (source `.env.example`, DB copy `data/concierge-test.sqlite3`, port 8001): `run_backend_test_plan.py --group <nhóm liên quan>` khi thử; đầy đủ 184 ca khi chốt bước; ca SLM dao động chạy 3 lần.
- B4: 3 suite retrieval; B5: `tools/nlu/perturb.py && tools/nlu/robustness_report.py`; cuối: `tools/evaluation/probe_real_behavior.py --base http://127.0.0.1:8001 --suite emergency` + bộ câu ẩn tiếng Việt mới (khẩn cấp, hỏi tiếp, câu ghép, phủ định "không cần").
- Chỉ số đích: tổng ≥ M (≥ 146), CMP và DLG tăng, NEG/SAFE/EMG/WR 100% như cũ, CPU p50 ≤ 5 s/lượt, budget ≤ 400, 0 cụm từ ý định trong config.
- Không `git switch dev` (sẽ xoá `datasets/` khỏi ổ đĩa).
