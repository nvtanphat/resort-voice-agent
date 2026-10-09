# Đề bài viết holdout độc lập cho fast path (giao nguyên văn cho người/agent viết)

Người viết **không** được đọc repo (quy tắc, ontology `config/agent-domain.json`, dữ liệu
`datasets/training`, `datasets/evaluation`, các tài liệu `docs/AGENT-FASTPATH-*` trừ đề bài này).
Chỉ dùng thông tin dưới đây. Kết quả ghi vào
`datasets/evaluation/holdout/fast_path_v1.jsonl`, đúng schema
`datasets/schemas/benchmarks/fast_path_holdout.schema.json`.

## Danh sách dịch vụ (registry, mã chính xác)

| Mã | Mô tả | Slot bắt buộc |
|---|---|---|
| amenity_delivery | Mang thêm vật dụng/đồ dùng lên phòng (khăn, nước, đồ vệ sinh, adapter, đồ nhỏ) | room_number, requested_item |
| maintenance | Sửa đồ hỏng/không hoạt động trong phòng (điều hòa, ống nước, đèn, TV, khóa) | room_number |
| housekeeping | Dọn/dọn dẹp phòng, thay ga, hẹn giờ dọn | room_number |
| human_assistance | Kết nối nhân viên hoặc việc không dịch vụ nào khác bao quát (hành lý, đồ thất lạc, hỗ trợ đặc biệt) | — |
| spa_reservation | Đặt spa, massage, liệu trình theo giờ | preferred_time |
| dining_reservation | Giữ bàn nhà hàng cho số người theo giờ | party_size, preferred_time |
| food_order | Gọi đồ ăn/uống lên phòng (room service) | room_number |
| tour_reservation | Đặt chỗ tour có sẵn theo giờ | preferred_time |
| wake_up_call | Đặt cuộc gọi báo thức theo giờ | preferred_time |
| facility_request | Thiết bị/tiện ích không thuộc dịch vụ riêng (giường phụ, nôi, bàn là, máy sấy tóc) | — |
| dining_request | Nhu cầu ăn uống đặc biệt không phải giữ bàn (bánh, bữa riêng, ăn kiêng, dị ứng) | — |
| tour_request | Nhờ quầy tour sắp xếp/tư vấn chuyến riêng, hướng dẫn viên, hoạt động ngoài tour có sẵn | — |
| transport_request | Gọi taxi, xe, đưa đón sân bay theo giờ | preferred_time |
| late_checkout | Xin giữ phòng quá giờ trả phòng | room_number, preferred_time |

## Trường mỗi dòng (bắt buộc, không thêm trường khác)

`case_id` (FPH-0001…), `classification` = `fast_path_acceptance_holdout`, `truth_status` =
`independent_author_unreviewed`, `author` = `independent_agent_2026_10_09` (hoặc đổi cả schema nếu
người viết khác), `holdout_part` (`trigger`/`traffic`), `category`, `language` (vi/en/zh/ko),
`utterance`, `expected_route`, `service_code` (một mã khi route là `service`, ngược lại null),
`expected_slots` (giá trị có nguyên văn trong câu), `expected_dialogue_action`.

## Quy tắc gán nhãn

- Hỏi VỀ dịch vụ (giá, giờ, cách đặt): `knowledge`, null, `answer_information`.
- Hai hành động trở lên trong một lượt: `multi_step`, null. Đồ ăn đi cùng vật dụng: `multi_step`.
- Hỏi trạng thái / đổi / hủy yêu cầu cũ: `status` / `request_change` / `cancel`, null.
- Báo việc đã xong ("housekeeping mang nước rồi"): `non_action`, `no_action`.
- Nêu đang bật DND kèm yêu cầu cần vào phòng (dọn, sửa): `clarification`, null, `clarify`.
- Đưa chi tiết rồi hoãn ("…để mình xem lại lịch đã"): `clarification`, null, `hold`.
- Khung giờ mơ hồ ("tối nay") cho dịch vụ bắt buộc `preferred_time`: `service`, `ask_missing_slot`.
- Mượn xe lăn/xe đẩy: `human_assistance`. Tên bộ phận đi với vật không thuộc bộ phận đó ("housekeeping
  mang bàn là"): dịch vụ của vật (bàn là → `facility_request`).

## Hạn mức (440 dòng)

- `trigger` 300 dòng (vi 150, en 50, zh 50, ko 50): plain_request 120 (đủ 14 dịch vụ, đủ slot),
  missing_slot 15, time_window 12, negation 12, completed_report 12, information_question 15,
  status_or_change 12, cancel 10, multi_intent 15, department_object_mismatch 10, food_with_item 10,
  mobility_aid 8, deferral 12, dnd_conflict 8, no_diacritics 9 (tiếng Việt không dấu).
- `traffic` 140 dòng (vi 70, en 24, zh 23, ko 23): phân bố tự nhiên ở kiosk sảnh (hỏi tiện ích, giờ,
  đường đi, xã giao, cảm ơn; khoảng một phần ba là yêu cầu dịch vụ).

## Sau khi có file

1. Thêm vào `datasets/schemas/contracts.json`:
   `{"path": "datasets/evaluation/holdout/fast_path_v1.jsonl", "schema": "datasets/schemas/benchmarks/fast_path_holdout.schema.json", "mode": "jsonl"}`;
   chạy `python datasets/schemas/validate_contracts.py` và `python tools/manifest/refresh_dataset.py`.
2. Khóa và chạy đúng một lần theo `docs/AGENT-FASTPATH-ACCEPTANCE-SPEC.md` mục 5.
   Precision đọc trên phần `trigger`; độ phủ (`coverage_on_traffic`) chỉ đọc trên phần `traffic`.
