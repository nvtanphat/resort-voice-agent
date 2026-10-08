# Refactor audit — 2026-10-09

Baseline: `fac9b1d` (nhánh `phase4-handoff`). Tài liệu này ghi lại kết quả review "Master Plan — Resort Voice Agent" so với code thật, các thay đổi đã làm, những gì đã xóa (kèm cách khôi phục), kết quả smoke với model thật và các lỗ hổng còn lại.

## 1. Review master plan

| Nhận định trong plan | Thực tế trong code | Xử lý |
| --- | --- | --- |
| `ConciergeAgent` cũ chỉ còn `_status`/`_verified` được dùng | Đúng. Vòng lặp `run()` và các dataclass riêng của nó không có ai gọi | Đã xóa, helper chuyển thành `tool_status`/`tool_verified` |
| Có hai cách thực thi agent | Sai. Chỉ có một vòng lặp LangGraph. "Fallback loop" chỉ còn trong docstring; `CONCIERGE_ORCHESTRATOR` không ai đọc | Sửa docstring, bỏ biến môi trường |
| `fast_router.py` cần xem xét gỡ | Sai. Đang được dùng (`engine.py` layer B) | Giữ nguyên |
| Hai LangGraph phải giữ riêng | Đúng: graph theo lượt và graph vòng đời yêu cầu độc lập | Giữ nguyên |
| Không ghi trước khi khách xác nhận | Đúng. INSERT duy nhất nằm trong `confirm()` | Giữ nguyên |
| (Plan bỏ sót) đường dispatch chết | `specialist_answer`, `KnowledgeService`, `CapabilityDispatcher`, `CapabilityRegistry` không có ai gọi | Đã xóa |
| (Plan bỏ sót) lỗi P0 khi dedupe lúc confirm | Session khác cùng số phòng nhận nguyên row của người khác; proposal hết hạn vẫn "thành công" | Đã sửa (mục 2) |
| (Plan bỏ sót) `conditional` bị mất phần đặt | Một `StartGoal(conditional)` đứng một mình bị route sang read-only | Đã sửa (mục 2) |
| 7 file tài liệu bàn giao | Trùng với `docs/ARCHITECTURE.md`, `OPERATIONS.md`, `LIMITATIONS.md` | Gộp thành file này và cập nhật CLAUDE.md |

## 2. Thay đổi (mỗi dòng là một commit, rollback độc lập)

| Commit | Nội dung |
| --- | --- |
| `a7e8fd2` | Xóa vòng lặp agent cũ và đường dispatch chết. Gộp kiểm tra "read đã verified" và observation "unavailable" về một chỗ |
| `1b663e6` | Dedupe lúc confirm: check hết hạn/hủy trước; khác session chỉ nhận mã tham chiếu công khai, không có status link (`backend.request.merged_with_existing`, đủ 4 ngôn ngữ) |
| `2405b12` | Chỉ còn một tầng chiếu command → route. Một command sai chỉ bị bỏ riêng nó (`partially_accepted`). `conditional` chạy trong loop, có cổng `availability_confirmed` |
| `df44628` | Test: khi command đã nêu tên dịch vụ, check_schedule không cần selector |
| `2a2add6` | Hết hardcode: preference lấy từ profile; xóa `stt_prompt_languages` (không ai đọc); xóa parser `**Localizations**` (không khớp tài liệu nào) |
| `623590c` | Guard hardcode bắt cả tập con ≥ 2 mã ngôn ngữ |
| `e97ddd1` | Xóa dataset không có consumer (mục 3) |
| `9e2e0bb` | Bảng `preferences.constraints` (ontology, không tính vào phrase budget) |
| `7e14156` | Từ smoke: bỏ `conditional` với dịch vụ không có nguồn availability; một dịch vụ thì vẫn giữ draft |
| `1d66cb4` | Từ smoke: sửa giờ ở lượt sau giữ buổi của draft (`corrected_time`); sửa regex "minus clock" gây TypeError |

`reference_resolver.py` được **giữ lại** vì Phase 2 trong `plan.md` sẽ dùng `model_reference_choice`.

## 3. Ledger dataset đã xóa

Khôi phục một file: `git restore --source=fac9b1d -- <path>` (sau đó thêm lại entry trong manifest/contracts và chạy `tools/manifest/refresh_*.py`).

Đã xóa (chỉ được liệt kê trong contracts/manifests, không có code, tool, test hay tài liệu nào đọc, kể cả qua glob):

- `datasets/evaluation/{audit,config,agent}/*`
- `datasets/evaluation/frames/split.json`
- `datasets/evaluation/retrieval/{metrics,natural_queries,split}.json`
- `datasets/evaluation/voice_text/index.json`
- `datasets/knowledge/documents/manifest.json`
- `datasets/quarantine/{manifest.json,source_candidates.jsonl}`
- `datasets/synthetic/operations/policies/dispatch_slas.json`
- `datasets/synthetic/operations/metadata/nonpublic_policy_registry.json`
- `datasets/training/rag/vi_grounded.jsonl`, `datasets/schemas/training/rag_grounded.schema.json`
- `datasets/training/agent/research/humanized_vi_web_2026_10.json`
- `tools/data_ingestion/` và hằng `EVAL_GOLD`

Giữ lại sau khi kiểm tra lại:

- `evaluation/gold/*`, `frames/semantic_frames.jsonl`, `end_to_end/edge_cases.jsonl` và schema của chúng. Lý do: `tests/ops/test_human_review.py` và guard chống rò rỉ evaluation→training (rglob `evaluation/**/*.jsonl`) đang đọc chúng.
- `benchmarks/edge_case.schema.json`: được `$ref` trong `end_to_end.schema.json`.
- Schema `context_labels`, `entity_display_labels`, `backend_plan_turn_case`: đang gắn với contracts.
- `agent_candidate.schema.json`: Phase 2 trong `plan.md` cần.

## 4. Kiểm chứng

- Test: baseline 681 passed / 18 xfailed. Sau thay đổi: 701 passed / 18 xfailed, 0 failed.
- Các bước CI: compileall, `repin_configs --check`, `build_runtime_profiles --check`, `audit_data`, `validate_contracts`, các validator schema/semantics/agent_domain/property_dataset, bandit, `node --check` đều đạt.
- Không chạy: `pip_audit` (dependency không đổi), `npm build` (không sửa `frontend/src`).
- Test mới đều được chạy trên `fac9b1d` để xác nhận chúng bắt được lỗi cũ (dedupe: 4/5 fail trên baseline; guard ngôn ngữ: fail trên baseline).

## 5. Smoke với model thật (qwen2.5:3b + bge-m3, GPU dev, server chạy trên bản sao DB)

| # | Câu | Kết quả | Ghi chú |
| --- | --- | --- | --- |
| 1 | Nhà hàng trong resort mở cửa đến mấy giờ? | FAIL (an toàn) | `no_evidence`: câu hỏi mơ hồ giữa nhiều nhà hàng; hệ thống gợi ý chủ đề, không bịa |
| 2 | Ở đó có phục vụ bữa tối không? | PASS | Có trích dẫn (Tàya House 18:00–22:00) |
| 3 | Đặt bàn 4 người lúc 7 giờ tối. | PASS | Draft 19:00, 4 khách, `business_writes 0` |
| 4 | Không, đổi thành 8 giờ, chưa đặt nhé. | PASS sau sửa | Lần đầu ra 08:00 (lỗi), đã sửa thành 20:00 và kiểm tra lại với model thật |
| 5 | Đúng rồi, xác nhận. | NOT_APPLICABLE | Xác nhận nghiệp vụ đi qua thẻ review trên UI (`/api/requests/confirm`); script chỉ gọi `/api/ask` |
| 6 | Tôi không cần dọn phòng nữa. | PASS (an toàn) | Không tạo sở thích hay yêu cầu nào; trả lời bằng thông tin housekeeping |
| 7 | Nếu còn bàn thì báo tôi trước, đừng đặt vội. | PASS (an toàn), hiểu sai | Model đề xuất `StartGoal(human_assistance, conditional)`; không có đề xuất nào được tạo |
| 8 | Cho tôi 2 khăn tắm và spa mở đến mấy giờ? | PASS sau sửa | Lần đầu mất yêu cầu khăn; sau sửa: trả lời giờ spa và hỏi số phòng cho khăn |

Hành trình dịch vụ qua HTTP: prepare → confirm (gửi lại trả về cùng request) → staff approve → start → complete: PASS. Trong lúc chạy, việc khác session cùng phòng được gộp đúng (không lộ request của người khác).

Độ trễ mỗi lượt khoảng 7–10 giây trên GPU dev. Chưa đo trên CPU (`num_gpu=0`).

## 6. Lỗ hổng còn lại

- **"Có, và …" khi đang chờ xác nhận:** Layer A coi cả câu là `Confirm`, phần câu hỏi phía sau bị bỏ. Muốn sửa đúng thì cần compose được Confirm cùng read answer có trích dẫn trên route `confirmation`.
- **Handoff + câu hỏi:** câu trả lời read bị bỏ. Route `handoff` là fast và không được mang citation; còn `multi_task` lại bỏ observation handoff vì không có candidate. Cần thiết kế lại phần compose.
- **Mức không chắc chắn:** dưới ngưỡng thì chọn knowledge read thay vì hỏi lại. An toàn vì không ghi gì; muốn có vùng "clarify" thì phải calibrate thêm.
- **Model nhỏ:** hay đặt `conditional=true` hoặc chọn sai dịch vụ (#7), và đôi khi không trả command khi câu chứa phủ định (#4 lần đầu). Server chặn được tác hại, nhưng chất lượng hiểu câu cần Phase 2 trong `plan.md`.
- **`num_predict: 220`** (`commands.py`) có thể cắt câu ghép dài.
- **`manage_request_tool`** hủy/sửa request đã gửi ngay từ câu chat, không qua bước xác nhận UI. Cần quyết định.
- **Proposal gộp với request có sẵn:** graph checkpoint của proposal đó vẫn ở `guest_confirmation` (`orchestration_sync: deferred`). Hành vi này đã có từ trước, cả khi trùng trong cùng session.
- **`datasets/schemas/manifest.json`:** hash đã lệch từ trước baseline, và không có tool nào kiểm tra file này.
- **Test chập chờn:** hai lần chạy full suite fail rồi tự hết, không tái hiện khi chạy lại hay chạy riêng. Lần đầu 4 test `tests/data` lúc qua nửa đêm (nghi phụ thuộc ngày), lần sau 50 test `tests/rag` khi smoke server đang chạy song song.
- **Chưa audit sâu:** phase voice, UI và vận hành. Còn nhiều default `language='en'` trong các hàm tiện ích, và message tiếng Anh trong `routes.py`. Frontend có toast đa ngôn ngữ viết cứng trong `App.tsx`.
