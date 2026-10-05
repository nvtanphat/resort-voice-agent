# API

## 1. Tổng quan

Backend dùng FastAPI. OpenAPI được bật ngoài production và bị tắt trong production configuration hiện tại.

Các endpoint dưới đây được tổng hợp từ route registration trong source. Request/response schema chi tiết nằm trong `src/concierge_kiosk/api/shared/contracts.py` và các module API tương ứng.

## 2. Public và health

| Method | Path | Mục đích |
|---|---|---|
| GET | `/` | giao diện kiosk |
| GET | `/ops` | giao diện vận hành nếu asset có sẵn |
| GET | `/healthz` | process health |
| GET | `/readyz` | runtime readiness |
| GET | `/api/config` | cấu hình public cần cho UI |
| GET | `/api/ui-contract` | UI contract |
| GET | `/api/services` | service catalog public |
| GET | `/api/map/places` | danh sách địa điểm/map data |

## 3. Guest session và conversation

| Method | Path | Mục đích |
|---|---|---|
| POST | `/api/session` | tạo guest session |
| POST | `/api/session/end` | kết thúc session |
| POST | `/api/ask` | gửi câu hỏi/turn |
| GET | `/api/turns/{turn_id}/events` | đọc lifecycle của turn |

## 4. Guest service requests

| Method | Path | Mục đích |
|---|---|---|
| POST | `/api/requests/prepare` | chuẩn bị proposal |
| POST | `/api/requests/confirm` | xác nhận proposal |
| POST | `/api/requests/cancel` | hủy request theo contract |
| POST | `/api/requests/{request_id}/change` | yêu cầu thay đổi |
| GET | `/api/requests/mine` | danh sách request của session |
| GET | `/api/requests/{request_id}/status` | trạng thái request |
| GET | `/api/requests/{request_id}/progress` | progress view |

## 5. Voice

| Method | Path | Mục đích |
|---|---|---|
| POST | `/api/audio/turn/start` | bắt đầu voice turn |
| POST | `/api/audio/turn/cancel` | hủy voice turn |
| POST | `/api/audio/transcribe/partial` | partial transcription |
| POST | `/api/audio/transcribe` | final transcription |
| WS | `/api/audio/stream` | stream audio |
| POST | `/api/audio/proof` | playback proof contract |
| POST | `/api/audio/played` | acknowledge playback |
| POST | `/api/audio/playback-failed` | báo playback lỗi |
| POST | `/api/audio/speak` | TTS |
| GET | `/api/audio/turn/proof` | legacy proof path |
| POST | `/api/audio/turn/played` | legacy playback acknowledgement |
| POST | `/api/audio/turn/playback-failed` | legacy playback failure |
| POST | `/api/audio/speak` | legacy TTS path |

## 6. Staff

| Method | Path | Mục đích |
|---|---|---|
| GET | `/staff/requests` | đọc request list |
| GET | `/staff/requests/page` | paged request list |
| GET | `/staff/queue/summary` | queue summary |
| GET | `/staff/requests/{request_id}` | request detail |
| GET | `/staff/requests/{request_id}/audit` | audit history |
| GET | `/staff/metrics` | operational metrics |
| POST | `/staff/requests/{request_id}/guest-change` | xử lý guest change |
| POST | `/staff/requests/{request_id}/transition` | business transition |
| POST | `/staff/requests/{request_id}/orchestration/reconcile` | reconcile orchestration state |
| POST | `/staff/orchestration/reconcile-deferred` | reconcile deferred records |

Staff endpoints dùng auth dependency và scope kiểm tra tại API layer.

## 7. Internal agent

| Method | Path |
|---|---|
| POST | `/internal/agent/session` |
| POST | `/internal/agent/ask` |
| POST | `/internal/agent/prepare` |
| POST | `/internal/agent/confirm` |

Các endpoint này dùng `require_agent` dependency.

## 8. Telemetry

`POST /api/telemetry` nhận client latency event. Source hiện tránh gắn raw text/audio vào metric record tại endpoint này.

## 9. Contract source

Khi tài liệu và code khác nhau, code/schema trong repository là nguồn cần kiểm tra lại trước khi tích hợp client.
