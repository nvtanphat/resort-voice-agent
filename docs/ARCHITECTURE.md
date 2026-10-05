# Kiến trúc hệ thống

## 1. Mô hình tổng quát

Hệ thống được tổ chức theo hướng tách API, application workflow, agent runtime, RAG, domain và persistence.

```text
Web / Client
  |
FastAPI routes
  |
Application services
  |---------------------|
  |           |
Agent runtime     Workflow service
  |           |
RAG / tools / memory  Domain rules
  |           |
  |---------- SQLite ---|
```

Sơ đồ trên phản ánh dependency chính trong mã nguồn, không mô tả toàn bộ call graph.

## 2. Composition root

`src/concierge_kiosk/main.py` tạo `FastAPI` app và nối các thành phần runtime. Các nhóm được khởi tạo tại đây gồm:

- settings và runtime profiles;
- property/domain profile;
- SQLite store;
- workflow service;
- RAG/answer services;
- agent runtime;
- memory/checkpoint stores;
- voice services;
- route groups;
- metrics và lifecycle resources.

Logic nghiệp vụ chính được đặt ngoài route registration để route chủ yếu xử lý request/response và dependency.

## 3. Domain và workflow

Các rule liên quan service request nằm tại:

```text
src/concierge_kiosk/domain/
src/concierge_kiosk/application/
```

Business request được lưu trong SQLite. LangGraph checkpoint được dùng cho orchestration state, không thay thế bảng business request.

Các transition được kiểm tra ở application/domain layer. Một số constraint cũng được đặt ở persistence layer.

## 4. Agent runtime

Agent code nằm tại:

```text
src/concierge_kiosk/agent/
```

Các phần chính:

- `core/`: capability và tool contracts;
- `runtime/`: execution loop, planner, verifier, state;
- `orchestration/`: graph và mixed workflows;
- `memory/`: conversation, preference và task memory;
- `tools/`: navigation, planning, scheduling và read tools;
- `understanding/`: intent, routing, NLI và semantic parsing.

Agent có budget theo step, planner call, read call và wall time. Giá trị được lấy từ runtime profile.

## 5. RAG

RAG code nằm tại:

```text
src/concierge_kiosk/rag/
```

Pipeline có các bước theo cấu hình runtime:

```text
query
 -> lexical retrieval
 -> dense retrieval khi embedding được cấu hình
 -> fusion / relevance filtering
 -> rerank khi model được cấu hình
 -> context construction
 -> claim/citation checks
```

Không phải mọi bước đều hoạt động trong mọi profile; model path và feature flag quyết định thành phần nào được dùng.

## 6. Persistence

`src/concierge_kiosk/persistence/sqlite_store.py` quản lý SQLite cho session, request, knowledge, metric và các dữ liệu runtime khác.

Runtime sử dụng một property trên một appliance. `property_id` được lấy từ cấu hình thay vì nhận trực tiếp từ guest request.

## 7. Voice

Voice API và runtime nằm trong:

```text
src/concierge_kiosk/api/voice/
src/concierge_kiosk/voice/
```

Hệ thống hỗ trợ các đường xử lý:

- tạo/cancel voice turn;
- HTTP transcription;
- WebSocket audio streaming;
- TTS playback acknowledgement;
- tùy chọn incremental STT.

Các model voice không được nhúng đầy đủ vào repository và cần được provision riêng nếu profile yêu cầu.

## 8. Frontend

`frontend/` chứa source React/TypeScript. `web/` chứa asset được backend phục vụ.

Không nên chỉnh trực tiếp bundle sinh ra nếu thay đổi có thể thực hiện tại `frontend/src/` rồi build lại.

## 9. Runtime profiles

Bốn profile hiện có:

| Profile | Mục đích trong repo | Đặc điểm cấu hình |
|---|---|---|
| `test` | chạy test | tắt model-assisted intent/planner mặc định |
| `development` | phát triển local | cho phép fallback model và embedding candidate |
| `edge` | cấu hình tài nguyên thấp hơn | budget agent thấp hơn development |
| `production` | cấu hình kiểm soát chặt hơn | strict SLM, manifest/signoff và ingress constraints |

Tên profile không phải là chứng nhận môi trường. Runtime vẫn kiểm tra các asset và setting bắt buộc khi load cấu hình.
