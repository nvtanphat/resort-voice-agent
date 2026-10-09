# Resort Voice Agent

Trợ lý khách sạn đa ngôn ngữ, chạy theo mô hình một property trên một appliance. Backend FastAPI điều phối Agent bằng LangGraph, trả lời từ knowledge có nguồn, chuẩn bị yêu cầu dịch vụ và chuyển sang quy trình xác nhận/nhân viên.

Repository: `nvtanphat/resort-voice-agent`; nhánh làm việc hiện tại: `phase4-handoff`. Tài liệu mô tả working tree, bao gồm sửa đổi chưa commit; Git HEAD riêng lẻ không đại diện toàn bộ trạng thái đó.

## Chức năng đang có

- Hội thoại VI/EN/ZH/KO; Structured Command NLU qua Ollama, candidate selection và Semantic Authorization phía server.
- Emergency Tier1 theo domain policy; Tier2 dùng embedding và logistic classifier khi sẵn sàng.
- RAG lexical/dense/hybrid, kiểm tra grounding/citations và context anchor của phiên.
- Draft/proposal, explicit confirmation, staff review, trạng thái yêu cầu và idempotency.
- React guest UI, staff/status UI; STT/TTS và voice transport tùy profile/model đã provision.
- Metrics và evaluation hiện có; Langfuse optional, mặc định tắt, chỉ export telemetry được lọc.

Model đề xuất command; server quyết định authority. Draft/proposal chưa phải service request đã commit. Xác nhận của khách và xử lý của nhân viên là các bước riêng.

## Cấu trúc

| Thư mục | Vai trò |
|---|---|
| `src/concierge_kiosk/` | API, application, Agent, RAG, domain, persistence và voice |
| `frontend/`, `web/` | Source React và assets backend phục vụ |
| `config/`, `releases/` | Domain/runtime profiles, schemas, hashes và releases |
| `locales/` | Nội dung hiển thị theo ngôn ngữ |
| `datasets/`, `knowledge/` | Dữ liệu canonical, synthetic operations, training/evaluation và knowledge biên dịch |
| `data/`, `models/` | SQLite/vector state và model assets |
| `tests/`, `tools/` | Regression tests và công cụ dùng lại |
| `reports/`, `.cache/` | Output sinh tự động và cache local; không phải tài liệu nguồn |

Markdown trong `knowledge/` là dữ liệu runtime. Không dọn chúng như báo cáo kỹ thuật.

## Bắt đầu

Đọc [Setup](SETUP.md) để cài và chọn profile. Package hỗ trợ Python ≥3.11; các kiểm tra gần nhất dùng Python 3.12. Cài dependencies không đồng nghĩa đã provision model, pin digest hoặc nghiệm thu voice.

## Tài liệu

| Nhu cầu | Tài liệu |
|---|---|
| Cài đặt và chạy local | [SETUP.md](SETUP.md) |
| Kiến trúc và luồng command | [Architecture](docs/ARCHITECTURE.md) |
| API và authentication | [API](docs/API.md) |
| Nguồn dữ liệu, schema và review | [Data](docs/DATA.md) |
| Retrieval, grounding và memory | [RAG](docs/RAG.md) |
| Profile, lifecycle, model và Langfuse | [Operations](docs/OPERATIONS.md) |
| Trust boundaries và privacy | [Security](docs/SECURITY.md) |
| Cách test và bằng chứng hiện có | [Testing](docs/TESTING.md), [backend cases](docs/backend-test-cases.md) |
| Blocker và phần chưa đo | [Limitations](docs/LIMITATIONS.md) |
| Quy tắc sửa code | [AGENT.md](AGENT.md), [CLAUDE.md](CLAUDE.md) |

## Trạng thái nghiệm thu

Chưa nghiệm thu production. Qwen timeout, real-model multi-item/multi-intent, replay nguyên bản S01–S15, learned Emergency Tier2, live voice/browser và Langfuse Cloud còn thiếu bằng chứng. Test offline/mock không thay thế các phép nghiệm thu này.

Tên tài liệu theo chức năng, một đường dẫn ổn định; không tạo báo cáo theo WP/phiên bản. Lịch sử thuộc Git.
