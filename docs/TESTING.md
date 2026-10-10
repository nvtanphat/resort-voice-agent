# Kiểm thử và bằng chứng nghiệm thu

## Cách chạy an toàn

Chạy từ root, tuần tự, trong environment test riêng. Không source development/model environment vào test. Test đọc shipped knowledge dùng `shipped_db`/`tests/shipped_db.py`; mọi luồng ghi dùng SQLite tạm/copy.

```powershell
python tools/runtime/run_offline_tests.py -- tests/agent/test_pending_read_boundaries.py -q
python tools/runtime/run_offline_tests.py --sdk -- tests/agent/test_router_acceptance_audit.py -q
python tools/config/repin_configs.py --check
```

Runner có Windows RAM preflight ≥1 GiB, subprocess timeout mặc định 150 s, không cho phép sockets thật/heavy model imports và HTTP model boundary. Basetemp/pytest cache nằm trong system temp; child không ghi bytecode vào repo. Fixtures lưu masked transcript trong tmp_path. --sdk dùng cache local có sẵn, không install/download; checkout mới có thể cần optional extra observability.

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

Các công cụ dưới đây giữ phạm vi và oracle riêng; không dùng kết quả của một công cụ để thay thế công cụ khác.

| Công cụ | Phạm vi / caller đã xác minh |
|---|---|
| `tools/evaluation/evaluate_intent_router.py` | NLU qua API thật, SQLite copy, holdout khóa hash, before/after; gọi `run_human_review_probe.oracle` |
| `tools/evaluation/evaluate_command_understanding.py` | Đo command understanding trực tiếp; cung cấp loader cho `audit_router_evidence` |
| `tools/evaluation/audit_router_evidence.py` | Audit ontology/gate offline, không đo model accuracy; được `test_router_acceptance_audit.py` gọi |
| `tools/evaluation/probe_real_behavior.py` | Probe server HTTP đang chạy; không có fallback app/DB giả trong process |
| `tools/evaluation/run_human_review_probe.py` | Direct reviewed workflow scenarios qua HTTP boundary; oracle dùng lại trong router evaluator |

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

Offline 2026-10-10 (bằng chứng đã ghi nhận, không chạy lại trong đợt dọn code): agent 910 passed, domain/data/api/voice 142 passed, ops 82 passed; còn 4 test cũ fail giống hệt trên HEAD trước thay đổi (3 test kỳ vọng knowledge read khi model unavailable, trái với contract `nlu_failure` hiện hành; 1 test kỳ vọng readback dùng tên catalog thay vì item) và 2 test RAG cần model thật không được offline runner cho phép.

## Real-model E2E (2026-10-10): đo được, chưa đạt nghiệm thu

Authenticated ASGI API thật (session + CSRF + origin), SQLite copy tạm, selector bge-m3 thật, SLM qua Ollama loopback với `num_gpu=0` (CPU, Ryzen 5 5600H, 16 GB), chỉ một SLM resident mỗi lần chạy. Không phải voice/browser E2E. Mỗi lượt khách là một session mới trừ journeys nhiều lượt.

| Bộ | Nguồn | n | Kết quả (qwen2.5:3b, code cuối) | Latency |
|---|---|---:|---|---|
| Service holdout | `evaluation/gold/vi_test` (47 service) + `evaluation/holdout/service_workflow` (12/ngôn ngữ en/zh/ko, stratified) | 83 | 36/83 (43%): vi 28/47, en 2/12, zh 4/12, ko 2/12 | p50 5.4 s, p95 8.06 s |
| Cùng bộ, gate trước thay đổi | như trên, tắt đường semantic agreement | 83 | 8/83 (10%) | p50 5.8 s, p95 8.14 s |
| Safety (hard negatives) | `evaluation/gold/vi_hard_negatives` | 80 | 80/80 không proposal/không write | p50 3.7 s, p95 9.8 s |
| Journeys nhiều lượt | 14 kịch bản tự viết (vi/en/zh/ko) | 22 lượt | 7/7 commit: 0 write trước confirm, đúng 1 row sau confirm, replay cùng request_id, session khác 403; hủy draft, multi-intent, emergency, handoff, chỉ đường, sửa số lượng đúng | xem log |

Kết quả không khớp nhãn trên service holdout: 13 timeout SLM (>8 s), 15 `nlu_failure` (shape/goal của model không qua gate), còn lại là loại command không khớp nhãn (knowledge/status/check_schedule thay vì service). Cỡ mẫu chưa đáp ứng điều kiện Wilson CI ở acceptance contract; kết quả dùng để so sánh các build trong phạm vi mẫu này.

So sánh model (cùng bộ 83, json + spec, CPU, một model resident):

| Model | Đúng | Timeout >8 s | Đúng trên lượt kịp trả lời | p50 |
|---|---:|---:|---:|---:|
| qwen2.5:3b | 29/83 | 7 | 38% | 5.2 s |
| qwen3:4b-instruct-2507 | 27/83 | 37 | 59% | 7.6 s |
| gemma3:4b | 2/83 | 78 | — (prefix cache không được tái sử dụng) | 8.0 s |

Kết quả accuracy của qwen3-4b cao hơn trong mẫu đối chiếu; latency vượt budget 8 s trên CPU này; giữ qwen2.5:3b. Fine-tuning không được thử: lỗi còn lại chủ yếu là latency và năng lực model ở kích thước này, chưa đủ điều kiện ở mục SLM policy.

Đo offline cho gate (không gọi model): leave-one-situation-out trên 736 lượt service train, lexical gate cũ từ chối 57–69% request vàng theo ngôn ngữ; đường đồng thuận semantic nhận ≈82% (`tools/nlu/calibrate_semantic_support.py`, ngưỡng 0.70).

### Kịch bản thực tế (2026-10-10, đợt 2)

19 kịch bản tự viết (văn nói, không dấu, sai chính tả, trộn tiếng Anh; vi/en/zh/ko), model thật qwen2.5:3b trên CPU: 15/19 đúng. 10/10 commit: 0 write trước confirm, đúng 1 row sau confirm, replay cùng request_id, session khác 403. Kết quả đối chiếu: "tối nay 7 giờ" → 19:00 và "đổi sang 8 giờ" → 20:00 (trước là 07:00/08:00); "6am" → 06:00; giờ trả phòng trả lời được ở vi/zh/en/ko (0.2 s cho vi/zh); dị ứng ("mình dị ứng hành") có trên phiếu nhân viên. Còn lỗi (do model): cảm ơn chuyện đã xong, phàn nàn tiếng ồn, sai chính tả tiếng Anh ("towles") đều nhận "chưa hiểu rõ"; giá massage không có trong dữ liệu.

### Đổi sang qwen2.5:7b (2026-10-10)

Cùng 19 kịch bản thực tế, CPU: 19/19 hiểu đúng ý (3b: 15/19) — 7b xử lý được cảm ơn chuyện đã xong, phàn nàn tiếng ồn (chuyển nhân viên), sai chính tả "towles". Mọi commit: 0 write trước confirm, đúng 1 row, replay cùng request_id, session khác 403. Latency lượt cần model 7–12 s (trung bình ~9.5 s); fast path/read-only 0.1–0.5 s. Tối ưu cho 7b: model chỉ điền slot dạng chữ (phòng, số lượng, giờ, ngày, số khách do server tự trích — `SERVER_EXTRACTED_SLOTS`), giữ 2 few-shot (bỏ few-shot giảm đúng từ 9/9 xuống 6–7/9 trong A/B), ngân sách NLU 12 s. Prefix system prompt được cache, nhưng mỗi token phía sau tốn ~28 ms trên CPU nên phần thay đổi mỗi lượt quyết định độ trễ.

### Intent router — đo bằng model thật (2026-10-10)

qwen2.5:7b + bge-m3 trên CPU laptop, API thật, SQLite copy, không gửi xác nhận. Nhãn do agent viết (`unreviewed`), chưa có người duyệt độc lập.

| Bộ | Cỡ | Kết quả | Ghi chú |
|---|---:|---|---|
| Chẩn đoán (lời nói tự nhiên, dùng để tìm nguyên nhân) | 42 | 28 → 34 | trước/sau cổng mới; dùng để sửa nên không phải ước lượng độc lập |
| Giữ riêng `intent_router_conversational` (khóa sha256 trước khi sửa, chạy một lần) | 60 | 44/60 — vi 20/30, en 6/10, zh 10/10, ko 8/10 | p50 12.7 s, p95 19.8 s; không có số "trước" cùng điều kiện |
| `vi_hard_negatives` | 80 | 80/80, 0 write, 0 proposal | p50 10.2 s, p95 13.0 s |

Lỗi còn lại trên bộ giữ riêng (16): model chọn dịch vụ gần nghĩa hoặc bỏ sót một việc (6); cổng loại lệnh đúng (4, gồm câu hỏi lịch sự tiếng Hàn "…수 있나요?"); câu hỏi chỗ trống/cảm ơn ra `nlu_failure` hoặc thừa một lượt đọc (4); khẩn cấp (2: "bóng đèn… cháy" từng thành khẩn cấp đầy đủ — đã chuyển sang hỏi xác nhận; rò nước vào vùng hỏi lại). Một câu của bộ này đã bị xem phân đoạn trong lúc phát triển (không xem kết quả, không sửa theo).

Thay đổi gốc:

- Cổng ngữ nghĩa xét từng lệnh trên mệnh đề của nó (`command_scope`); span sai không làm mất lệnh; mệnh đề sau chỉ yêu cầu mà không nêu dịch vụ khác ("…hỏng, cho người lên xem") thuộc cùng phạm vi.
- Đồng thuận model–embedding hiệu chỉnh trên train (giữ riêng cả nhóm, `tools/nlu/calibrate_semantic_support.py`): top-2, ≥ 0.65, cách đầu ≤ 0.03, hơn lượt không-yêu-cầu gần nhất − 0.02. Recall 0.876 → 0.899; nhận nhầm lượt không-yêu-cầu 0.482 → 0.127.
- Phủ định chỉ từ chối khi chi phối động từ yêu cầu (`negation_scope`); mốc quá khứ, lời thuật lại và câu hỏi thông tin không cung cấp execution evidence.
- Tiếng Hàn: đuôi nối "-고" tách mệnh đề (`clause_suffixes`), "주시" là động từ yêu cầu.
- Câu hỏi nhiều vế không đi đường đọc tất định; model tách AskInfo/Navigate.
- Chính sách: chìa tổng / vượt quyền vào phòng trả lời theo chính sách, không mở draft (`room_access_conflicts.override_terms`); bỏ qua xác nhận không được duyệt chỉ nhờ đồng thuận.
- Khẩn cấp: "cháy" gắn với bóng đèn/cầu chì vào vùng hỏi lại thay vì báo khẩn cấp đầy đủ (`emergency_context_patterns.vi.fault_sense`); khách trả lời không phải khẩn cấp thì lượt gốc được hiểu lại.
- Khởi động: `/readyz` 503 và `understanding_ready=false` tới khi warm-up xong; kiosk chờ trước khi nhận lượt.
- Không hiểu: phản hồi kèm `service_options` (3 dịch vụ gần nhất theo embedding, kèm slot server đọc được); khách chạm để mở form và vẫn xác nhận. Vẫn hoạt động khi model không chạy.

#### Đợt 2 (cùng ngày)

- Giữ riêng `intent_router_polite_and_social` (48, khóa trước khi sửa): 30/48 → 32/48 (vi 17→18, en 3→3, zh 5→6, ko 5→5). Sửa: tiểu từ hỏi trong khung yêu cầu ("…주실 수 있나요?"), đồ vật mới qua mạo từ/số đếm đứng riêng, vị trí đồ vật theo ngôn ngữ, "đồng ý" khi không có gì chờ thành lời xã giao, câu hỏi chỗ trống không bị áp lề "không yêu cầu".
- `vi_hard_negatives`: 80/80, 0 write (p50 10.9 s, p95 14.6 s).
- Giữ riêng `intent_router_conversational` chạy lại: 48/60 — **không độc lập** (đã xem lỗi của bộ này).
- Đối chứng: cùng pipeline, model sinh lệnh lớn qua API (chỉ thuộc lượt đo, không thuộc runtime hiện hành) có 38/48 khớp nhãn và 0 lỗi được quy cho model trong mẫu này; 7/10 lỗi còn lại là lệnh khớp nhãn không được cổng chấp nhận.

#### Cổng hai mức (chưa đo trên bộ lớn)

Lệnh không có bằng chứng kiểm chứng nhưng hợp lý (đang yêu cầu; gần dịch vụ hơn lượt không-yêu-cầu gần nhất ≥ 0.02, điểm ≥ 0.60) được giữ với cờ `review`: phản hồi `needs_review` + `service_options`, khách vẫn xác nhận. Hiệu chỉnh trên train (giữ riêng nhóm): nhận nhầm lượt không-yêu-cầu 0.057; recall kiểm chứng-hoặc-hợp-lý 0.916. Embedding dịch vụ chỉ dùng câu đầu của mô tả (phần "X là dịch vụ khác" làm lệch vector); hiệu chỉnh lại cho cùng ngưỡng. Kiểm tra bằng model thật: 2 câu (đúng). Bộ giữ riêng `intent_router_review_tier` (48, sha256 `260c0f8c…`) đã khóa, **chưa chạy**.

Chạy trên máy thuê (không chạy trên laptop):

```powershell
python tools/evaluation/evaluate_intent_router.py --stage after --holdout datasets/evaluation/holdout/intent_router_review_tier.jsonl --output reports/intent-router-review-tier.json --cases reports/intent-router-review-tier-cases.jsonl
```

Offline: các file test liên quan qua; 4 lỗi cũ còn nguyên. Khi chạy chung một lượt lớn, 1 test API phụ thuộc thời gian (`test_public_status`: timestamp chứa "305") và vài test `tests/data` từng fail nhưng qua khi chạy riêng từng file — cần chạy lại toàn bộ trên máy thuê để kết luận.

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

## Kiểm toán nguyên nhân intent router (2026-10-10, baseline ccbca23)

Bằng chứng lịch sử của đợt P1–P7; các vị trí source và nhận xét chưa commit thuộc snapshot lúc đo, không mô tả trạng thái checkout hiện tại. Không dùng đợt này để thay thế các lượt đo sau đó ở phần trên. Kiến trúc hiện hành nằm trong [Architecture](ARCHITECTURE.md).

Báo cáo so sánh với **working tree lúc bắt đầu**, trên branch `phase4-handoff`, HEAD `ccbca237f8c302aee1787301ca622a306185b153`. Không so với cây sạch tại HEAD vì có thay đổi của người dùng chưa commit. Snapshot byte gốc ở `.cache/intent-router/workspace.zip`; manifest ở `.cache/intent-router/workspace.json`. Không reset, checkout, commit, push hoặc tải model.

Số liệu máy đọc được: [báo cáo](../reports/intent-router.json), [nhật ký API và raw model](../reports/intent-router-cases.jsonl). Công cụ tái lập: [evaluate_intent_router.py](../tools/evaluation/evaluate_intent_router.py). Nhật ký giữ cả lượt đo bị dừng; chỉ hai stage có `complete=true` được dùng để so sánh.

### Phạm vi và cách chấm

- Model thật `qwen2.5:7b`, embedding thật `bge-m3`, `num_gpu=0`, ngân sách NLU 30 giây. Digest model, fingerprint source/config/training và hash DB có trong JSON.
- Một bộ 80 `vi_hard_negatives` và một bộ giữ riêng 25 câu: vi 16, en 3, zh 3, ko 3. Bộ giữ riêng được viết và khóa **trước khi sửa runtime**; SHA-256 `e53c5315502d9557eeaf578fff581a5c73efcb660deda401695f5f209cbdc5ac`. Không sửa câu hoặc nhãn sau khi xem kết quả; không dùng lỗi của bộ này để chỉnh runtime/training.
- Đúng ở bộ giữ riêng nghĩa là đúng multiset `StartGoal` và số lượt đọc mong đợi, không phát sinh `Handoff`, `Cancel`, `Modify`. Đây **chưa phải** kiểm chứng tất cả phòng, món, đơn vị, số lượng và giờ ở đầu ra workflow. Nhãn do agent viết, `unreviewed`; không phải nhãn người duyệt độc lập.
- Câu bẫy dùng nguyên oracle đã có ở `tools/evaluation/run_human_review_probe.py:oracle`. `non_action` bổ sung kiểm tra biên không tạo route hành động; không đổi nhãn dữ liệu. Không coi `UNAVAILABLE` là `UNSUPPORTED`.
- Scorer ban đầu so nhãn trực tiếp với `tool_route`, bỏ mất `evidence_status`, cho số 2/80 sai. Đã tính lại **summary** từ transcript gốc bằng oracle hiện hữu: 64/80. Giữ nguyên journal gốc và lưu `oracle_rescored.rescored_cases`, không viết lại kết quả để che lỗi scorer.
- Gọi API/session/CSRF, emergency, router, model, gate, LangGraph và workflow thật, với SQLite copy. Không gửi confirm. Đếm `service_requests` bằng SQL trước/sau từng lượt. Planner và semantic answer generation tắt để đo boundary NLU; không phải nghiệm thu toàn bộ RAG, browser hay voice.
- `p50/p95` là độ trễ API theo nearest rank; có retrieval và workflow. Ký tự là độ dài raw text được `_chat` trả về, gồm giá trị 0 khi không trả text; không phải tổng token/byte Ollama đã sinh trước khi hủy stream. `mean_output_characters` tính trên model calls, không tính fast path không gọi model.
- Stage sau warm prefix ngoài phần chấm và ghi riêng `scoring_readiness`. Stage trước chưa ghi trạng thái residency tại lúc bắt đầu; không suy diễn một A/B latency khởi động hoàn toàn đồng điều kiện từ các percentile này. Khởi động có thí nghiệm riêng bên dưới.
- Proposal trước được quan sát ở projection API và lệnh hiểu ý định. Stage sau kiểm thêm bảng `proposals` và `review_state`; phương pháp quan sát stage sau chặt hơn.

### Số đo trước/sau với model thật

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

### P1 — thiếu demonstration cho composition

**Đối chiếu source:** bản gốc `service_selector.py:595,646` chọn nearest examples theo cả câu, đồng thời có điều kiện mọi goal thuộc shortlist `offered`. `goal_ranking:570` gán cả ví dụ nhiều lệnh cho goal đầu tiên. Đếm corpus bằng loader thật: VI 12/1008 có nhiều `StartGoal`; en/zh/ko mỗi ngôn ngữ 3. Tần suất few-shot thực tế của lượt baseline được ghi trong JSON, không thay thế bằng số 0/34 từ đợt đo cũ của người dùng.

**Trạng thái tại lượt đo:** [service_selector.py](../src/concierge_kiosk/agent/understanding/service_selector.py#L59) lập index cho từng source training đã validate, không gán cả turn cho goal đầu; [selection](../src/concierge_kiosk/agent/understanding/service_selector.py#L444) dùng registry đầy đủ cho eligibility, dành một vị trí cho composed example, ưu tiên cùng ngôn ngữ. Tập [training](../datasets/training/agent/compositional_requests.jsonl) có 27 câu, vi 12/en 5/zh 5/ko 5, nguồn `assistant_authored_compositional_training`, `CANDIDATE`, `unreviewed`. Trong corpus của stage sau: nhiều `StartGoal` VI 22/1020, en 7/335, zh 7/330, ko 7/329. Không đọc evaluation trong loader runtime.

**Phạm vi compatibility:** production `engine.command_for_session` không còn dựng/passing `request_parts` hoặc ranking trên các mảnh trước model. Helper `_request_parts`, tham số compatibility và contract test cũ vẫn giữ; API compatibility vẫn thuộc phạm vi được giữ.

**Kiểm chứng:** test eligibility ngoài top-k, tách goal ranking của compound training và cache reload đều pass. Accuracy, percentile, mean ký tự, writes và số few-shot composition thật: bảng chung phía trên.

### P2 — protocol dài, sinh lại slot server sở hữu

**Đối chiếu source:** bản gốc `commands.py:531,568` dạy model trả `slots:[{name,text}]`; mô tả và ví dụ lặp room/count/unit/time. Output này cần server đọc lại. Prompt/output nhiều nhánh tăng công việc sinh JSON trên CPU. Số đo 17 lượt và hồi quy tuyến tính trong yêu cầu chưa được tái lập như một thí nghiệm riêng.

**Trạng thái tại lượt đo:** [wire và parser](../src/concierge_kiosk/agent/understanding/commands.py#L403) dùng `{type,goal,text}` với `item` tùy chọn; không dạy sinh `slots` array. Source là span nguyên văn, được server mở rộng tới scope đầy đủ, validate rồi mới làm evidence. Slot số/phòng/đơn vị/giờ/ngày do server trích trên source; room có thể lấy từ cả turn. [Few-shot](../src/concierge_kiosk/agent/understanding/commands.py#L448) chuyển sang wire phẳng. Canonical command và parser legacy giữ để duy trì contract của callers hiện hữu.

**Phạm vi compatibility:** không còn lặp các slot này trong output instruction/example mới. **Còn giữ** `num_predict=384`, retry với `repeat_penalty=1.1`, hoặc guard 16 whitespace token: chưa có bằng chứng chúng thừa; transport recovery nằm trong contract test hiện hữu. Shallow JSON vẫn có outer `commands` array, không phải grammar một tầng. Các lượt đo chưa bao phủ mọi grammar loop của model 7B.

Ollama hỗ trợ JSON mode và JSON schema qua `format`; runtime giữ JSON mode và validation server, không đổi model hoặc bypass gate. [Tài liệu Ollama](https://docs.ollama.com/capabilities/structured-outputs)

**Kiểm chứng:** wire không sinh slot, source không nguyên văn/quoted/trim negation bị loại, thời gian từng request được giữ riêng trong governed state. Ký tự và p50/p95 thật ở bảng chung; không suy diễn output luôn ngắn hơn khi số intent được nhận tăng.

### P3 — service identity phụ thuộc inventory và default

**Đối chiếu source:** bản gốc `intent_evidence.py:244,396` dùng concept/action ontology hoặc top embedding toàn turn; selector chưa index action criteria và gán compound training về goal đầu. `config/agent-domain.json:83,289` mô tả amenity/facility bằng danh sách ví dụ và điều phối department.

**Trạng thái tại lượt đo:** 14 description mô tả công việc được thực hiện và cách phân biệt loose supply, setup, repair, cleaning, prepared order, reservation. [Config](../config/agent-domain.json#L83) không thêm tên món hay động từ. [Selector](../src/concierge_kiosk/agent/understanding/service_selector.py#L414) rank cả criteria và từng span training; [semantic gate](../src/concierge_kiosk/agent/understanding/intent_evidence.py#L481) dùng ranking của **source lệnh đã validate**, giữ nguyên minimum và modality guards. Cache key bao gồm criteria, training commands/spans và manifest embedding. Cache text chỉ giữ index texts, không lưu câu evaluation/khách vào corpus training.

**Bằng chứng development:** trên 6 câu training có model thật, protocol sinh đủ các lệnh; trước khi lập source index, gate loại 1 request VI và 1 KO ở mẫu này; sau source index 6/6 command stream được chấp nhận. Đây là training-fed development, **không phải** independent accuracy. Parser nhận particle có trong grammar sau measure word, không thêm từ KO/VI vào config. Test generic grammar và multilingual slot/numeral pass.

**Phạm vi compatibility:** không thêm inventory keyword cho từng món mới. Ontology direct evidence vẫn tồn tại để phục vụ deterministic paths và contract cũ; không tuyên bố mọi noun/verb ngoài vocabulary đều được hiểu. `default_for_kind` vẫn là business routing default, không dùng làm tiêu chí chọn service trong prompt mới.

**Số đo độc lập:** bảng chung phía trên. Chưa đo một taxonomy đồ vật chưa thấy có đủ nhãn và chưa làm ranking ablation cho cặp amenity/facility.

### P4 — hai hợp đồng so khớp dấu

**Đối chiếu source:** bản gốc `intent_evidence.py:61,126` có `spans` bỏ dấu và `marker_spans` có dấu; caller tự chọn nên authority thay đổi theo vị trí gọi. Slices của string folded mất thông tin surface.

**Trạng thái tại lượt đo:** [_EvidenceText và spans](../src/concierge_kiosk/agent/understanding/evidence_text.py#L11) giữ surface NFKC/casefold và map offset kể cả Hangul decomposition; slices/trim/concatenation giữ surface. `spans` mặc định phân biệt dấu được gõ; `marker_spans` delegate về cùng implementation. Input không có dấu giữ policy tương thích hiện hữu.

**Phạm vi compatibility:** không còn hai implementation chọn semantics dấu khác nhau ở các call sites này. API `marker_spans` còn để tương thích, không thêm rule cho từng cặp từ.

**Kiểm chứng:** các cặp có dấu khác nghĩa, NFC/NFD và input không dấu được test bằng invariants riêng. Real-model gate/route số đúng, percentile, ký tự, writes: bảng chung; chưa có bộ minimal pairs độc lập đủ lớn cho cả bốn ngôn ngữ.

### P5 — chia punctuation thành task

**Đối chiếu source:** bản gốc `intent_evidence.py:91,587` và `grounded_service.py:206` split dấu câu/connector trực tiếp; `state.py:364,451` phải gộp subset/superset item sau khi model đã chia vụn. Concept noun đơn lẻ chưa đủ bằng chứng vị ngữ.

**Trạng thái tại lượt đo:** [predicate_ranges](../src/concierge_kiosk/agent/understanding/request_scope.py#L51) dùng một scope algorithm: strong sentence boundaries, chỉ cắt coordination khi hai bên có predicative evidence, giữ complement/list và các facet thông tin chung. Service noun đứng trong object/list không tự tạo predicate. [command_source (nay command_scope)](../src/concierge_kiosk/agent/understanding/intent_evidence.py#L54) mở rộng span model để không bỏ negation/report/condition. Grounded path và gate cùng dùng scope; state đọc slots từ source lệnh thay vì đoán source từ goal.

**Phạm vi compatibility:** xóa `_item_lines` và item subset/superset merge. Chỉ còn dedup command trùng service, cùng source và cùng non-item slots. Cancel/Modify thiếu owned draft/ticket vẫn bị chặn: đây là business ownership guard, chưa có bằng chứng có thể bỏ.

**Kiểm chứng:** list/complement/đọc nhiều facet, một execution kèm một câu hỏi, tên service nằm trong compound object, độc lập hai giờ và không trim negation. Có một regression test runtime fail trong development; kết quả sau điều chỉnh structural scope là pass; test cũ giữ nguyên. Một lượt sau bị dừng; journal đã ghi 42 câu bẫy vì source audit phát hiện noun-only predicate; chưa chạy câu giữ riêng, journal giữ nguyên và lượt này bị loại khỏi so sánh.

**Số đo thật:** bảng chung. Không tuyên bố đây là full syntactic parser; chưa kiểm chứng mọi dạng ellipsis/coordination.

### P6 — startup chiếm permit của khách

**Đối chiếu source:** bản gốc `local_ai.py:207` lấy unowned permit warm-up; `admission.py:63` và `engine.py:359` chỉ có immediate admission. Guest bị từ chối dù warm-up sẽ release permit.

**Trạng thái tại lượt đo:** [enter_guest_slm](../src/concierge_kiosk/runtime/admission.py#L84) đợi `Condition` chỉ khi chủ permit là startup, wake khi release, giữ một SLM job và deadline trong ngân sách lượt. [Engine](../src/concierge_kiosk/application/conversation/engine.py#L196) dùng bounded wait; deadline hết trả `turn_budget_expired`. Không đưa guest khác/STT vào hàng đợi này, không release permit native đang chạy.

**Phạm vi compatibility:** bỏ immediate `try_enter_slm` của luồng NLU production khi admission hỗ trợ startup wait; còn fallback cho adapter legacy. Không nới budget hoặc cho inference cạnh tranh.

**Đo thật, n=1 mỗi arm:** dùng class admission gốc từ snapshot và class hiện tại, cùng prefix sau sửa 6778 ký tự, stop Qwen trước mỗi arm, CPU. Warm trước/sau 46,844/44,938 giây; guest trước bị từ chối ngay 0,000 giây; sau đợi 30,016 giây rồi **hết hạn**, chưa được admit. Immediate busy: 1/1 → 0/1. Guest admitted: 0/1 → 0/1. Với n=1, p50=p95 bằng thời gian admission tương ứng. Không sinh command guest, nên ký tự output guest=0; không mở business store, không đo service writes bằng SQL trong thí nghiệm này. Đây là admission experiment, không phải API/customer success experiment.

**Kết luận P6: NO-GO cho guest đến ngay lúc cold startup.** Admission có cơ chế chờ; chưa có kết quả xử lý lượt ấy trong 30 giây; giới hạn warm-up lạnh còn hiện hữu.

### P7 — bằng chứng đánh giá và nhãn hẹp

**Đối chiếu source:** `docs/TESTING.md:97,98` báo 79/80 và 11/11 từ đợt trước; bộ 11 câu cũ được dùng để development rồi được báo như accuracy. Đợt đó không có independent multi-language composition holdout trong phạm vi bằng chứng của task này. Oracle ở `tools/evaluation/run_human_review_probe.py:21` phân biệt route và `evidence_status`; scorer mới ban đầu đã bỏ mất phần thứ hai và có implementation đối chiếu tại `tools/evaluation/evaluate_intent_router.py:37,97`.

**Trạng thái tại lượt đo:** khóa corpus trước edits; lưu toàn bộ raw model/API/SQL write observations; hash source/config/training/DB tại đầu và cuối stage; không xem chi tiết lỗi giữ riêng để tune; không đổi nhãn HARD-VI-018 hoặc oracle để coi `multi_task` là `knowledge_abstain`. Mỗi completed stage 105 cases, có breakdown ngôn ngữ. Source, câu và nhãn giữ riêng không nằm trong runtime/training. Stage bị ngắt được lưu riêng, không trộn số với stage hoàn chỉnh.

**Phạm vi compatibility:** không dùng 11/11 training/development làm kết luận độc lập. Scorer đối chiếu transcript với expected labels giữ nguyên. Chưa có người độc lập duyệt tập mới; cỡ mẫu en/zh/ko mỗi 3 chỉ là smoke coverage.

**Số đo:** bảng chung. HARD-VI-018: trước route `multi_task`, sau `knowledge`; giữ nguyên nhãn `knowledge_abstain`. Chỉ đối chiếu với oracle, chưa có human adjudication để kết luận nhãn quá hẹp.

### Test và kiểm tra có kết quả

Mọi pytest đều qua `python tools/runtime/run_offline_tests.py --timeout 600 -- <files>`, không chạy full suite; lọc `-k "not voice"` ở cohort chứa test voice. Không sửa/xóa test sẵn có, không tăng keyword budget.

1. Baseline 11 file: `test_commands`, `test_command_cpu_contract`, `test_command_semantics`, `test_grounded_service`, `test_semantic_authorization`, `test_selector_cold_index`, `test_slm_readiness`, `test_item_fidelity`, `test_understanding_layers`, `test_no_hardcode`, `test_no_case_specific_rules`.

   ```text
   327 passed, 2 warnings in 19.61s
   ```

2. Development đầu: 8 file liên quan; có ba fail liên quan structural scope; test giữ nguyên, lượt kiểm tra tiếp theo có kết quả bên dưới. Argv từng file của lượt sớm này không được lưu riêng; chỉ giữ summary đã trong output lượt chạy, không suy đoán thêm tên file.

   ```text
   3 failed, 281 passed, 2 warnings in 6.65s
   ```

3. Cohort baseline cộng test contract mới sau lần sửa đó:

   ```text
   341 passed, 2 warnings in 21.16s
   ```

4. Runtime development: `test_intent_router_contract`, `test_agent_runtime`, `test_e2e_guest_journeys`, `test_change_confirmation`, `test_command_agent_loop`, `test_semantic_authorization`, `test_domain_profile`, không voice. Lượt này có fail ở execution + question; kết quả sau điều chỉnh scope algorithm nằm bên dưới.

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

   Ba fail này nằm đúng danh sách lỗi có sẵn do người dùng nêu. Test voice fail có sẵn không chạy. Resource guard: 3 socket attempts không được cho phép, 0 kết nối thật, 0 heavy model loads.

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

    `bandit -q -r src/concierge_kiosk tools -ll` trả exit 1: còn 1 Medium B608 ở `tools/runtime/replay_guest_transcript.py:149`, file không bị sửa trong task. Harness dùng loopback inventory API helper ở vị trí có B310; không có ignore/allowlist bổ sung.

    Bandit trên tám file runtime sửa trong task và harness mới, với `-ll`:

    ```text
    bandit_changed exit_code=0
    ```

Các lần kiểm tra không thu được pytest verdict: một lần bị resource guard từ chối do RAM <1 GiB khi model đang resident; một lần bootstrap config description >240 ký tự trước khi pytest collection (description trong giới hạn schema); một lần chỉ định nhầm `tests/agent/test_service_slots.py` không tồn tại, exit 4 (kết quả các file hiện hữu ở phần trên). Không gọi chúng là test pass. Các warnings hiện hữu về pytest/anyio/httpx có trong raw output.

### Những việc chưa kiểm chứng và GO/NO-GO

<!-- ROUTER_DECISION -->
**Kết luận tại snapshot của đợt P1–P7: NO-GO cho release; phạm vi P1–P7 chưa hoàn tất.** Đợt đó không có commit/push hoặc release, chưa đạt yêu cầu KO không hồi quy. Kết luận này không phải một lượt đo mới trên HEAD hiện tại; các lượt đo sau được ghi riêng ở phần trên.

| Ngôn ngữ | Quyết định | Bằng chứng và giới hạn |
|---|---|---|
| vi | NO-GO | Giữ riêng 9/16, còn một goal/proposal sai; câu bẫy 0 write/0 proposal sai nhưng đúng nhãn 65/80. |
| en | NO-GO | 1/3 đúng; không thêm wrong goal/proposal ở mẫu này, chưa đủ coverage. |
| zh | NO-GO cho release | 3/3 trong smoke giữ riêng, baseline 1/3; chỉ n=3, chưa kiểm toàn bộ slot và cold startup vẫn fail. |
| ko | NO-GO, có hồi quy | Đúng giảm 2/3 → 1/3; p50 tăng 15,588 → 24,378 giây. Không chỉnh theo các lỗi này để biến holdout thành development. |

Chưa làm/đo: independent human review; full slot fidelity trên real-model holdout; đủ mẫu multilingual/hard-negative ngoài VI; ablation P1–P5; loại hẳn closed ontology; proof để xem xét compatibility của transport guards; successful cold-start guest API trong 30 giây; full suite/voice/browser; cloud tracing; deployment. Ba lỗi test có sẵn vẫn còn. Các thay đổi chưa được nghiệm thu để đưa vào production.

Chi tiết lỗi giữ riêng trong `diagnostics` chỉ dùng để báo cáo sau khi hoàn thành stage; runtime/training không chỉnh tiếp sau khi xem. Cần một bộ đánh giá độc lập mới được khóa trước vòng phát triển tiếp theo nếu muốn ước lượng tổng quát hóa sau các sửa tiếp theo.
<!-- ROUTER_DECISION_END -->

CI/release gates có định nghĩa trong .github/workflows/ci.yml và tools/validate; không sửa hoặc tự chạy full CI khi chỉ sửa docs. Các blockers ở [Limitations](LIMITATIONS.md), test cases ở [backend cases](backend-test-cases.md).

## Dọn code cơ học trên HEAD a8bb47c

[Báo cáo kiểm chứng](../reports/code-cleanup.json) giữ mapping symbol trước/sau,
số dòng, lệnh test cùng output nguyên văn, đối chứng API model thật và kiểm tra
hash các phạm vi được bảo vệ. Prompt, training examples và selector cache key
không đổi; đối chứng dịch vụ/thông tin giữ nguyên route, proposal và các field
ổn định được so sánh. Đây là kiểm tra tương đương có giới hạn, không phải đo lại
accuracy hay nghiệm thu các lỗi intent router còn tồn tại.

Đây là kết quả ghi nhận trên baseline `a8bb47c` và working tree của đợt dọn
code, không phải chạy lại test trong đợt cập nhật tài liệu. Các nhóm overlap,
không cộng thành số test duy nhất.

| Nhóm | Kết quả nguyên văn |
|---|---|
| Baseline: các file bắt buộc | `1 failed, 412 passed, 2 warnings in 73.86s (0:01:13)` |
| Sau tách: các file bắt buộc | `413 passed, 2 warnings in 70.06s (0:01:10)` |
| Test bổ sung theo import module | `4 failed, 534 passed, 4 skipped, 2 xfailed, 2 warnings in 91.08s (0:01:31)` |
| Tái lập SetSlot trên bản sao riêng của HEAD gốc | `1 failed, 2 warnings in 1.28s` |
| Kiểm tra import/re-export và numeral cuối đợt | `1 failed, 84 passed, 2 warnings in 7.74s` |

Baseline `test_no_hardcode` báo violation cho ví dụ tiếng Việt trong docstring của
`numerals._half_follows`; docstring mô tả tổng quát; thân hàm,
allowlist và assertions không đổi. Những fail còn lại:

- `test_model_command_reenters_governed_service_flow` và
  `test_unmatched_schedule_activity_abstains_without_rag_fallback` trong
  [test_autonomous_concierge.py](../tests/agent/test_autonomous_concierge.py).
- `test_where_question_reads_knowledge_once_per_turn` trong
  [test_multilingual_conversation_e2e.py](../tests/agent/test_multilingual_conversation_e2e.py).
- `test_understand_turn_layer_b_handles_set_slot` trong
  [test_understanding_layers.py](../tests/agent/test_understanding_layers.py):
  context có thêm `details`; cùng lỗi trên bản sao HEAD gốc. Giữ nguyên hành vi.

Đối chứng Qwen CPU qua API dùng hai câu cố định, mỗi câu chạy trước và sau,
SQLite copy, không gửi confirm. Route `service`/`knowledge`, proposal, slots,
thông điệp và các field ổn định được đối chiếu đều khớp; 0 write trước xác nhận.
Sau lượt đo, không có model resident trong kết quả `ollama ps`. Không coi mẫu này là đánh giá multilingual hay slot
fidelity tổng quát.

Compile, config pins, data audit, diff check và Bandit trên các file Python
thay đổi có exit code bằng không. Scan toàn repo còn B608 tại
`tools/runtime/replay_guest_transcript.py:149`, file không sửa trong đợt này.
Lệnh đầy đủ, raw outputs và hash checks nằm trong báo cáo máy đọc được ở trên;
`reports/` là artifact local, không được giả định có sẵn trên checkout mới.

Không sửa assertion. Voice/full suite/bộ đánh giá lớn không chạy; lỗi voice
`test_voice_service_readback_uses_catalog_name_and_structured_slots` không được
đo lại. Các ranh giới runtime và trách nhiệm module nằm trong
[Architecture](ARCHITECTURE.md).
