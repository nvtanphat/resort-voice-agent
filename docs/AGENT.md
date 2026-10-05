# Agent runtime

## 1. Vai trò

Agent runtime dùng để quyết định bước tiếp theo trong các tình huống cần đọc dữ liệu, lập kế hoạch hoặc thực hiện workflow nhiều bước. Business transition vẫn đi qua application/domain service.

## 2. Luồng execution

Các semantic chính được tập trung tại `agent/runtime/loop_semantics.py` và được dùng bởi runtime loop/LangGraph adapter.

Luồng khái quát:

```text
input
 -> build/restore state
 -> verify state
 -> plan next action
 -> validate action
 -> execute tool
 -> observe result
 -> update state
 -> verify / finish / re-plan
```

Planner không tự ghi business state trực tiếp vào database.

## 3. Budget

Các giới hạn chính:

- `agent_max_steps`
- `agent_max_wall_time_ms`
- `agent_max_planner_calls`
- `agent_max_read_calls`
- timeout riêng cho planner, goal interpreter và reference resolver

Các giá trị mặc định khác nhau giữa runtime profile.

## 4. LangGraph

`agent/runtime/langgraph_loop.py` là adapter orchestration sử dụng LangGraph. Checkpoint được lưu riêng với business request state.

Repository có test cho restart/resume và trường hợp commit business data trước khi checkpoint được cập nhật. Việc các test này chạy được phụ thuộc vào dependency LangGraph được cài trong môi trường test.

## 5. Tool contracts

Tool definitions và capability contracts được đặt tại:

```text
agent/core/capabilities.py
agent/core/tool_contracts.py
agent/tools/
```

Mỗi tool xử lý một nhóm thao tác như đọc knowledge, navigation, scheduling hoặc service slot.

Tool result được đưa trở lại agent state để planner/verifier quyết định bước tiếp theo.

## 6. Memory

Agent sử dụng nhiều loại state:

- conversation memory;
- session preference memory;
- task memory;
- task checkpoint;
- LangGraph checkpoint;
- semantic/reference resolution state.

TTL và giới hạn context được cấu hình trong `Settings`.

## 7. Model-assisted components

Tùy runtime profile, hệ thống có thể dùng local SLM cho:

- intent parsing;
- goal interpretation;
- next-action planning;
- semantic answer generation.

Endpoint SLM được giới hạn về loopback trong settings validation. Production profile còn yêu cầu digest và các asset semantic/NLI liên quan.

## 8. Boundary với business workflow

Agent đề xuất hoặc điều phối action, nhưng các thao tác như tạo/xác nhận/thay đổi request phải đi qua workflow service và transition rule.

Cách tách này nhằm giảm việc phụ thuộc business truth vào output tự do của model. Hiệu quả của cơ chế cần được đánh giá bằng test theo từng workflow và môi trường runtime.
