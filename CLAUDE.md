# Hướng dẫn cho coding agent

Đọc [AGENT.md](AGENT.md) trước khi sửa code. Quy tắc ở đó áp dụng cho mọi coding agent, không phụ thuộc tên công cụ.

## Điểm vào dự án

- `src/concierge_kiosk/main.py`: composition root, routes và lifecycle. Import module tạo app và mở storage; đặt test environment và DB tạm trước khi import.
- `application/conversation/`: điều phối lượt hỏi, context và câu trả lời.
- `agent/understanding/`: routing, Structured Commands, semantic evidence và emergency gate.
- `agent/runtime/`: LangGraph, governed tools, checkpoints và evaluation harness.
- `domain/requests/`, `persistence/`: business policy, transaction và receipts.
- `rag/`, `voice/`: adapters/capabilities, không cấp business authority.

Chi tiết nằm trong [Architecture](docs/ARCHITECTURE.md), [API](docs/API.md) và [Data](docs/DATA.md); không sao chép kiến trúc vào nhiều tài liệu.

## Quy trình làm việc

1. Kiểm tra HEAD, branch, working tree; giữ nguyên công việc chưa commit.
2. Đọc source tại boundary lỗi, profile/schema và test tương ứng; tái hiện bằng test nhỏ.
3. Sửa domain-owned data hoặc thuật toán tổng quát; không hardcode câu evaluation hay đổi nhãn để tăng điểm.
4. Dùng test tuần tự, SQLite tạm/copy và unique basetemp. Không coi mocked NLU là real-model accuracy.
5. Kiểm tra config pins và cập nhật tài liệu theo chức năng; không tạo file WP/final/v2/rerun.
6. Không tự commit/push, download model, chạy full benchmark, Cloud ingestion hay inference ngoài phạm vi user đã cho phép.

## Kiểm tra thường dùng

```powershell
python tools/config/repin_configs.py --check
python tools/runtime/run_offline_tests.py -- tests/agent/test_pending_read_boundaries.py -q
```

Optional SDK cache: thêm `--sdk` trước `--`; cache có thể không tồn tại trên checkout mới. Cài đặt chính thức qua extra `observability`, không copy dependency vào reports.

Các test đọc knowledge dùng `shipped_db`/`tests/shipped_db.py`. Không mở tracked SQLite để thử luồng ghi. `tests/rebuild_pending.py` chứa strict-xfail còn cần xử lý; chỉ bỏ khi case gốc PASS với contract đúng.

Frontend source ở `frontend/`, runtime assets ở `web/`; build theo [Setup](SETUP.md). Production không tự reload model/config. Xem [Operations](docs/OPERATIONS.md) trước khi dùng maintenance/ingestion tools.

## Ranh giới phải giữ

Emergency thắng normal routing; Semantic Gate vẫn fail-closed; similarity fast path bật và evidence fast path tắt theo cấu hình hiện tại. Context phải owned/live/verified. Service writes cần explicit confirmation; proposal, queued, approved và completed là các trạng thái khác nhau. Langfuse chỉ quan sát.

Bằng chứng và phần chưa nghiệm thu nằm trong [Testing](docs/TESTING.md) và [Limitations](docs/LIMITATIONS.md).
