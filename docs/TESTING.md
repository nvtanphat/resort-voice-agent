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

## Real-model smoke gần nhất: chưa đạt nghiệm thu

Đã có 3 Ollama POST lịch sử: preload rỗng, food NLU timeout và một evidence-copy generation ngoài ý định sau timeout. Run là **PROTOCOL_FAILURE**: stop-after-model-failure chưa giữ được, dù còn trong ceiling. Boundary read-only suppression và stop_on_failure đã được sửa và test deterministic; không có replay real-model thành công tiếp theo.

| Quan sát | Giá trị |
|---|---|
| Preload HTTP | 4.375 s, HTTP 200, done_reason=load |
| Food NLU | 3.016 s HTTP / 3.922 s API, timeout |
| Provider loading/prefill/generation counters | NOT_RETURNED; không coi bằng 0 |
| Sau preload | 2,059,284,480 bytes RAM available, 87% load |
| Model context trong smoke | CPU, context 4096 đã kiểm tra |
| Source SQLite | Hash không đổi; các delta service/proposal/emergency writes bằng 0 |

Đây là observations đơn lẻ, không latency distribution/p95 hay real-model accuracy. Nguyên bản lỗi preload ValueError S01–S15 chưa có stack để tái hiện chính xác.

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
