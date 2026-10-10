# API và authentication

Nguồn contract: `api/shared/contracts.py`; routes ở `api/{public,guest,staff,internal,voice}/`, `main.py` và voice transport. OpenAPI được bật ngoài production; không import app để đọc schema trên máy thật vì có startup/storage/model side effects.

## Authentication

| Client | Cách gọi |
|---|---|
| Guest | Tạo session, giữ cookie ck_session và gửi X-CSRF-Token |
| Staff | Authorization: Bearer với read/write scopes; production thêm staff gateway ingress |
| Internal agent | X-Agent-Token |
| Voice WebSocket | Same-origin, session và token theo transport contract |

POST public dùng đúng `Origin` của `CONCIERGE_PUBLIC_ORIGIN`; cookie/CSRF vẫn bắt buộc cho guest endpoints đã bảo vệ. Không đặt raw session_id vào request để thay authorization. Staff bearer không bypass gateway production.

`X-Request-ID`/`X-Trace-ID` trong response do server tạo cho HTTP correlation; không tin trace ID client hoặc mặc định đồng nhất với SDK trace ID.

## Luồng guest cơ bản

1. `POST /api/session`: trả session_id, csrf_token, expires_in và session cookie.
2. `POST /api/ask`: query (2–500 ký tự), language, source và turn_nonce nếu dùng.
3. Response có answer/sources/citations, grounding/retrieval/generation metadata và action/voice fields theo contract. Dùng machine-readable state/receipt, không suy ra hoàn thành từ answer.
4. Prepare → explicit confirm theo policy; kết thúc bằng `POST /api/session/end`.

Ví dụ body hỏi, dùng dữ liệu synthetic khi thử:

```json
{"query":"What time does the spa open?","language":"en","source":"dialogue","turn_nonce":"synthetic_turn_01"}
```

Prepare yêu cầu kind/language/details/nonce; service, payload, change và data_consent theo contract. Confirm:

```json
{"proposal_id":"<32-character-owned-proposal-id>","confirmed":true,"price_acknowledged":false}
```

Giá trị price_acknowledged/verification phải phản ánh consent thật, không tự đặt true để bypass policy. Proposal ID lấy từ response, không fabricate. Pending/approved/in_progress/paused thường trả 202; service completed dựa trên business row. Confirm replay không tạo duplicate request.

## Endpoint catalog

### Public, health và client telemetry

| Method | Path |
|---|---|
| POST | `/api/telemetry` |
| GET | `/` |
| GET | `/staff` |
| GET | `/healthz` |
| GET | `/readyz` |
| GET | `/api/config` |
| GET | `/api/ui-contract` |
| GET | `/api/services` |
| GET | `/api/map/places` |

`/readyz` trả 503 "Understanding layer is warming up" cho tới khi warm-up lúc khởi động (prefix lệnh của model, index selector, emergency classifier) kết thúc; `/api/config.understanding_ready` báo cùng trạng thái để kiosk chờ trước khi nhận lượt khách.

### Guest session, requests và status

| Method | Path |
|---|---|
| GET | `/status/{token}` |
| GET | `/api/status/{token}` |
| GET | `/api/status/{token}/events` |
| GET | `/api/status/{token}/qr.svg` |
| GET | `/api/status/lookup/{confirmation_code}` |
| POST | `/api/session` |
| POST | `/api/session/end` |
| POST | `/api/ask` |
| GET | `/api/turns/{turn_id}/events` |
| GET | `/api/proactive/suggestions` |
| POST | `/api/requests/prepare` |
| POST | `/api/consent` |
| POST | `/api/requests/confirm` |
| POST | `/api/requests/cancel` |
| POST | `/api/requests/{request_id}/change` |
| GET | `/api/requests/mine` |
| GET | `/api/requests/{request_id}/status` |
| GET | `/api/requests/{request_id}/progress` |
| POST | `/api/requests/{request_id}/feedback` |

Khi một lượt không được hiểu (`nlu_failure`/`clarification`), phản hồi `/api/ask` có thể kèm `service_options`: tối đa 3 `{kind, service, label, details, payload}` là các dịch vụ gần nhất với lời khách theo embedding, `payload` là slot server đọc được (phòng, số lượng, giờ, ngày). Chọn một option chỉ mở form yêu cầu; khách vẫn xem lại và xác nhận, option không mang quyền ghi.

### Staff

| Method | Path |
|---|---|
| GET | `/staff/emergencies` |
| POST | `/staff/emergencies/{alert_id}/transition` |
| GET | `/staff/requests` |
| GET | `/staff/requests/page` |
| GET | `/staff/queue/summary` |
| GET | `/staff/requests/{request_id}` |
| GET | `/staff/requests/{request_id}/audit` |
| GET | `/staff/metrics` |
| POST | `/staff/requests/{request_id}/guest-change` |
| POST | `/staff/requests/{request_id}/transition` |
| POST | `/staff/requests/{request_id}/orchestration/reconcile` |
| POST | `/staff/orchestration/reconcile-deferred` |

### Internal agent

| Method | Path |
|---|---|
| POST | `/internal/agent/session` |
| POST | `/internal/agent/ask` |
| POST | `/internal/agent/prepare` |
| POST | `/internal/agent/confirm` |

### Voice

| Method | Path |
|---|---|
| POST | `/api/audio/turn/start` |
| POST | `/api/audio/greeting` |
| POST | `/api/audio/turn/cancel` |
| POST | `/api/audio/transcribe/partial` |
| POST | `/api/audio/transcribe` |
| POST | `/api/audio/proof` |
| POST | `/api/audio/played` |
| POST | `/api/audio/playback-failed` |
| POST | `/api/audio/speak` |
| GET | `/api/audio/turn/proof` |
| POST | `/api/audio/turn/played` |
| POST | `/api/audio/turn/playback-failed` |
| WS | `/api/audio/stream` |
| WS | `/api/voice/agent` |

`/api/telemetry` dùng guest session/CSRF; `/staff` UI trong production cần staff gateway. Catalog theo nơi đăng ký route, không hàm ý mọi endpoint trong nhóm đầu đều anonymous.

Voice endpoints đăng ký/sử dụng theo profile và assets; `/api/voice/agent` là Pipecat transport. Status link/token là capability riêng có expiry; không công khai token hoặc suy ra quyền session khác từ nó.

## Failure và ownership

Schema/payload sai trả validation errors; origin/ownership/auth/scope không hợp lệ bị từ chối. Expired proposal, missing consent, price/guest verification và invalid transition có state-specific errors. API không dùng 500 để diễn đạt expected business rejection.

NLU unavailable/timeout/semantic rejection có thể trả response hội thoại an toàn với failure metadata; HTTP 200 không nghĩa task thành công. So sánh route/action, clarification và DB receipts khi nghiệm thu.

Chi tiết safety ở [Security](SECURITY.md); các tình huống độc lập ở [backend cases](backend-test-cases.md). Catalog được đối chiếu tĩnh với source, không phải bằng chứng live endpoint acceptance.
