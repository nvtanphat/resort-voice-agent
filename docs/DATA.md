# Dữ liệu, schema và review

## Nguồn chính

Path resolution nằm trong `core/dataset_layout.py`. `CONCIERGE_STRUCTURED_DATASET_DIR` có thể chọn dataset root; các reader dùng resolver chung, không hardcode thư mục property.

| Vị trí | Vai trò |
|---|---|
| `datasets/knowledge/canonical/` | Facts, entities, relations, aliases, service catalog, property/map/planning và context labels |
| `datasets/knowledge/sources/` | Verified sources và verification snapshots |
| `datasets/quarantine/` | Dữ liệu chưa đủ điều kiện làm canonical evidence |
| `datasets/synthetic/operations/` | Workflow/inventory/staff/PMS/billing synthetic; không phải bằng chứng tích hợp hệ thống khách sạn thật |
| `datasets/training/agent/` | Reviewed examples/candidates cho understanding |
| `datasets/evaluation/` | Gold, holdout, frames, scenarios, challenges và evaluation inputs |
| `datasets/schemas/` | Schemas, contracts và validators |
| `knowledge/approved/`, `knowledge/compiled/` | Knowledge markdown đầu vào/biên dịch |
| `releases/` | Artifacts generated, hashes/signatures theo release contract |
| `data/` | Knowledge/business/checkpoint/vector runtime state |

Source facts và operational synthetic data có authority khác nhau. Không dùng synthetic inventory để tuyên bố live booking/dispatch/PMS đã hoạt động.

## Config và nội dung ngôn ngữ

`config/agent-domain.json` sở hữu ontology/grammar/service/locale policy; schema và SHA-256 đi kèm. Runtime source profile ở `config/runtime-profiles/src/`; generated profiles và hashes ở parent directory.

`locales/*.json` chứa nội dung ngôn ngữ dùng chung. Backend đọc qua `src/concierge_kiosk/i18n/`; frontend sử dụng i18n adapter. Không viết lời khách/alias/property facts cố định vào Python để qua một test.

## Thay đổi dữ liệu

1. Sửa nguồn canonical hoặc candidate được phép; giữ provenance và phạm vi authority.
2. Validate schema/contracts/semantics. Release/artifact generated phải được tạo bằng tool tương ứng, không sửa tay.
3. Refresh dataset/property/synthetic manifests khi loại nguồn đó thay đổi; repin config/release theo công cụ hiện có.
4. Kiểm tra source revision/index/context invalidation và regression liên quan.

```powershell
python datasets/schemas/validate_contracts.py
python tools/config/repin_configs.py --check
```

Đây là lệnh kiểm tra; rebuild/ingestion khác và có thể gọi model/ghi DB. Xem [Operations](OPERATIONS.md).

## Training và holdout

Không chép câu evaluation hoặc [backend cases](backend-test-cases.md) sang training/domain policy. Candidate do agent viết dùng CANDIDATE; GOLD cần human review. Runtime/training loader và evaluator phải dùng cùng layout, nhưng evaluation labels không cấp live authority.

Giữ split theo concept/source family; bản dịch/biến thể không được đếm như independent holdout. Slot evidence phải có trong utterance/context hợp lệ, không tự bổ sung chỉ để oracle accept.

Existing fast-path holdout schema: `datasets/schemas/benchmarks/fast_path_holdout.schema.json`. Giữ trường/provenance/author theo schema hiện hành; không đổi tên dataset đã pin chỉ để dọn tên báo cáo.

- Independent author không xem train/ontology trước khi viết; phủ VI/EN/ZH/KO và enabled registry services.
- Tách trigger (precision/adversarial behavior) với traffic (coverage); không gộp mẫu số.
- Phủ negation, completed/information/status/change/cancel, multi-intent, missing slot, department/object mismatch, deferral/DND và context.
- Quyết định acceptance giữ sample size/CI và severity policy; xem [Testing](TESTING.md).

Nhãn wake-up `NAT-0295` đã được human review thành `wake_up_call` theo registry. Đây là provenance hiện có, không phải thay nhãn trong đợt viết tài liệu.

## Các nhãn còn cần review

10/63 semantic frames và 45/270 production scenarios kỳ vọng service writes trước confirmation, xung đột `guest_confirm_all`. Queue 55 IDs ở [Limitations](LIMITATIONS.md). Chưa áp dụng sửa nhãn; mismatch không chứng minh Agent đã ghi trái phép.

Draft/proposal/audit không bằng committed service receipt. Emergency alert có workflow riêng; không dùng nó để nới service confirmation.
