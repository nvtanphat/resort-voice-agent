# Concierge Kiosk Agent

## 1. Mục đích

Repository này chứa mã nguồn cho một kiosk trợ lý khách sạn chạy theo mô hình single-property. Hệ thống kết hợp API, RAG, agent orchestration, workflow dịch vụ, bộ nhớ phiên, giao diện web và tùy chọn xử lý giọng nói.

Tài liệu này mô tả những thành phần đang có trong mã nguồn. Các mô tả về vận hành thực tế, độ chính xác của mô hình hoặc khả năng chịu tải cần được xác minh trong môi trường triển khai tương ứng.

## 2. Phạm vi hiện tại

Hệ thống có các nhóm chức năng sau:

- hỏi đáp dựa trên knowledge base của khách sạn;
- tìm dịch vụ và thông tin theo property profile;
- tạo, xác nhận, thay đổi và hủy yêu cầu dịch vụ;
- theo dõi trạng thái yêu cầu của khách;
- orchestration bằng LangGraph hoặc runtime loop tương ứng;
- lưu state và business data bằng SQLite;
- giao diện web cho khách và trang vận hành;
- tùy chọn STT/TTS cục bộ;
- cấu hình runtime theo profile `test`, `development`, `edge`, `production`.

## 3. Cấu trúc thư mục chính

```text
docs/           Tài liệu kỹ thuật của dự án
src/concierge_kiosk/    Backend Python
frontend/         Source React/TypeScript
web/            Web bundle được phục vụ bởi backend
config/          Runtime/domain profiles và schema
datasets/furama/      Dữ liệu cấu trúc của property hiện tại
knowledge/approved/    Knowledge đã được chuẩn bị cho ingestion
knowledge/compiled/    Knowledge đã biên dịch theo ngôn ngữ
releases/          Property/map/planning release hiện tại
tests/           Regression và integration tests
tools/           Công cụ data, packaging, operation và evaluation
data/            SQLite runtime data
```

`knowledge/**` và `datasets/furama/**` là dữ liệu khách sạn dùng cho hệ thống. Không nên xem chúng là tài liệu kỹ thuật của repository.

## 4. Yêu cầu môi trường

- Python 3.11 trở lên
- các dependency trong `pyproject.toml`
- LangGraph khi dùng orchestrator mặc định
- Ollama hoặc endpoint SLM loopback nếu bật các chức năng model-assisted
- model local tương ứng nếu bật embedding, reranker, NLI hoặc voice

Cài backend cho development:

```bash
python -m pip install -e ".[test,ops]"
```

For the local multilingual RAG path, install the optional OpenVINO adapter and
provision the pinned INT4 `bge-reranker-v2-m3` asset:

```bash
python -m pip install -e ".[test,ops,reranker]"
python tools/runtime/download_reranker_model.py
```

The generated manifest is required at runtime; the model directory is local
runtime state and is intentionally excluded from source control.

Chạy test:

```bash
python -m pytest -q
```

Chạy API:

```bash
python -m concierge_kiosk
```

API mặc định lắng nghe tại `0.0.0.0:8000` khi chạy qua `__main__.py`.

## 5. Cấu hình

Các biến môi trường mẫu nằm ở:

```text
.env.example
config/local-runtime.env.example
```

Runtime profile nằm ở:

```text
config/runtime-profiles/test.json
config/runtime-profiles/development.json
config/runtime-profiles/edge.json
config/runtime-profiles/production.json
```

Một số profile yêu cầu thêm model, manifest, hash hoặc credential trước khi khởi động. Chi tiết xem `docs/OPERATIONS.md` và `docs/SECURITY.md`.

## 6. Tài liệu kỹ thuật

- `docs/ARCHITECTURE.md`: cấu trúc và luồng xử lý
- `docs/AGENT.md`: agent runtime và orchestration
- `docs/RAG.md`: ingestion, retrieval và grounding
- `docs/API.md`: nhóm endpoint
- `docs/DATA.md`: dữ liệu property và knowledge
- `docs/OPERATIONS.md`: chạy local, maintenance và deployment
- `docs/TESTING.md`: test và kiểm tra
- `docs/SECURITY.md`: boundary và cấu hình liên quan bảo mật
- `docs/LIMITATIONS.md`: giới hạn và phần chưa được xác minh

## 7. Ghi chú về kết luận kỹ thuật

Repository có các cơ chế kiểm tra, profile, test và operation tool. Các cơ chế này cho biết cách hệ thống được thiết kế và kiểm tra trong codebase; chúng không tự động chứng minh hiệu năng, độ chính xác hoặc mức độ phù hợp với mọi môi trường triển khai.

# resort-voice-agent
