# WP14.1 — Langfuse acceptance

Audit ngày 2026-10-09 trên branch `phase4-handoff`, HEAD
`aa0c4d8c937899133562fcda63244f317c45b09e`, đúng WP14 baseline được chỉ định.
Working tree ban đầu chỉ có `docs/AGENT-CORRECTNESS-AUDIT.md` untracked, được giữ nguyên.

## Status

| Acceptance boundary | Status | Evidence |
|---|---|---|
| Offline FastAPI/custom transport/LangGraph/SDK integration | **OFFLINE_INTEGRATION_PASS** | SDK 4.17.0 thật, in-memory exporter, mocked Ollama stream |
| Cloud receipt / API read-back / dashboard hierarchy | **CLOUD_INGESTION_NOT_VERIFIED** | Không có Langfuse configuration trong process environment; chưa có explicit permission xuất synthetic data |
| Tested privacy boundaries | PASS offline; không phát hiện PRIVACY_FAILURE | Export-stage masking, scope/resource stripping, PII assertions, synthetic error tests |
| Integration failures | Không còn INTEGRATION_FAILURE trong targeted tests | Status enum và SDK float metadata loss đã sửa, có export-level regressions |

Không tạo project/account, không auth probe, không kết nối endpoint Cloud.
HTTP exporter success và Cloud/dashboard correctness đều **NOT_VERIFIED**, không gộp
mock/in-memory PASS thành `CLOUD_INGESTION_PASS`. Không dùng credentials thật trong tests/report.

## Test conditions và actual integration audit

Python 3.12.3, optional `langfuse==4.17.0`, compatible dependencies trong isolated
`reports/wp14-sdk`. SDK không import trong disabled mode. Existing tests chặn exporter
constructor khi disabled/zero sample, cùng failure isolation/cancellation/concurrency.

Test mới `test_real_sdk_fastapi_wrong_and_valid_mock_nlu_with_existing_scores` dùng
temporary SQLite, pinned existing property profile để enabled services đúng contract,
authenticated `/api/session` + CSRF `/api/ask`. Không load/preload Qwen/BGE.
Shared local HTTP opener được mock; hai lượt guest gọi **hai mock HTTP transports**,
**0 real Qwen HTTP calls**. Requests/httpx real transports bị chặn.
Các test phát triển ban đầu phát hiện fixture thiếu enabled property profile;
đã cấu hình profile có pin, không bypass semantic gate hoặc thay business contract.

| Requirement | Audit result |
|---|---|
| Disabled/no credentials | No SDK exporter/socket; existing guest/business regressions vẫn chạy |
| Enabled/configured | Real SDK observations được exporter trong bộ nhớ nhận |
| One root per guest turn | Hai lượt tạo đúng hai parentless `guest_turn` roots |
| Correct parent/child | Every child parent ID nằm trong exported observations; chung trace ID của root |
| Actual command outcomes | Mock valid amenity có accepted stages; frozen WP12 wrong output có hai semantic rejections |
| WP13 reason | `unsupported_semantics`, policy WP13; không live expected_goal |
| Business writes | Prepare0, confirm1, replay0; service request ID giữ nguyên |
| Cross-request correlation | Keyed proposal link giống nhau giữa confirmation/receipt; session/nonce không export |
| Missing spans | Rejected WP12 turn không có tool_execution/prepare; pre-cancel/disabled generation không model_call |
| Failure isolation | Missing config, Full/timeout/DNS/connection/401 failures, real SDK BrokenSink không đổi guest response |
| Shutdown | Finite application wait; disabled không helper; SDK provider isolation fail closed |

## Trace hierarchy verified offline

```text
guest_turn [WP12 frozen response]
  understanding
    qwen_nlu
      model_call [generation, actual mocked provider counters]
      model_proposed [housekeeping / quiet field]
      command_validation
        server_validated
        semantic_authorization [rejected]
        semantically_authorized [unsupported_semantics]
  final_response [nlu_failure, business_writes=0, proposals=0]

guest_turn [valid amenity]
  understanding / qwen_nlu / model_call / command stages
  agent_execution
    langgraph_execution
      actual verify/plan/tool nodes
  final_response [service]

business_workflow [separate authoritative confirm call]
  confirmation
    verified_write_receipt [1, then idempotent replay0]
```

Hai root và40 actual observations được kiểm tra trong SDK evidence. SDK roots dùng actual
generated OTEL trace IDs, không giả remote parent. Cross-request
proposal-confirmation dùng HMAC links; không tự kéo dài parent context. CallbackHandler
không bật, không duplicated auto/manual LangGraph spans. Actual executed validation stages
có thể kiểm tra nhiều lần khi business code gọi gate lại; đây không phải callback duplication.
Trace IDs/span IDs/hierarchy và sanitized attributes lưu ở
`reports/wp141-sdk-offline.json`; đây là **offline evidence**, không Cloud receipt.

## Privacy evidence

Existing `test_observability.py` kiểm tra nested model output/state/memory, HTTP errors,
guest name/room/contact/payment/passport/token; SDK export-stage mask loại arbitrary attributes.
Filter reject auto-capture spans/events/links/status descriptions; wrapper loại SDK project
public key trong scope/resource. Raw input/output/prompt/graph state không được capture.

Test mới kiểm tra toàn attributes/resource/scope/events/links sau SDK export: không frozen
utterance, synthetic valid query, raw room scalar, session ID, CSRF token, secret/public key.
Đếm DB sau wrong turn: **proposals0, service_requests0, agent_session_preferences0**.
Clarification khớp existing `nlu.clarify`, không housekeeping proposal hoặc preference persistence.
SDK debug environment/error logs có privacy filter; không raw diagnostics chứa key/body.

Không raw datasets uploaded. Không real session/guest data. Langfuse không quyết định state.
Privacy PASS này chỉ bao phủ tested export boundaries; chưa xác minh Cloud storage/UI redaction
bằng server API hoặc dashboard read permission.

## Score association và exporter failures

Existing `grade_journey` chấm actual synthetic API response rồi chuyển result qua adapter
hiện có. SDK `create_score` boundary được capture bằng mock enqueue để tránh Cloud request.
Score trace ID bằng actual valid guest trace ID; names/types/units giữ nguyên từ harness.
Re-export giữ score ID/payload/persisted timestamp. Không fake production trace hoặc judge.
`queued` không đồng nghĩa server receipt; SDK server-side deduplication **NOT_VERIFIED**.
Unlinked offline results vẫn `unlinked` trong existing adapter tests.

Broken exporter/start/full queue/timeout tests không gây retry Qwen/tool/business action.
Confirm và replay vẫn một service request; telemetry chỉ quan sát authoritative return.
Lifecycle shutdown không chờ telemetry trên guest request/event loop.

## Small fix và evidence commands

Audit tìm thấy `prepare` trả `awaiting_confirmation` nhưng privacy closed status vocabulary
WP14 chưa có giá trị này. Bổ sung `awaiting_confirmation`, `confirmed`, `expired` vào
`runtime/observability.py` enum; không đổi workflow transaction/confirmation/idempotency.
Regression kiểm tra actual prepare status và confirm/replay receipt counts.

Export-level acceptance còn phát hiện SDK4 serialize float metadata thành JSON string
(ints/bools giữ native type). Mask WP14 không decode numeric fields nên mất `latency_ms`,
`prompt_eval_ms`, `generation_ms`, `total_model_ms`; native OTEL span timestamps vẫn có.
Sửa `mask_otel_spans` decode chỉ numeric/list fields trong allowlist, rồi chạy typed/bounded
sanitize như cũ. Arbitrary text/nested state vẫn drop. Regression assert every exported
observation có numeric latency và hai mocked generations giữ exact provider2/4/7ms.
Không sửa transport, inference, transaction hoặc thêm exporter. Lỗi được tìm qua real SDK
in-memory payload inspection, không tuyên bố tìm thấy qua Cloud acceptance.

| Run | Result | Evidence path |
|---|---|---|
| Existing SDK/privacy/failure tests | 34 passed | `reports/wp141-offline-tests.txt` |
| New SDK acceptance + receipt + audit + four B2 probes | 7 passed | `reports/wp15-context-probes.txt` / final adapter run |
| Combined 14 targeted modules | 326 passed, 4 existing xfailed | `reports/wp15-targeted-regressions.txt` |
| Final SDK/privacy/new acceptance + existing task/tool evaluation tests, sau numeric-mask fix |52 passed,0 failures,0 skips;9.02s | `reports/wp15-final-adapters.txt` |

Runs dùng `reports/run_wp14_targeted.py --sdk <explicit test files> -q
-W error::ResourceWarning`, test profile, disabled default và unique
`--basetemp=reports/wp14-test-tmp-<PID>`. Local logs ignored, không credentials.
Không full pytest, CI changes, commit/push, heavy benchmark hoặc model inference.

## Remaining Cloud blockers

1. Operator cần provision endpoint/project credentials và explicit authorization xuất
   dữ liệu synthetic đã lọc. Hiện không có configuration trong process environment.
2. Cần Langfuse read API hoặc dashboard permission để xác minh actual ingestion/hierarchy,
   model metadata, scores và duplicates. Exporter HTTP success riêng chưa đủ.
3. Cần kiểm tra server-side score dedupe với fixed timestamp/result identity và linked trace.

Không tuyên bố production-ready: real Cloud, current real-model accuracy, live voice/browser
E2E và production latency còn chưa nghiệm thu. Offline acceptance không cấp phép export thật.
