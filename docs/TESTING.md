# Kiểm thử và bằng chứng nghiệm thu

## Cách chạy an toàn

Chạy từ root, tuần tự, trong environment test riêng. Không source development/model environment vào test. Test đọc shipped knowledge dùng `shipped_db`/`tests/shipped_db.py`; mọi luồng ghi dùng SQLite tạm/copy.

```powershell
python tools/runtime/run_offline_tests.py -- tests/agent/test_pending_read_boundaries.py -q
python tools/runtime/run_offline_tests.py --sdk -- tests/agent/test_router_acceptance_audit.py -q
python tools/config/repin_configs.py --check
```

Runner có Windows RAM preflight ≥1 GiB, subprocess timeout mặc định 150 s, chặn sockets thật/heavy model imports và HTTP model boundary. Basetemp/pytest cache nằm trong system temp; child không ghi bytecode vào repo. Fixtures lưu masked transcript trong tmp_path. --sdk dùng cache local có sẵn, không install/download; checkout mới có thể cần optional extra observability.

Direct pytest có addopts basetemp trong pyproject; nếu dùng ngoài runner phải chọn basetemp riêng và profile/DB đúng. Không chạy parallel pytest hoặc full benchmark khi chỉ kiểm tra một thay đổi nhỏ. Strict-xfail ở tests/rebuild_pending.py giữ nguyên ý nghĩa; chỉ bỏ khi original case PASS, không nới assertion/nhãn.

## Regression khác evaluation

Existing harness ở `agent/runtime/eval/harness.py` và tools/evaluation chấm trajectories, task journeys, commands, retrieval, tools, voice và latency. Không có evaluator Langfuse thứ hai.

| Kết quả | Chứng minh được |
|---|---|
| Deterministic/mock test PASS | Contract trên scripted inputs/transport và business state |
| Authenticated ASGI API replay | Application integration trên app thật/DB copy, trong process |
| Real-model replay | Hành vi model tại inputs/hardware/profile đã đo |
| Browser/voice/live TCP E2E | Journey thực tế khi đã chạy và có receipt/audio/browser evidence |
| Cloud read-back | Ingestion/hierarchy/scores/privacy tại project đã được cấp quyền |

Safe rejection của wrong command không là intent accuracy PASS. Oracle reject không là real-model false-rejection rate. P50/P95 cần đủ mẫu, không suy ra từ một timeout. Nêu mẫu số, split/ngôn ngữ, model/provider, cache/RAM và failure classes.

## Fast-path acceptance contract

Chấm lượt thực sự chạy, giữ ground truth được reviewed. Intent correctness, slot fidelity và dialogue action là các chiều riêng. Sai service, unauthorized action hoặc persisted service write trước confirmation là lỗi safety; missing slot có thể cần clarification.

Trigger holdout đo precision/adversarial behavior; traffic holdout đo coverage. Tách bản dịch/source descendants khỏi independent samples. Báo Wilson CI; chỉ tuyên bố precision ≥99% được chứng minh khi cận dưới Wilson 95% ≥0.99 (ít nhất 381 eligible executions không lỗi cho mỗi nhánh). Không lấy mẫu nhỏ hoặc skipped cases làm đủ bằng chứng.

Khóa evaluation theo existing evaluator: code/config/holdout/model hashes và flags; khóa cũ không cấp acceptance cho working tree mới. Langfuse score adapter chỉ forward metrics đã có, giữ scale/unit/association và stable re-export identity; result không có real trace báo unlinked.

## Bằng chứng đã chạy, không chạy lại model trong đợt tài liệu

Các logs/output cũ đã được xóa theo cleanup; bảng dưới giữ kết quả ghi nhận, không phải bản raw artifact có thể inspect lại. Counts của các nhóm overlap không được cộng thành unique tests.

| Nhóm | Kết quả | Thời gian |
|---|---:|---:|
| Pending/read/context/change-confirmation, trước cleanup | 59 passed | 27.23 s |
| Grounded service/coverage/readiness/journeys, trước final clock fix | 153 passed | 33.34 s |
| Profile/no-hardcode/phrase guards | 55 passed | 11.79 s |
| Semantic gate + Langfuse SDK/mock | 124 passed | 5.37 s |
| Clock/date/service/readiness/journeys | 176 passed | 32.79 s |
| Cleanup: observability/SDK acceptance | 41 passed | 10.53 s |
| Cleanup: pending/idempotency/voice contract | 39 passed | 6.89 s |
| Sau xóa output và sửa fixture: SDK acceptance + pending/read | 29 passed | 6.74 s |

Hai nhóm cleanup 41/39 và nhóm sau cleanup 29 đều ghi nhận 0 real network connections, 0 heavy model loads. SDK dùng cache đã chuyển ra khỏi reports. Initial post-cleanup probe 29 passed trong 6.58 s phát hiện fixture tạo lại 5 JSON transcripts; output bị xóa, fixture sửa sang tmp_path và final probe ở bảng trên không tạo lại reports/cache.

Source AST, config pins, Markdown links, whitespace và protected-file hashes đã được kiểm tra trong cleanup. Source/data/config của công việc trước đó được giữ; fixture output path là thay đổi có chủ đích. Việc viết lại tài liệu chỉ validate tĩnh, không giả một test run mới.

Operator từng cung cấp “714 targeted tests / 11 pre-existing failures / 4 skips”; exact failing node IDs và nguyên bản transcript chưa có. Không nhập các con số đó vào PASS ở bảng này.

## Real-model E2E (2026-10-10): đo được, chưa đạt nghiệm thu

Authenticated ASGI API thật (session + CSRF + origin), SQLite copy tạm, selector bge-m3 thật, SLM qua Ollama loopback với `num_gpu=0` (CPU, Ryzen 5 5600H, 16 GB), chỉ một SLM resident mỗi lần chạy. Không phải voice/browser E2E. Mỗi lượt khách là một session mới trừ journeys nhiều lượt.

| Bộ | Nguồn | n | Kết quả (qwen2.5:3b, code cuối) | Latency |
|---|---|---:|---|---|
| Service holdout | `evaluation/gold/vi_test` (47 service) + `evaluation/holdout/service_workflow` (12/ngôn ngữ en/zh/ko, stratified) | 83 | 36/83 (43%): vi 28/47, en 2/12, zh 4/12, ko 2/12 | p50 5.4 s, p95 8.06 s |
| Cùng bộ, gate trước thay đổi | như trên, tắt đường semantic agreement | 83 | 8/83 (10%) | p50 5.8 s, p95 8.14 s |
| Safety (hard negatives) | `evaluation/gold/vi_hard_negatives` | 80 | 80/80 không proposal/không write | p50 3.7 s, p95 9.8 s |
| Journeys nhiều lượt | 14 kịch bản tự viết (vi/en/zh/ko) | 22 lượt | 7/7 commit: 0 write trước confirm, đúng 1 row sau confirm, replay cùng request_id, session khác 403; hủy draft, multi-intent, emergency, handoff, chỉ đường, sửa số lượng đúng | xem log |

Thất bại còn lại trên service holdout: 13 timeout SLM (>8 s), 15 `nlu_failure` (model sai shape/goal bị gate từ chối), còn lại là sai loại command (knowledge/status/check_schedule thay vì service). Mẫu nhỏ, không đủ để tuyên bố tỷ lệ với Wilson CI chặt; chỉ dùng để so sánh tương đối giữa các build.

So sánh model (cùng bộ 83, json + spec, CPU, một model resident):

| Model | Đúng | Timeout >8 s | Đúng trên lượt kịp trả lời | p50 |
|---|---:|---:|---:|---:|
| qwen2.5:3b | 29/83 | 7 | 38% | 5.2 s |
| qwen3:4b-instruct-2507 | 27/83 | 37 | 59% | 7.6 s |
| gemma3:4b | 2/83 | 78 | — (prefix cache không được tái sử dụng) | 8.0 s |

qwen3-4b chính xác hơn rõ rệt nhưng vượt budget 8 s trên CPU này; giữ qwen2.5:3b. Fine-tuning không được thử: lỗi còn lại chủ yếu là latency và năng lực model ở kích thước này, chưa đủ điều kiện ở mục SLM policy.

Đo offline cho gate (không gọi model): leave-one-situation-out trên 736 lượt service train, lexical gate cũ từ chối 57–69% request vàng theo ngôn ngữ; đường đồng thuận semantic nhận ≈82% (`tools/nlu/calibrate_semantic_support.py`, ngưỡng 0.70).

### Kịch bản thực tế (2026-10-10, đợt 2)

19 kịch bản tự viết (văn nói, không dấu, sai chính tả, trộn tiếng Anh; vi/en/zh/ko), model thật qwen2.5:3b trên CPU: 15/19 đúng. 10/10 commit: 0 write trước confirm, đúng 1 row sau confirm, replay cùng request_id, session khác 403. Đã sửa và xác nhận: "tối nay 7 giờ" → 19:00 và "đổi sang 8 giờ" → 20:00 (trước là 07:00/08:00); "6am" → 06:00; giờ trả phòng trả lời được ở vi/zh/en/ko (0.2 s cho vi/zh); dị ứng ("mình dị ứng hành") có trên phiếu nhân viên. Còn lỗi (do model): cảm ơn chuyện đã xong, phàn nàn tiếng ồn, sai chính tả tiếng Anh ("towles") đều nhận "chưa hiểu rõ"; giá massage không có trong dữ liệu.

### Đổi sang qwen2.5:7b (2026-10-10)

Cùng 19 kịch bản thực tế, CPU: 19/19 hiểu đúng ý (3b: 15/19) — 7b xử lý được cảm ơn chuyện đã xong, phàn nàn tiếng ồn (chuyển nhân viên), sai chính tả "towles". Mọi commit: 0 write trước confirm, đúng 1 row, replay cùng request_id, session khác 403. Latency lượt cần model 7–12 s (trung bình ~9.5 s); fast path/read-only 0.1–0.5 s. Tối ưu cho 7b: model chỉ điền slot dạng chữ (phòng, số lượng, giờ, ngày, số khách do server tự trích — `SERVER_EXTRACTED_SLOTS`), giữ 2 few-shot (bỏ few-shot giảm đúng từ 9/9 xuống 6–7/9 trong A/B), ngân sách NLU 12 s. Prefix system prompt được cache, nhưng mỗi token phía sau tốn ~28 ms trên CPU nên phần thay đổi mỗi lượt quyết định độ trễ.

## Transcript application khi model unavailable

Đây là FastAPI/LangGraph/catalog thật qua ASGI TestClient, session cookie + CSRF và SQLite copy; Qwen budget=0, không BGE/reranker load. **REAL_APPLICATION_API_MODEL_UNAVAILABLE**, không phải real-model/browser/voice PASS.

Không gọi các lượt dưới là S01–S15 vì transcript gốc chưa tìm thấy.

| Case/input synthetic | Câu trả lời đã ghi nhận | API ms | Layer ms, inclusive | Hành vi/DB |
|---|---|---:|---|---|
| food-list-user-specified: 我在1105房间，想点一份炒饭和一杯橙汁送到房间。 | 系统暂时无法处理您的要求。请稍后主动重试；本轮尚未提交任何请求。 | 688.0 | no RAG/model/graph tool | FAIL task / safe no-write |
| item-and-taxi-boundary: Bring 2 towels to room 705 and call a taxi at 5pm | The system could not process your request. Please try again when ready; this turn has not sent a request. | 31.0 | no RAG/model/graph tool | FAIL task / safe no-write |
| verified-spa-followup: What time does the spa open? | V-Senses Wellness & Spa opening hours: 09:00–22:00 (daily). | 235.0 | Memory=0.0, Memory=0.0, Retrieval=16.0, LangGraph=172.0 | PASS expected boundary / 0 writes |
| verified-spa-followup: Please book one at 4pm | I have enough information to prepare this request. Please review it before confirming. | 62.0 | Memory=0.0, Memory=0.0, Memory=16.0, Memory=0.0, SemanticGateAndProjection=16.0, Memory=0.0, LangGraph=0.0 | PASS expected boundary / 0 writes |
| verified-spa-followup: Please cancel it | I cleared the draft request. Nothing was submitted. | 31.0 | SemanticGateAndProjection=0.0 | PASS expected boundary / 0 writes |
| clear-information: What time does the swimming pool open? | Resort Swimming Pools schedule: 06:00–18:30 (Lifeguard Hours). | 63.0 | Memory=0.0, Memory=0.0, Retrieval=15.0, LangGraph=15.0 | PASS expected boundary / 0 writes |
| unresolved-action-unavailable: Please arrange a helicopter | The system could not process your request. Please try again when ready; this turn has not sent a request. | 15.0 | no RAG/model/graph tool | PASS expected boundary / 0 writes |

Layer intervals nested/inclusive, không cộng làm exclusive latency. Giá trị 0 có thể do timer quantization. Selector/embedder không chạy là NOT_RUN, không tạo spans giả.

## Langfuse và release

**OFFLINE_INTEGRATION_PASS**, **CLOUD_INGESTION_NOT_VERIFIED**. SDK/mock kiểm tra root/children, semantic rejection, masking, score association và idempotency; chưa chứng minh dashboard ingestion.

Cloud cần operator credentials/permission và synthetic filtered data, rồi read-back hierarchy/scores/privacy; exporter HTTP thành công đơn lẻ chưa đủ. Real-model và voice/browser nghiệm thu riêng.

CI/release gates có định nghĩa trong .github/workflows/ci.yml và tools/validate; không sửa hoặc tự chạy full CI khi chỉ sửa docs. Các blockers ở [Limitations](LIMITATIONS.md), test cases ở [backend cases](backend-test-cases.md).
