# Giới hạn và phần cần xác minh

## 1. Benchmark

Tài liệu hiện tại không dùng benchmark retrieval, generation hoặc voice để kết luận chất lượng hệ thống. Các metric này cần chạy trên bộ đánh giá và model artifact tương ứng với môi trường mục tiêu.

## 2. Model artifact

Runtime profile tham chiếu nhiều model local nhưng không phải mọi model đều được đóng gói trong repository. Trước khi bật feature tương ứng cần kiểm tra model path, manifest và dependency.

## 3. LangGraph dependency

Agent orchestration mặc định dùng LangGraph. Các integration test liên quan chỉ phản ánh execution thực tế khi LangGraph và checkpoint dependency đã được cài.

## 4. SQLite

SQLite phù hợp với kiến trúc single-appliance hiện tại nhưng giới hạn concurrency và operational model khác database server. Nếu thay đổi sang multi-node hoặc write concurrency cao cần đánh giá lại persistence design.

## 5. Single-property assumption

Code hiện đặt `property_id` từ cấu hình appliance. Hệ thống không được mô tả như multi-tenant runtime trong tài liệu này.

## 6. Voice

STT/TTS phụ thuộc local model, executable và hardware. Latency, quality và language coverage cần đo trên thiết bị mục tiêu.

## 7. Frontend bundle

`web/` là runtime asset và có thể chứa file build lớn. Source chỉnh sửa nằm trong `frontend/src/`. Sau thay đổi frontend cần build lại bundle và kiểm tra contract với backend.

## 8. Production configuration và production operation

Tên profile `production`, startup validation, signoff tool hoặc security option trong container không đồng nghĩa deployment đã được kiểm thử đầy đủ. Cần xác minh riêng:

- TLS/reverse proxy;
- credential rotation;
- backup/restore;
- crash recovery;
- disk-full behavior;
- sustained load;
- model availability;
- network isolation;
- monitoring/alerting;
- hardware resource budget.

## 9. Data freshness

Knowledge và structured dataset có version/release metadata, nhưng độ mới của nội dung phụ thuộc quy trình cập nhật nguồn. Không nên suy ra dữ liệu đang mới chỉ từ việc schema validation pass.

## 10. Evaluation và regression

Regression tests cho biết behavior đã encode trong test suite. Chúng không tự đo trải nghiệm người dùng hoặc factual accuracy trên dữ liệu ngoài test set.
