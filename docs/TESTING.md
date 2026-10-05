# Testing

## 1. Test runner

Test suite dùng pytest.

```bash
python -m pytest -q
```

CI được cấu hình để cài project cùng test/ops dependencies rồi chạy pytest.

## 2. Nhóm test hiện có

Các file test bao phủ những nhóm sau:

- agent runtime;
- LangGraph restart/durability;
- RAG retrieval và golden queries;
- NLU và memory;
- property/runtime profiles;
- service registry và workflow transition;
- property data/schema/semantic consistency;
- UI contract;
- signed release pipeline;
- production signoff logic;
- navigation và planning;
- user journeys.

Danh sách đầy đủ nằm trong `tests/`.

## 3. LangGraph test

Các test LangGraph import dependency trực tiếp. Nếu dependency bắt buộc bị thiếu, test không nên được xem là đã thực thi thành công.

Ví dụ:

```bash
python -m pytest -q tests/test_langgraph_durable_restart.py
python -m pytest -q tests/test_agent_runtime.py
```

## 4. ResourceWarning

Có thể chạy test với warning cụ thể được nâng thành error:

```bash
python -m pytest -q -W error::ResourceWarning
```

Cách chạy này hữu ích khi kiểm tra lifecycle của file/socket/SQLite connection.

## 5. Data validation

Ngoài pytest còn có các tool kiểm tra dataset, schema và semantic trong `tools/`.

Ví dụ:

```bash
python tools/validate_furama_schemas.py
python tools/validate_furama_semantics.py
python tools/audit_furama_data.py
```

Cần xem `--help` hoặc source nếu script yêu cầu tham số.

## 6. Evaluation khác test

Regression test kiểm tra contract và behavior đã được encode trong test suite. Benchmark/evaluation đo chất lượng model/retrieval theo dataset riêng. Hai loại kết quả không nên thay thế cho nhau.

Repository có evaluation utilities trong `tools/evaluation/`, nhưng tài liệu này không đưa ra kết luận về chất lượng model dựa chỉ trên việc test pass.

## 7. Trước khi merge/release

Một chuỗi kiểm tra có thể dùng:

```bash
python -m compileall -q src tools
python -m pytest -q -W error::ResourceWarning
```

Nếu frontend thay đổi, chạy thêm build/type checks tương ứng của frontend.
