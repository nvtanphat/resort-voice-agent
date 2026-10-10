# Giới hạn và phần chưa nghiệm thu

Tài liệu này là danh sách blocker hiện hành, không phải roadmap theo WP. Implementation/configured feature không đồng nghĩa model/Cloud/browser/voice đã được nghiệm thu.

## Trạng thái

| Hạng mục | Trạng thái và bằng chứng còn thiếu |
|---|---|
| SLM latency trên CPU | **NOT MET** trên laptop hiện hành; latency/budget và điều kiện đo nằm ở [Testing](TESTING.md#real-model-e2e-2026-10-10-đo-được-chưa-đạt-nghiệm-thu). Voice với model hiện hành chưa đo |
| Intent router (lời nói tự nhiên) | Còn chọn dịch vụ gần nghĩa, bỏ sót việc, câu hỏi lịch sự tiếng Hàn và câu hỏi chỗ trống. Số đo từng holdout ở [Testing](TESTING.md#intent-router--đo-bằng-model-thật-2026-10-10); `service_options` hỗ trợ khách chọn, chưa là bằng chứng NLU đã đạt |
| Transcript S01–S15 | Nguyên bản inputs/harness/failed node IDs chưa có; không thay bằng probe khác rồi gọi cùng suite |
| Real-model task accuracy | **NOT MET** trên service holdout độc lập; kết quả theo ngôn ngữ và đối chứng model ở [Testing](TESTING.md#real-model-e2e-2026-10-10-đo-được-chưa-đạt-nghiệm-thu) |
| BGE Recall@K và Emergency Tier2 | **NOT_MEASURED** tại model/hardware hiện hành; Tier1 recall không thay whole-system recall |
| Langfuse Cloud | **CLOUD_INGESTION_NOT_VERIFIED**; chưa controlled ingestion/read-back có quyền |
| Live voice/browser E2E | Chưa nghiệm thu STT/TTS/browser/streaming/audio lifecycle trên thiết bị mục tiêu |
| Production operation | Chưa đủ evidence về TLS/proxy, rotation, backup/restore, crash/disk-full/load/network isolation |

Chi tiết số đo và test ở [Testing](TESTING.md). Các lỗi được ghi nhận khi dọn code,
bao gồm lỗi SetSlot tái lập trên HEAD gốc, nằm tại
[kiểm chứng cleanup](TESTING.md#dọn-code-cơ-học-trên-head-a8bb47c). Các test RAG cần
model thật có thể không được offline runner cho phép; không suy diễn offline PASS thành
nghiệm thu model/voice. Tách module và đối chứng API có giới hạn chưa nghiệm thu
cổng hai mức trên holdout lớn.

- Khởi động: kiosk chờ `understanding_ready` trước khi nhận lượt; số đo warm-up thuộc [Testing](TESTING.md).
- Khẩn cấp: vùng hỏi lại (`emergency_check`, ngưỡng thiên recall) vẫn hỏi với câu báo hỏng có từ gần nghĩa ("nghẹt", "rò nước"); khách trả lời không thì yêu cầu gốc tiếp tục.

## Coverage và authority còn cần review

- Emergency domain chưa đủ reviewed evidence cho sudden speech/facial symptoms CORE-VI-EMERGENCY-14 và flooding/trapped person CORE-VI-EMERGENCY-18.
- Một số active-hazard examples có label service/status thay emergency: NEEDS_ADJUDICATION; không hạ safety hoặc sửa nhãn theo output.
- Verified spa anchor follow-up có kết quả theo boundary trong API model-unavailable; đây chưa phải real NLU hiểu ngữ cảnh massage trên transcript gốc.
- Similarity/candidate coverage cần đo với learned embedder sẵn sàng. Không thay unavailable similarity bằng keyword classifier.
- Implicit information/reference wording ngoài contract có thể cần Qwen hoặc clarification. Read-only fallback không cấp service authority.
- Training balance, missing examples và translation concepts cần source/human review; không tự grant business authority cho bản dịch chưa duyệt.

## Giới hạn triển khai

SQLite và process-local pending state phù hợp appliance hiện tại; multi-node/concurrency/restart behavior cần nghiệm thu theo topology thực. Property identity do config/pin cấp, không là multi-tenant user input.

Models lớn/secrets không có đầy đủ trong checkout. Default/template production cần operator provision digest, manifests, keys và assets; không chỉ đổi ENV rồi coi ready.

Knowledge freshness phụ thuộc nguồn/review, không suy ra từ checksum PASS. Synthetic operations/PMS/inventory không chứng minh kết nối business system thật. React source và bundle cần build consistency; voice cần assets/hardware đo thật.

## Review queue: 55 business-write expectations

Đối chiếu dataset hiện tại: 10/63 semantic frames và 45/270 production scenarios có expected_business_writes_before_confirmation >0. Policy guest_confirm_all yêu cầu explicit consent trước persisted service receipt. Draft/proposal/audit event và emergency alert khác service write.

Đây là dataset contract mismatch, không là bằng chứng Agent đã ghi DB trái phép. Chưa sửa gold labels; proposed value 0 cần adjudication đúng giai đoạn và authoritative release/review process. Source-family descendants không được đếm như independent holdout.

Machine-readable output lịch sử đã bị xóa khi cleanup; IDs/giá trị review được giữ nguyên:

| Source | ID | Existing before-confirmation writes | Review |
| --- | --- | --- | --- |
| frames/semantic_frames.jsonl | FRAME-AC_HOT | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-EXTRA_PILLOW | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-LIGHT_BROKEN | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-MULTI_TOWEL_CLEAN | 2 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-MULTI_TOWEL_LATE | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-SINK_LEAK | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-TOILET_BLOCKED | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-TOWEL_TWO | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-TV_SIGNAL | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| frames/semantic_frames.jsonl | FRAME-WATER_FOUR | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_light | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_clean | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_multi | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | vi_towel | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_vi_leaking_sink | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_vi_31 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_ac | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_clean | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_light | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_multi | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | en_towel | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_ac_not_cooling | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_clean_at_time | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_flooded_room_balance | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_leaking_sink | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_multi_towel_late | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_en_towel_direct | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_en_31 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_en_32 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_ac | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_clean | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_light | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_multi | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | ko_towel | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_ac_not_cooling | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_clean_at_time | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_dnd_clean_balance | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_leaking_sink | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_multi_towel_late | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_ko_towel_direct | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_ko_31 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_ko_32 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_clean_at_time | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_dnd_clean_balance | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_flooded_room_balance | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_leaking_sink | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_multi_towel_late | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | production_zh_towel_direct | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_zh_31 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | scenario_zh_32 | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_ac | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_clean | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_light | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_multi | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
| end_to_end/scenarios/production.jsonl | zh_towel | 1 | Propose0; NEEDS_ADJUDICATION; NOT_APPLIED |
