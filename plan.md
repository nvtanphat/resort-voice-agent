# Kế hoạch: xoá thẳng hardcode và code thừa, rồi dựng lại bằng model/command/dữ liệu

## Bối cảnh

Gỡ keyword từng cụm nhỏ không hiệu quả: bài thi chỉ nhích 140 → 146/184, budget keyword 1473 → 1132, mỗi bước tốn hàng giờ đo. Hai nguyên nhân:
1. Model SLM qwen2.5:3b yếu ở hội thoại nhiều lượt và câu ghép (BFCL v3: 58, so với Qwen3-4B non-thinking: 76).
2. Gỡ từng phần thì phần keyword còn lại vẫn kéo hệ thống về đường cũ.

Hướng mới: **xoá thẳng toàn bộ hardcode ý định, kiến thức khách sạn nằm trong config và luật vá truy hồi (kể cả `nlu.authority.*`) trong một lần**, chấp nhận hệ thống tạm thời không chạy; sau đó **dựng lại** theo thiết kế đúng (model mạnh hơn, command có schema, trạng thái hội thoại, dữ liệu).

Mốc trước khi xoá: nhánh `phase4-handoff` @ `6f6c697`; bài thi 146/184 (INF 7/7, CHAT 18/20, KB 11/16, SVC 17/22, NEG 5/7, CMP 5/15, DLG 5/15, REF 4/7, PREF 3/4, PLAN 3/3; SAFE, EMG, WR, STF, SEC, VOC, NOREQ đạt hết); 630 test.

## Phần 1 — Xoá (không cần chạy hay đo sau khi xoá)

### 1.1 Config `config/agent-domain.json`: XOÁ

| Nhóm | Khoá |
|---|---|
| Nhận diện ý định bằng cụm từ | `nlu.routing.greeting_terms`, `thanks_terms`, `courtesy_particles`, `bare_topic_terms`, `language_switch_terms`, `switch_command_patterns`, `korean_target_first_pattern`, `sequence_pattern`; toàn bộ `nlu.memory_vocabulary.*`; `nlu.discourse_terms`; `nlu.slots.room_reference_terms`; `nlu.normalization.noise_terms`, `phrase_terms`; toàn bộ `nlu.authority.*` |
| Kiến thức khách sạn đặt sai chỗ | `planning.categories.*`, `planning.constraints.activity_priority_cues`, `preferred_window`, `budget`; `rag.document_domains.markers`; `voice.service_names` |
| Luật vá truy hồi | `rag.query_rewrites`, `rag.query_fillers`, `rag.concrete_facets`, `rag.explicit_topic_patterns`, `rag.explain_patterns` |

Với mỗi khoá xoá luôn: phần đọc trong `core/domain_profile/loader.py` và `models.py`, validator trong `core/domain_profile/validate/`, schema `config/agent-domain.schema.json`, `_clone_language` trong `tests/agent/test_nlu_memory.py`, và các test chỉ kiểm cụm từ đó.

### 1.2 Config: GIỮ (ngữ pháp, an toàn, hiển thị, chính sách)

`emergency_*`, `negation_patterns`, `completion_claims`, `confirmation_terms`/`affirm_terms`/`deny_terms`, `static_text`, `slot_labels`, `clarification_text`/`ready_text`, `numerals`, `clock`, `time_expressions`, các pattern số/giờ/phòng/số người trong `nlu.slots`, `qualifier_patterns`, `security.*`, `services`, `tools.*`, `ui.*`, phần rendering của `voice.*`, `rag.token_stopwords`, `rag.tokenization`, `rag.opening_hours.time_range_pattern`, `rag.grounding_budgets`, `nlu.service_selector`.

### 1.3 Code: XOÁ

- **Hằng số và import không còn dùng:**
  - Trong `agent/understanding/domain_nlu.py`: `THANKS_TERMS`, `BARE_TOPIC_TERMS`, `LANGUAGE_SWITCH_TERMS`, `SWITCH_COMMAND_PATTERNS`, `KOREAN_TARGET_FIRST_PATTERN`, `ROOM_REFERENCE_TERMS`, `ACTION_FOLLOWUP_TERMS`, `GREETING_TERMS`, `CONFIRMATION_TERMS`, `READ_INTENT`.
  - Import `COURTESY_PARTICLES` ở `agent/tools/service_slots.py`.
- **Hàm không còn ai gọi:**
  - Khối `SEQUENCE_PATTERN` trong `routing.py::fast_response` (danh sách tasks luôn rỗng).
  - `intent.py::normalize_intent_with_spans`.
  - `relevance.py::question_type` và `_EXPLAIN_PATTERNS`.
  - `_EXPLAIN` trong `agent/orchestration/grounding.py` và `rag/retrieval/policy.py`.
- **Hàm đoán ý định bằng chữ:**
  - Trong `agent/memory/heuristics.py`: `is_followup`, `needs_model_reference_resolution`, `is_pending_answer`, `question_facet`, `_focuses`, `_subjects`, `_anchor_subject`.
  - Trong `agent/understanding/authority.py`: `explicit_service_intent` và phần so `RESTRICTED`.
  - Trong `routing.py`: `_read_question_type`, `is_location_question`.
  - Trong `rag/grounding/relevance.py`: phần dùng rewrite/filler/facet alias.
  - Trong `rag/retrieval/context.py`: phần lọc `DISCOURSE_TERMS`.
  - Trong `agent/tools/planning.py` và `agent/tools/scheduling.py`: phần đọc `categories`, `activity_priority_cues`, `preferred_window`, `budget`.
  - Trong `rag/documents.py`: phần đọc `markers`.
  - Trong `application/service_actions.py`: phần dùng `voice.service_names`.
- **Code chết còn lại:** chạy `python -m vulture src/concierge_kiosk tools --min-confidence 60` và xoá (bỏ qua route FastAPI và model Pydantic).
- **Hạ budget:** `tests/agent_domain_keyword_budget.json` về đúng số chuỗi còn lại.

## Phần 2 — Dựng lại

| Chức năng cũ (keyword) | Thiết kế mới |
|---|---|
| **Model** | `qwen3:4b` non-thinking. Thêm tuỳ chọn chung `slm.think: false`: khai báo ở `config/runtime-profiles/src/base.json`, `config/runtime-profile.schema.json`, `core/settings.py`, và truyền vào mọi request Ollama (`runtime/local_http.py`, `agent/understanding/commands.py`, `agent/runtime/planner.py`, `agent/memory/reference_resolver.py`, `agent/orchestration/grounding.py`). Đổi `primary_model`, digest và `.env.example`. |
| **Hỏi tiếp** | Đưa trạng thái hội thoại vào prompt `model_commands`: anchor gần nhất (tiêu đề, entity, facet) và câu hỏi đang chờ. Dò thực thể trong câu bằng tên/alias của `releases/domain-vocab.json` (`core/domain_vocab.py`). Nhắc thực thể → chủ đề mới. Không nhắc mà có anchor → thừa kế anchor. Có nhiều anchor khả dĩ → `reference_resolver.model_reference_choice`. |
| **Facet (giờ, giá, vị trí…)** | Trường `facet` (enum đóng) trong `AskInfo` và `Navigate` do SLM điền. Bảng `rag.facet_fact_types` chỉ ánh xạ tên facet sang `fact_type`. `fact_type` dùng cho structured lookup trong `rag/retrieval/engine.py`. Thay cho `facet_aliases`, `query_rewrites` (token `operating`), `concrete_facets`, `explain_patterns`. |
| **Trả lời slot** | `SetSlot` qua router lớp B hoặc schema SLM khi server đang hỏi (đã có sẵn). |
| **Authority** | `explicit_intent` = lượt có `StartGoal` mà không `conditional`. Từ chối dựa vào `authority: deny` trong registry. Mọi lệnh ghi vẫn phải qua cổng xác nhận của khách (`hitl_mode=guest_confirm_all`). |
| **Kế hoạch** | Command `Plan` mang slot (khung giờ, ngân sách, sở thích, số ngày), giá trị là chữ của khách. Danh mục lấy từ category trong domain vocab và topic trong release lịch. Mã tiền tệ ISO đặt ở `planning.currencies`. |
| **Truy hồi** | FTS/BM25 + dense + rerank sẵn có. Thiếu alias thì bổ sung `datasets/knowledge/canonical/aliases.json`, rồi chạy `tools/knowledge/build_domain_vocab.py`. |
| **Tên dịch vụ, domain tài liệu** | Tên bản địa hoá đặt trong `services` của registry (`domain/service_registry.py` + schema). Domain tài liệu lấy từ front-matter `domain:`; allowlist = category vocab + `default`. |
| **Từ vựng sửa dấu/gõ sai** | `normalization.py::_profile_terms` chỉ lấy token từ dữ liệu train và domain vocab. |
| **Dữ liệu** | Ví dụ nhiều lượt tiếng Việt có trường `context`: cập nhật `datasets/training/agent/assistant_candidates.jsonl`, schema `datasets/schemas/training/agent_candidate.schema.json`, `CommandExample` và phần few-shot. Thêm ví dụ `Plan` và `SetPreference`. Mỗi câu một `frame_id` riêng, kiểm trùng với `datasets/evaluation/` và `docs/BACKEND-TEST-PLAN.md`. |
| **Hiệu chỉnh** | `tools/nlu/calibrate_service_fallback.py`, `tools/nlu/calibrate_emergency_gate.py`. |
| **Tài liệu** | Cập nhật `CLAUDE.md` (Turn flow, Configuration), `docs/COMPLETION-PLAN.md`, `docs/nlu-robustness.md`. Xoá `docs/OVERNIGHT-TASKS.md` và `docs/HARDCODE-RESET.md`. |

## Phần 3 — Kiểm tra cuối (sau khi dựng lại xong)

- Gate CI:
  - `python -m compileall -q src tools`
  - `python tools/config/repin_configs.py --check`
  - `python datasets/schemas/validate_contracts.py`
  - `PYTHONPATH=src python tools/validate/agent_domain.py`
  - `python -m pytest -q -W error::ResourceWarning`
- Bài thi `tools/evaluation/run_backend_test_plan.py` trên server thật.
  - Đích: tổng ≥ 146/184, CMP và DLG tăng, NEG/SAFE/EMG/WR không tụt.
- Độ trễ CPU p50 ≤ 5 giây/lượt.
- Config còn 0 cụm từ dùng để đoán ý định.
- Lưu ý: không `git switch dev` (làm vậy sẽ xoá `datasets/` khỏi ổ đĩa).

## Kết quả đợt xoá hardcode — 2026-10-08

Đợt này chỉ xoá. Không chạy test, compileall, benchmark, đo điểm hay server; không dựng lại logic đã xoá. Mốc đối chiếu/khôi phục do người dùng cung cấp: `phase4-handoff @ 6f6c697` (đã push), 146/184, 630 test.

| Bộ đếm giá trị chuỗi config (không đếm tên khoá) | Trước | Sau |
|---|---:|---:|
| Toàn bộ giá trị chuỗi | 2291 | 1267 |
| Keyword theo `KEEP_PREFIXES` của repo | 1132 | 182 |
| Giá trị trùng tên/alias property theo guard của repo | 29 | 3 |

Đã hạ `tests/agent_domain_keyword_budget.json` đúng bộ đếm hiện tại. Các chuỗi còn lại gồm an toàn, grammar, registry, hiển thị và policy; đây không phải kết quả chạy test. `_clone_language` không tồn tại trong phiên bản repo hiện tại. Test giờ mở cửa cần xoá nằm tại `tests/agent/test_multilingual_hours.py`, không phải `tests/rag/`. `validate/semantic.py` không trực tiếp kiểm các khoá đã xoá nên được giữ nguyên.

Các khoá config/schema đã xoá:

- `nlu.normalization.noise_terms`
- `nlu.normalization.phrase_terms`
- `nlu.authority`
- `nlu.routing.greeting_terms`
- `nlu.routing.thanks_terms`
- `nlu.routing.courtesy_particles`
- `nlu.routing.bare_topic_terms`
- `nlu.routing.language_switch_terms`
- `nlu.routing.switch_command_patterns`
- `nlu.routing.korean_target_first_pattern`
- `nlu.routing.sequence_pattern`
- `nlu.slots.room_reference_terms`
- `nlu.memory_vocabulary`
- `nlu.read_intent`
- `nlu.discourse_terms`
- `planning.max_query_chars`
- `planning.minimum_categories`
- `planning.intent_cues`
- `planning.categories`
- `planning.constraints.constraint_terms`
- `planning.constraints.preferred_window`
- `planning.constraints.budget`
- `planning.constraints.activity_priority_cues`
- `rag.query_rewrites`
- `rag.query_fillers`
- `rag.concrete_facets`
- `rag.explicit_topic_patterns`
- `rag.opening_hours.canonical_token`
- `rag.opening_hours.ignored_subject_tokens`
- `rag.document_domains.markers`
- `rag.compound_terms`
- `rag.explain_patterns`
- `voice.service_names`
- `voice.pronunciation_aliases`

Các file tracked đã xoá:

- `docs/HARDCODE-RESET.md`
- `docs/OVERNIGHT-TASKS.md`
- `src/concierge_kiosk/agent/memory/heuristics.py`
- `src/concierge_kiosk/agent/runtime/model/constraints.py`
- `src/concierge_kiosk/agent/understanding/authority.py`
- `src/concierge_kiosk/operations/production_readiness.py`
- `tests/agent/test_router_contract.py`
- `tools/evaluation/verify_command_price_behavior.py`

Các hàm/method/test đã xoá (gồm hàm thuộc file bị xoá):

- `src/concierge_kiosk/agent/memory/conversation.py::ConversationMemory._resolve_current`
- `src/concierge_kiosk/agent/memory/conversation.py::ConversationMemory._retrieval_current`
- `src/concierge_kiosk/agent/memory/conversation.py::ConversationMemory.candidate_anchors`
- `src/concierge_kiosk/agent/memory/conversation.py::ConversationMemory.reference_query`
- `src/concierge_kiosk/agent/memory/conversation.py::ConversationMemory.reference_query_from_anchor`
- `src/concierge_kiosk/agent/memory/heuristics.py::_anchor_subject`
- `src/concierge_kiosk/agent/memory/heuristics.py::_focuses`
- `src/concierge_kiosk/agent/memory/heuristics.py::_focuses.contains`
- `src/concierge_kiosk/agent/memory/heuristics.py::_subjects`
- `src/concierge_kiosk/agent/memory/heuristics.py::is_followup`
- `src/concierge_kiosk/agent/memory/heuristics.py::is_pending_answer`
- `src/concierge_kiosk/agent/memory/heuristics.py::needs_model_reference_resolution`
- `src/concierge_kiosk/agent/memory/heuristics.py::question_facet`
- `src/concierge_kiosk/agent/runtime/eval/harness.py::TrajectoryScore.unauthorized_action_rate`
- `src/concierge_kiosk/agent/runtime/model/constraints.py::constraint_specs`
- `src/concierge_kiosk/agent/runtime/model/constraints.py::wants_navigation_goal`
- `src/concierge_kiosk/agent/runtime/state.py::_constraint_models`
- `src/concierge_kiosk/agent/tools/planning.py::_category_terms`
- `src/concierge_kiosk/agent/tools/planning.py::advisory_topics`
- `src/concierge_kiosk/agent/tools/planning.py::itinerary_topics`
- `src/concierge_kiosk/agent/tools/planning.py::planning_query_expansions`
- `src/concierge_kiosk/agent/tools/planning.py::planning_search`
- `src/concierge_kiosk/agent/tools/scheduling.py::guest_activity_preferences`
- `src/concierge_kiosk/agent/tools/scheduling.py::guest_budget`
- `src/concierge_kiosk/agent/tools/scheduling.py::guest_preferred_window`
- `src/concierge_kiosk/agent/tools/scheduling.py::guest_preferred_window.convert`
- `src/concierge_kiosk/agent/understanding/authority.py::AuthorityDecision.public`
- `src/concierge_kiosk/agent/understanding/authority.py::AuthorityDecision.requires_confirmation`
- `src/concierge_kiosk/agent/understanding/authority.py::_contains_any`
- `src/concierge_kiosk/agent/understanding/authority.py::evaluate_service_authority`
- `src/concierge_kiosk/agent/understanding/authority.py::explicit_service_intent`
- `src/concierge_kiosk/agent/understanding/domain_nlu.py::_nested_pattern_strings`
- `src/concierge_kiosk/agent/understanding/domain_nlu.py::_nested_terms`
- `src/concierge_kiosk/agent/understanding/intent.py::normalize_intent_with_spans`
- `src/concierge_kiosk/agent/understanding/routing.py::_read_question_type`
- `src/concierge_kiosk/agent/understanding/routing.py::is_location_question`
- `src/concierge_kiosk/agent/understanding/semantic.py::parse_candidates`
- `src/concierge_kiosk/application/conversation/answers.py::build_answer_services.query_keys`
- `src/concierge_kiosk/application/conversation/answers.py::build_answer_services.structured_selectors`
- `src/concierge_kiosk/application/service_actions.py::_configured_venue_name`
- `src/concierge_kiosk/core/domain_vocab.py::all_category_terms`
- `src/concierge_kiosk/core/domain_vocab.py::category_terms`
- `src/concierge_kiosk/core/domain_vocab.py::service_terms_by_catalog_id`
- `src/concierge_kiosk/operations/production_readiness.py::_file_or_dir`
- `src/concierge_kiosk/operations/production_readiness.py::_value`
- `src/concierge_kiosk/operations/production_readiness.py::production_provisioning_gaps`
- `src/concierge_kiosk/rag/grounding/relevance.py::_compound_terms`
- `src/concierge_kiosk/rag/grounding/relevance.py::_contains_phrase`
- `src/concierge_kiosk/rag/grounding/relevance.py::_facet_terms`
- `src/concierge_kiosk/rag/grounding/relevance.py::_query_tokens`
- `src/concierge_kiosk/rag/grounding/relevance.py::concrete_facets_supported`
- `src/concierge_kiosk/rag/grounding/relevance.py::has_explicit_topic`
- `src/concierge_kiosk/rag/grounding/relevance.py::is_opening_hours_query`
- `src/concierge_kiosk/rag/grounding/relevance.py::normalized_query`
- `src/concierge_kiosk/rag/grounding/relevance.py::question_type`
- `src/concierge_kiosk/rag/grounding/relevance.py::requested_facets`
- `src/concierge_kiosk/rag/retrieval/policy.py::explain_requested`
- `src/concierge_kiosk/rag/vectorstore/base.py::VectorStore.delete_release`
- `src/concierge_kiosk/rag/vectorstore/faiss.py::FaissVectorStore.delete_release`
- `tests/agent/test_multilingual_hours.py::test_p0_4_time_question_variants_normalize_to_hours_facet`
- `tests/agent/test_router_contract.py::_payload`
- `tests/agent/test_router_contract.py::_write_profile`
- `tests/agent/test_router_contract.py::test_availability_question_is_a_read_only_schedule_check`
- `tests/agent/test_router_contract.py::test_configured_intent_phrases_do_not_bypass_command_understanding`
- `tests/agent/test_router_contract.py::test_generic_request_change_markers_require_a_prior_request_reference`
- `tests/agent/test_router_contract.py::test_language_switch_is_exact_command_not_substring`
- `tests/agent/test_router_contract.py::test_social_courtesy_phrases_stay_out_of_service_routing`
- `tests/agent/test_service_nlu_recall.py::test_compound_action_is_not_mistaken_for_a_knowledge_followup`
- `tests/agent/test_service_nlu_recall.py::test_vietnamese_until_what_time_questions_are_opening_hours_queries`
- `tests/agent/test_user_journeys.py::UserJourneyTests.test_practical_evening_plan_cues_and_window`
- `tests/agent/test_user_journeys.py::UserJourneyTests.test_verified_entity_can_resolve_navigation_followup`
- `tests/ops/test_maintenance_cleanup.py::test_production_preflight_reports_unprovisioned_assets`
- `tests/rag/test_planning_rag.py::test_current_planning_and_rag_behavior_is_preserved`
- `tests/rag/test_planning_rag.py::test_new_planning_category_and_rag_rewrite_are_config_only_extensions`
- `tests/rag/test_planning_rag.py::test_profile_owns_planning_and_rag_domain_rules`
- `tests/rag/test_semantic_paraphrase.py::SemanticParaphraseTests.test_accepts_natural_yes_no_paraphrase_when_hours_are_unchanged`
- `tests/rag/test_semantic_paraphrase.py::SemanticParaphraseTests.test_dynamic_business_state_stays_extractive`
- `tests/rag/test_semantic_paraphrase.py::SemanticParaphraseTests.test_preserves_price_and_extension_literals_exactly`
- `tests/rag/test_semantic_paraphrase.py::SemanticParaphraseTests.test_rejects_changed_or_omitted_numeric_fact`
- `tools/evaluation/verify_command_price_behavior.py::_assert_unique_cases`
- `tools/evaluation/verify_command_price_behavior.py::_dataset_utterances`
- `tools/evaluation/verify_command_price_behavior.py::_norm`
- `tools/evaluation/verify_command_price_behavior.py::_reuse_calibration_vectors`
- `tools/evaluation/verify_command_price_behavior.py::_types`
- `tools/evaluation/verify_command_price_behavior.py::main`

Các artifact/cache đã xoá (thư mục được liệt kê đại diện toàn bộ nội dung):

- `scratch`
- `.cache/overnight`
- `.cache/overnight-backup`
- `.pytest-tmp`
- `.pytest_cache`
- `data/tts-cache`
- `reports/overnight`
- `scratch_inspect.txt`
- `scratch_vi_noise.txt`
- `.cache/16b3e206f1494e976f36d4828e5babb3c2115356cac4191687a6399dde99e036.json`
- `.cache/4a993c06d871c1dd3d43f1dc1d5b22afb9cc942212601a557506a4f5ab579ac0.json`
- `.cache/683519836fa344459759cf8ffb00eef027aeddd6e1922c61f909f9ca35d29c29.json`
- `.cache/bbe80e4e6c708d852d663a0a15b0a5e8c4f2693473df150dbda5f88a555a66ef.json`
- `.cache/calibrate_05cef49f7846aa2756992a2cfdb582f1eac75c7e77a474f757cdb42ea7bbc5e9.json`
- `.cache/calibrate_0a2c8a62648ff17f93a378642fb833fc135a43915cb9f7d233dce7703448b6a8.json`
- `.cache/calibrate_34be4350732c0fa7dc9a8bc56248c9cb866574e9d24d87e711bc2b9ff46b564b.json`
- `.cache/calibrate_3af02b35595ee1c6edb785e89175976be417d08d96cec013ee226b3a656f4cb7.json`
- `.cache/calibrate_4768c0eb998cc791b0ba7a45e1408345f8e12404730ab00a91955a48b16ce190.json`
- `.cache/calibrate_63e82357e994d519407fa2760f408ee64a8b16218288a4056c309b2bef7c28d6.json`
- `.cache/calibrate_953492efdf7f641bda5fd1453de8daba9eb58582061101d1ac17dafc8718fac2.json`
- `.cache/calibrate_ad9202dbbe33b85875a92f99e5bbd290f3ff2852e0ead0e38e1109814995bc8c.json`
- `.cache/calibrate_embeddings.json`
- `.cache/calibrate_f21c14fa54070fba93bee876b31f13debabdda2483f5b827ea66e578d1cf1872.json`
- `.cache/calibrate_f441a3d341fb1fe7cf22700a7792d89eee20375b7036f4c8aae857a458f70089.json`
- `.cache/d9d600fa19c17c80f02ec5467d5a111c9601317df3e07df5bd34df045e47e064.json`
- `.cache/emergency_embeddings_dict.json`
- `.cache/emergency_scores.json`
- `.cache/loo_predictions.json`
- `data/concierge-test-graph.sqlite3`
- `data/concierge-test-graph.sqlite3-shm`
- `data/concierge-test-graph.sqlite3-wal`
- `data/concierge-test.sqlite3`
- `data/concierge-test.sqlite3-shm`
- `data/concierge-test.sqlite3-wal`
- `data/primary-backup-20261007`
- `data/service-selector-cache/0edeca485d099d29932340938411e5ae2e2b91bcb6bfc4aaec9f251fbe3cc41a.json`
- `data/service-selector-cache/278b93819cdbfc3c77026c53ee58b3ccb03e970d2405834ce708ea0307b4e9ef.json`
- `data/service-selector-cache/d9f608c93cde75f38ef2066530ed6b610e65621769e6a05ea6b500bde3199627.json`
- `data/service-selector-cache/897f803797c94d4f5211d18da505918459d5ade27e2af72ba5851ecf4a62333f.json`
- `data/service-selector-cache/1a707afc4be7991424688dc75c1534374c039aabad252f0041ce3f15448b8ecd.json`
- `data/service-selector-cache/fa27d186b2b5d1af47a4c6fcea5cc97a7aca4e19e865acfe16b7a43e8bcb4f3c.json`
- `data/service-selector-cache/d9d600fa19c17c80f02ec5467d5a111c9601317df3e07df5bd34df045e47e064.json`
- `data/service-selector-cache/4a993c06d871c1dd3d43f1dc1d5b22afb9cc942212601a557506a4f5ab579ac0.json`
- `data/service-selector-cache/b61c8de7cc6ee82bbf2e60712613da2609ce270b5de8b52c8e2cf5b4754f5e62.json`
- `data/service-selector-cache/cf354bd248ceed0746191f4ab46bcc907886036f42af8435bf3286968abf4922.json`
- `reports/nlu-perturbations-command-baseline.jsonl`
- `reports/nlu-robustness-command-baseline.json`
- `reports/nlu/command-calibration-baseline-task.json`
- `reports/nlu/nlu-robustness-command-semantic-new.json`
- `reports/nlu/robustness-before-perturbed.jsonl`
- `reports/retrieval/g-nr-rerank.json`
- `reports/retrieval/g-rr-a03-k4-l64.json`
- `reports/retrieval/g-rr-a03-k4-l96-smoke.json`
- `reports/retrieval/g-rr-a03-k4-l96.json`
- `reports/retrieval/g-rr-a03-k4.json`
- `reports/retrieval/g-rr-a03-k5.json`
- `reports/retrieval/g-rr-a03-k6.json`
- `reports/retrieval/g-rr-a05-k6.json`
- `reports/retrieval/g-rr-a07-k4.json`
- `reports/retrieval/g-rr-a07-k5.json`
- `reports/retrieval/g-rr-a07-k6.json`
- `reports/retrieval/g-rr-k4.json`
- `reports/retrieval/g-rr-rerank.json`
- `reports/retrieval/grounded-baseline.json`
- `reports/retrieval/h-nr-rerank.json`
- `reports/retrieval/h-rr-a03-k4-l64.json`
- `reports/retrieval/h-rr-a03-k4-l96.json`
- `reports/retrieval/h-rr-a03-k4.json`
- `reports/retrieval/h-rr-a03-k5.json`
- `reports/retrieval/h-rr-a03-k6.json`
- `reports/retrieval/h-rr-a07-k4.json`
- `reports/retrieval/h-rr-a07-k5.json`
- `reports/retrieval/h-rr-a07-k6.json`
- `reports/retrieval/h-rr-k4.json`
- `reports/retrieval/h-rr-k6.json`
- `reports/retrieval/h-rr-rerank.json`
- `reports/tool-eval/service-actions-en-bounded2.json`
- `reports/tool-eval/service-actions-en-bounded3.json`
- `reports/tool-eval/service-actions-en-bounded4.json`
- `reports/tool-eval/service-actions-full-local2.json`
- `src/concierge_kiosk/agent/core/__pycache__`
- `src/concierge_kiosk/agent/memory/__pycache__`
- `src/concierge_kiosk/agent/orchestration/__pycache__`
- `src/concierge_kiosk/agent/runtime/eval/__pycache__`
- `src/concierge_kiosk/agent/runtime/execution/__pycache__`
- `src/concierge_kiosk/agent/runtime/model/__pycache__`
- `src/concierge_kiosk/agent/runtime/persistence/__pycache__`
- `src/concierge_kiosk/agent/runtime/planning/__pycache__`
- `src/concierge_kiosk/agent/runtime/presentation/__pycache__`
- `src/concierge_kiosk/agent/runtime/__pycache__`
- `src/concierge_kiosk/agent/tools/__pycache__`
- `src/concierge_kiosk/agent/understanding/__pycache__`
- `src/concierge_kiosk/agent/__pycache__`
- `src/concierge_kiosk/api/guest/__pycache__`
- `src/concierge_kiosk/api/internal/__pycache__`
- `src/concierge_kiosk/api/public/__pycache__`
- `src/concierge_kiosk/api/shared/__pycache__`
- `src/concierge_kiosk/api/staff/__pycache__`
- `src/concierge_kiosk/api/voice/__pycache__`
- `src/concierge_kiosk/api/__pycache__`
- `src/concierge_kiosk/application/conversation/__pycache__`
- `src/concierge_kiosk/application/__pycache__`
- `src/concierge_kiosk/cli/__pycache__`
- `src/concierge_kiosk/core/domain_profile/validate/__pycache__`
- `src/concierge_kiosk/core/domain_profile/__pycache__`
- `src/concierge_kiosk/core/__pycache__`
- `src/concierge_kiosk/domain/requests/__pycache__`
- `src/concierge_kiosk/domain/__pycache__`
- `src/concierge_kiosk/i18n/__pycache__`
- `src/concierge_kiosk/integrations/__pycache__`
- `src/concierge_kiosk/operations/__pycache__`
- `src/concierge_kiosk/persistence/__pycache__`
- `src/concierge_kiosk/rag/embedding/__pycache__`
- `src/concierge_kiosk/rag/grounding/__pycache__`
- `src/concierge_kiosk/rag/index/__pycache__`
- `src/concierge_kiosk/rag/ingestion/__pycache__`
- `src/concierge_kiosk/rag/rerank/__pycache__`
- `src/concierge_kiosk/rag/retrieval/__pycache__`
- `src/concierge_kiosk/rag/text/__pycache__`
- `src/concierge_kiosk/rag/vectorstore/__pycache__`
- `src/concierge_kiosk/rag/__pycache__`
- `src/concierge_kiosk/runtime/__pycache__`
- `src/concierge_kiosk/voice/agent/__pycache__`
- `src/concierge_kiosk/voice/runtime/__pycache__`
- `src/concierge_kiosk/voice/session/__pycache__`
- `src/concierge_kiosk/voice/__pycache__`
- `src/concierge_kiosk/__pycache__`
- `tests/agent/__pycache__`
- `tests/api/__pycache__`
- `tests/data/__pycache__`
- `tests/domain/__pycache__`
- `tests/ops/__pycache__`
- `tests/rag/__pycache__`
- `tests/voice/__pycache__`
- `tests/__pycache__`
- `tools/config/__pycache__`
- `tools/data_ingestion/__pycache__`
- `tools/evaluation/__pycache__`
- `tools/knowledge/__pycache__`
- `tools/maintenance/__pycache__`
- `tools/manifest/__pycache__`
- `tools/nlu/__pycache__`
- `tools/operations/__pycache__`
- `tools/packaging/__pycache__`
- `tools/runtime/__pycache__`
- `tools/security/__pycache__`
- `tools/validate/__pycache__`
- `tools/_shared/__pycache__`
- `tools/__pycache__`

Vulture đã chạy đúng `python -m vulture src/concierge_kiosk tools --min-confidence 60`; xoá code chết và import/local không dùng đã xác định. Bỏ qua route FastAPI/model Pydantic theo yêu cầu. Các callback/framework override, trường serialized/record, enum protocol, SQLite `row_factory`, ZipInfo `compress_type`, hook urllib/Pipecat/http.server và security middleware là điểm gọi động hoặc contract, không coi là code chết chỉ vì vulture không thấy lời gọi.

Giữ nguyên các vùng dữ liệu/asset được bảo vệ, `emergency_gate.py`, regex khẩn cấp, config xác nhận và `domain/requests/`. Chỉ giữ cache service-selector mới nhất theo `LastWriteTimeUtc`. Không đổi nhánh. Các tên/hàm/biến còn thiếu ở chỗ gọi không được thay bằng logic mới; lỗi import/runtime/test được chấp nhận theo kế hoạch. Script dọn tạm của đợt này đã xoá.

Đã chạy `python tools/config/repin_configs.py` sau lần xoá cuối: thành công. Hash config được ghim ở `config/agent-domain.sha256` và `CONCIERGE_DOMAIN_PROFILE_SHA256` trong `.env.example`; không sửa release được bảo vệ. Rà soát tĩnh không còn các khoá/hàm bị loại và diff các vùng được bảo vệ rỗng. Không chạy test hay server.

Rà soát cuối đã xoá hai lời gọi `_constraint_models` còn sót và khối kiểm xoá release trong test vectorstore; phần kiểm filter được giữ với tên `test_vector_backend_filters`. Không bổ sung logic thay thế hay chạy test.
