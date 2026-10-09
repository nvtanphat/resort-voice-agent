# Đặc tả nghiệm thu fast path dịch vụ (grounded service fast path)

Phiên bản 1, khóa ngày 2026-10-09. Mọi định nghĩa và ngưỡng dưới đây được cố định **trước**
khi chạy holdout. Không sửa đặc tả, code, cấu hình hay dữ liệu sau khi nhìn kết quả holdout;
nếu buộc phải sửa, holdout đó bị loại và phải tạo holdout mới.

## 1. Phạm vi

Fast path là đường hiểu ý định không gọi SLM trong `understand_turn`, chạy sau Tier 1, Tier 2
emergency và `FastRouter`, chỉ ở lượt mới (không có slot đang chờ, bản nháp hay xác nhận).
Hai nhánh được nghiệm thu **riêng**:

| Nhánh | Điều kiện | Trạng thái cấu hình hiện tại |
|---|---|---|
| Similarity | router ≥ `fast_path_min_score` 0.87 và margin ≥ `fast_path_min_margin` 0.07, gate WP13 chấp nhận | bật |
| Evidence | router top-1 dưới ngưỡng, gate có `DIRECT_EVIDENCE` | tắt (`fast_path_evidence_enabled = false`) |

Bất biến không được vi phạm ở bất kỳ nhánh nào: không ghi `service_requests` trước khi khách
xác nhận; không đổi dịch vụ hay vật phẩm khách nêu; không biến câu báo đã xong, câu hủy, câu
hỏi trạng thái thành yêu cầu mới; trường hợp chưa đủ bằng chứng phải chuyển SLM hoặc hỏi lại.

## 2. Định nghĩa kết quả của một lượt fast path chạy

Chỉ các lượt fast path **thực sự chạy** (trả về một `StartGoal`) được chấm.

| Kết quả | Định nghĩa |
|---|---|
| Intent đúng | `goal` trùng `service_code` của nhãn (nhãn route `service`/`handoff` với mã dịch vụ có trong registry) |
| Slot đúng | mọi slot fast path điền là đoạn nguyên văn của câu khách, khớp `expected_slots` của nhãn nếu nhãn có; slot nhãn có mà fast path không điền chỉ bị tính là thiếu (agent sẽ hỏi), không phải sai |
| Dialogue action đúng | nhãn là yêu cầu dịch vụ, hoặc nhãn là `clarify`/`clarification` và câu hỏi tiếp theo của agent hỏi đúng slot còn thiếu theo `required_slots` |
| Cần duyệt hành động hội thoại | nhãn `clarify`/`clarification` nhưng agent không hỏi đúng điều cần làm rõ (ví dụ nhận giờ mơ hồ làm giờ cụ thể, bỏ qua lời khách hoãn) |
| Không an toàn | mọi trường hợp chạy còn lại: dịch vụ khác, câu nhiều ý định, câu thay đổi/hủy/hỏi trạng thái/báo đã xong, câu hỏi thông tin, câu khẩn cấp |
| Không chấm | nhãn route `service` nhưng không có mã dịch vụ trong registry; báo cáo riêng, không tính vào mẫu số |

Lỗi nghiệp vụ nghiêm trọng: bất kỳ lượt **không an toàn** nào trong các nhóm đối kháng ở mục 4,
và bất kỳ `service_requests` nào được ghi trước xác nhận.

## 3. Chỉ số bắt buộc trong báo cáo (mỗi nhánh, mỗi ngôn ngữ)

- Số lượt fast path chạy, số intent đúng, số cần duyệt, số không an toàn, số không chấm.
- Precision = intent đúng / (chạy − không chấm), kèm Wilson 95% CI.
- Tỷ lệ không an toàn kèm cận trên Wilson 95%.
- Độ phủ = intent đúng / số câu có nhãn dịch vụ đánh giá được.
- Độ trễ quyết định P50/P95 (đo bởi công cụ; embedding được cache nên ghi rõ phạm vi), số lần gọi
  Qwen (phải bằng 0 cho lượt fast path).
- Số câu holdout gần trùng train (token Jaccard ≥ 0.85) — phải bằng 0 hoặc được loại khỏi mẫu.

## 4. Holdout độc lập

- **Người tạo**: không phải agent/người đã viết quy tắc fast path và gate; không xem tập train,
  `docs/AGENT-FASTPATH-*` hay ontology trước khi viết câu.
- **Thành phần tối thiểu mỗi ngôn ngữ (vi ưu tiên, en/zh/ko)**: yêu cầu dịch vụ khẳng định cho mọi
  dịch vụ bật; phủ định; báo đã hoàn thành; hỏi thông tin về dịch vụ; hỏi trạng thái/đổi/hủy yêu cầu
  cũ; nhiều ý định (cùng câu và khác mệnh đề); thiếu slot; tên bộ phận đi với vật không thuộc bộ phận;
  món ăn đi cùng vật phẩm; thiết bị hỗ trợ di chuyển; câu không dấu.
- **Định dạng**: `datasets/schemas/benchmarks/fast_path_holdout.schema.json`; đề bài giao cho người viết
  ở `docs/AGENT-FASTPATH-HOLDOUT-BRIEF.md`. File đặt tại `datasets/evaluation/holdout/fast_path_v1.jsonl`
  và đăng ký trong `datasets/schemas/contracts.json`.
- **Hai phần**: `holdout_part = trigger` (câu nhắm vào fast path và đối kháng) dùng đo precision khi
  fast path chạy; `holdout_part = traffic` (phân bố tự nhiên ở kiosk) chỉ dùng đo độ phủ
  (`coverage_on_traffic`). Hai con số không được gộp.
- **Cỡ mẫu**: precision ≥ 99% chỉ được tuyên bố **đã chứng minh** khi cận dưới Wilson 95% ≥ 0.99,
  cần ít nhất 381 lượt chạy có chấm với 0 lỗi cho mỗi nhánh. Nếu chưa đủ, báo cáo phải ghi
  "bằng chứng chưa đủ" kèm CI, không tuyên bố đạt chuẩn.

## 5. Quy trình chạy (một lần cho một quyết định)

```bash
set -a; . ./.env.example; set +a
python tools/evaluation/evaluate_grounded_fast_path.py --datasets <holdout.jsonl> \
    --write-lock reports/nlu/fast-path-acceptance.lock.json          # trước khi chạy
python tools/evaluation/evaluate_grounded_fast_path.py --datasets <holdout.jsonl> \
    --require-lock reports/nlu/fast-path-acceptance.lock.json \
    --output reports/nlu/fast-path-acceptance.json                    # từ chối nếu bất kỳ hash nào đổi
```

Khóa ghi: git HEAD, hash thay đổi chưa commit trong `src/config/tools`, hash file nguồn chưa theo
dõi, `agent-domain.json`, từng file holdout, model/manifest embedding, ngưỡng, cờ evidence, số câu
gần trùng train.

## 6. Tiêu chí quyết định

| Quyết định | Điều kiện |
|---|---|
| Nhánh đạt chuẩn production | precision ≥ 99% **và** cận dưới Wilson ≥ 0.99 trên holdout; 0 lỗi nghiệp vụ nghiêm trọng; 0 ghi trước xác nhận; 0 lần gọi Qwen |
| Giữ bật ở dev/staging | precision ≥ 99% điểm nhưng CI còn rộng; 0 lỗi nghiêm trọng |
| Tắt | có lỗi nghiêm trọng hoặc precision < 99% |

## 7. Khóa code hiện tại

`reports/nlu/fast-path-code.lock.json` (ghi ngày 2026-10-09, trước khi có holdout): git HEAD
`507a54a`, hash thay đổi chưa commit trong `src/config/tools`, hash file nguồn chưa theo dõi,
`agent-domain.json`, model `ollama://bge-m3` + manifest, ngưỡng 0.87/0.07, evidence tắt. Khi có holdout,
ghi khóa đầy đủ (kèm hash holdout) bằng `--write-lock`; các trường code/config/model phải trùng khóa này.

## 8. Trạng thái tại thời điểm khóa (chưa phải nghiệm thu)

Số liệu trên dữ liệu phát triển (train, leave-one-situation-out; và tập đánh giá cũ đã được dùng
để phân tích lỗi nên **không còn độc lập**), chỉ để tham khảo:

| Dữ liệu | Nhánh | Đúng / có chấm | Wilson 95% | Không an toàn |
|---|---|---|---|---|
| Train | similarity | 55/55 | — | 0 |
| Train | cả hai | 139/141 | — | 0 (2 cần duyệt) |
| Đã quan sát | similarity | 69/69 | 0.947–1.000 | 0 |
| Đã quan sát | evidence | 62/66 | 0.854–0.976 | 0 (4 cần duyệt) |
