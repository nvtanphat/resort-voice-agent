# Retrieval, grounding và memory

## Nguồn và ingestion

Canonical facts/entities/relations/aliases nằm trong `datasets/knowledge/canonical/`; sources và verification snapshots ở `datasets/knowledge/sources/`. Knowledge markdown là input/derived runtime content, không phải báo cáo kỹ thuật.

Ingestion áp dụng chunk/metadata policy, property namespace và source/revision evidence. Development CLI có thể ingest directory; production yêu cầu signed bundle. Rebuild/indexing có thể ghi SQLite và gọi embedding model: không chạy chỉ để đọc tài liệu hoặc lấy telemetry.

Xem [Data](DATA.md) và [Operations](OPERATIONS.md) trước khi thay nguồn hoặc index.

## Retrieval

Implementation chính: `rag/retrieval/engine.py`, policy, evidence/context helpers; embedding, reranker và vectorstore tách riêng.

- Hỗ trợ `lexical`, `dense`, `hybrid`; dense yêu cầu embedder. Policy/profile chọn assets, budgets và fallback.
- Filter theo property, ngôn ngữ và context/fact constraints; kết hợp candidates và rerank khi khả dụng.
- Answerability, evidence verification và citation validation quyết định câu trả lời hoặc abstention.
- Hash embedder phục vụ hermetic tests/fallback theo profile, không đại diện chất lượng learned embedding. Kiểm tra model/index consistency ở readiness.

Không suy ra mode/model chạy thực tế chỉ từ file cấu hình. Retrieval latency và top-k là metadata của bước đã chạy; instrumentation không truy hồi thêm lượt.

## Trả lời

Câu hỏi thông tin rõ ràng có thể đi RAG read-only trước Qwen. Nhánh này không prepare/confirm service và không dùng generator để vá một execution request chưa hiểu. Các cách nói thông tin implicit chưa được nhận diện vẫn cần NLU bình thường.

Câu trả lời phải dựa trên nguồn được phép; source/citation không hợp lệ hoặc evidence thiếu thì clarification/abstention theo contract. Optional generation không cấp quyền ghi hay tự xác nhận booking.

## Verified memory

Anchor hợp lệ phải thuộc session hiện tại, còn TTL, đúng source revision và đã được server xác minh. Resolver xem đúng facet/context reference; model đặt `refers_to_context` chưa đủ cấp quyền.

Hỏi tiếp và booking referent chỉ dùng context có thật. Một anchor spa hợp lệ có thể giữ spa referent cho booking; anchor hết hạn, foreign session, nhiều referents hoặc thiếu evidence phải clarify. Không gửi raw snapshots/preferences/conversation lên Langfuse.

Pending task/proposal continuation khác anchor kiến thức: có ownership và state contract riêng. Kết thúc session, expiry hoặc invalidation phải ngăn tái dùng context không còn quyền.

## Đánh giá

Dùng `tools/evaluation/run_retrieval_eval.py` với qrels/ground-truth của suite, ghi mode/model/cache/RAM và budgets. Recall@K/MRR không được thay bằng số test PASS. Current learned Recall@K và live RAG/browser nghiệm thu còn thiếu; xem [Limitations](LIMITATIONS.md).
