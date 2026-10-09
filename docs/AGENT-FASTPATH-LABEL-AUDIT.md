# Audit nhãn báo thức: `human_assistance` → `wake_up_call`

Ngày 2026-10-09, branch `phase4-handoff`, gốc so sánh HEAD `507a54ada5c779f21cbc2750cef1dfadafd42a0a`. Nội dung gốc của mọi dòng
được chép nguyên văn ở mục 4 và vẫn còn trong lịch sử git tại HEAD trên.

## 1. Căn cứ (không dùng dự đoán của BGE-M3 làm bằng chứng)

- Registry (`config/agent-domain.json → services`):
  - `wake_up_call`: "Schedule a telephone wake-up call to the guest room at a requested time.",
    `catalog_service_id = service.wake_up_calls`, `required_slots = [preferred_time]`.
  - `human_assistance`: "Connect the guest with a staff member, or handle a request **no other
    service covers** (luggage, lost items, special help)." Yêu cầu báo thức đã có dịch vụ riêng
    nên không thuộc phạm vi này.
- Catalog: `datasets/knowledge/canonical/service_catalog.json` có `service.wake_up_calls`
  ("Wake-Up Call Service").
- Nhất quán dữ liệu: dòng train `TRAIN-674527BC49C4` có `targets.expected_service_id =
  service.wake_up_calls` nhưng `service_code = human_assistance` (mâu thuẫn trong chính dòng);
  18 dòng train CANDIDATE cùng ý định gán `wake_up_call`; NAT-0803, NAT-0823 trong
  `challenges/natural.jsonl` cùng ý định gán `wake_up_call`.
- Dịch vụ `wake_up_call` có trong config từ commit `59652b8` (2026-10-05), trước khi các nhãn
  này được viết (`8f11fe1`, 2026-10-08), nên không phải do dịch vụ ra đời sau.
- Từng câu đã được đọc: mỗi câu đều là khách yêu cầu khách sạn gọi/đánh thức vào một giờ cụ
  thể, không phải yêu cầu gặp nhân viên hay việc ngoài các dịch vụ đã có.

## 2. Đã sửa (chỉ trường `service_code`; các trường khác giữ nguyên)

### 2.1 Tập đánh giá (6 dòng được duyệt)

| Vị trí (HEAD) | ID | Ngôn ngữ | Câu | Nhãn cũ | Nhãn mới | Bằng chứng trong dòng |
|---|---|---|---|---|---|---|
| `datasets/evaluation/gold/vi_dev.jsonl`:1 | DEV-E3EC45F57A03 | vi | Mai mình phải dậy lúc 4 giờ 45 để đi sân bay, nhờ đặt một cuộc gọi báo thức cho phòng. | `human_assistance` | `wake_up_call` | — |
| `datasets/evaluation/gold/vi_test.jsonl`:69 | TEST-854470E2AF7D | vi | Mai mình phải dậy lúc 5 giờ, nhờ khách sạn gọi báo thức giúp. | `human_assistance` | `wake_up_call` | — |
| `datasets/evaluation/challenges/natural.jsonl`:120 | NAT-0010 | ko | 내일 새벽 다섯 시에 깨워 주실 수 있을까요? | `human_assistance` | `wake_up_call` | expected_agent_action=create_service_request |
| `datasets/evaluation/challenges/natural.jsonl`:200 | NAT-0134 | en | Please schedule a wake-up call for five tomorrow morning; I have an early start. | `human_assistance` | `wake_up_call` | expected_agent_action=create_service_request |
| `datasets/evaluation/challenges/natural.jsonl`:269 | NAT-0228 | ko | 내일 오전 다섯 시에 모닝콜 등록 부탁드립니다. | `human_assistance` | `wake_up_call` | expected_agent_action=create_service_request |
| `datasets/evaluation/challenges/natural.jsonl`:358 | NAT-0361 | zh | 我明天需要五点起床，请帮我登记叫醒服务。 | `human_assistance` | `wake_up_call` | expected_agent_action=create_service_request |

`concept_key` của DEV-E3EC45F57A03 và TEST-854470E2AF7D vẫn là
`service__human_assistance__natural_challenge_curated` (khóa nhóm nguồn, không phải nhãn); giữ nguyên.

### 2.2 Tập train (13 dòng, sửa ở bước trước)

| Vị trí (HEAD) | ID | Ngôn ngữ | Câu | Nhãn cũ | Nhãn mới | Bằng chứng trong dòng |
|---|---|---|---|---|---|---|
| `datasets/training/agent/vi_gold.jsonl`:123 | TRAIN-91318A9B3B3C | vi | Báo thức giúp mình lúc 5 giờ sáng. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/vi_gold.jsonl`:173 | TRAIN-2FB6D460C901 | vi | Năm giờ sáng mai nhờ gọi báo thức cho mình. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/vi_gold.jsonl`:204 | TRAIN-F1160C7F00B9 | vi | À, tôi cần báo thức lúc 5 giờ sáng nha. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/vi_gold.jsonl`:336 | TRAIN-C4C2AC6DD795 | vi | Sáng mai mình phải dậy sớm, nhờ cài cuộc gọi báo thức lúc 5:00 cho phòng mình. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/vi_gold.jsonl`:440 | TRAIN-674527BC49C4 | vi | Sáng mai nhờ gọi báo thức cho phòng mình. | `human_assistance` | `wake_up_call` | targets.expected_service_id=service.wake_up_calls |
| `datasets/training/agent/vi_gold.jsonl`:553 | TRAIN-HUM-410C2241F8BB | vi | Nhờ wake-up call lúc 5 giờ sáng mai giúp mình với. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/vi_gold.jsonl`:585 | TRAIN-PH1-DF54406BA61E | vi | Mai mình có chuyến sớm, 5 giờ gọi báo thức giúp mình nhé. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/multilingual_support.jsonl`:174 | TRAIN-C8D5BCBCD097 | en | Please arrange a wake-up call for five tomorrow morning. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/multilingual_support.jsonl`:175 | TRAIN-9AFA45B395A0 | ko | 내일 아침 5시에 모닝콜 부탁드립니다. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/multilingual_support.jsonl`:176 | TRAIN-7D61CF78A281 | zh | 请明早五点给我安排叫醒服务。 | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/multilingual_support.jsonl`:225 | TRAIN-MGUEST-D2BB5E8B4E7A | en | I've got an early flight tomorrow. Could you give my room a wake-up call at five? I'm worried I'll sleep through my alarm. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/multilingual_support.jsonl`:226 | TRAIN-MGUEST-CE6BEE186C46 | ko | 내일 아침 비행기가 일러서요. 혹시 5시에 모닝콜 부탁드려도 될까요? 알람을 못 들을까 봐 걱정돼요. | `human_assistance` | `wake_up_call` | — |
| `datasets/training/agent/multilingual_support.jsonl`:227 | TRAIN-MGUEST-4131B8F1815D | zh | 我明天一早的航班，可以五点给房间打个叫醒电话吗？我怕自己睡过头。 | `human_assistance` | `wake_up_call` | — |

Sau khi sửa: `tools/manifest/refresh_dataset.py` làm mới `datasets/manifest.json`;
`validate_contracts.py`, `tools/validate/schemas.py`, `semantics.py`, `audit_data.py` đều đạt.
Vector cache của selector chuyển sang khóa mới (vector chỉ phụ thuộc câu, 2027 câu giữ nguyên thứ tự).

## 3. Bổ sung sau duyệt (2026-10-09)

NAT-0295 được người dùng duyệt sau khi đối chiếu nguyên văn với registry: câu yêu cầu đặt một
cuộc gọi báo thức lúc 5 giờ sáng mai, đúng mô tả `wake_up_call` ("Schedule a telephone wake-up call
to the guest room at a requested time.") và nằm ngoài phạm vi `human_assistance` ("a request no other
service covers"). Chỉ sửa `service_code`.

| Vị trí (HEAD) | ID | Ngôn ngữ | Câu | Nhãn cũ | Nhãn mới | Bằng chứng trong dòng |
|---|---|---|---|---|---|---|
| `datasets/evaluation/challenges/natural.jsonl`:318 | NAT-0295 | en | Could you set a 5 a.m. wake-up call for me tomorrow morning? | `human_assistance` | `wake_up_call` | expected_agent_action=create_service_request |

Không sửa nhãn nào khác trong lần này.

## 4. Nội dung gốc nguyên văn (HEAD)

`datasets/evaluation/gold/vi_dev.jsonl`:1
```json
{"scenario_id": "DEV-E3EC45F57A03", "split": "dev", "language": "vi", "utterance": "Mai mình phải dậy lúc 4 giờ 45 để đi sân bay, nhờ đặt một cuộc gọi báo thức cho phòng.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {}, "concept_key": "service__human_assistance__natural_challenge_curated", "frame_id": null, "source_family": "natural_challenge_curated", "source_id": "NAT-0522", "gold_status": "GOLD", "training_priority": "primary", "targets": {"expected_department_id": null, "expected_agent_action": "create_service_request", "capability": "service", "route_group": "standard"}}
```

`datasets/evaluation/gold/vi_test.jsonl`:69
```json
{"scenario_id": "TEST-854470E2AF7D", "split": "test", "language": "vi", "utterance": "Mai mình phải dậy lúc 5 giờ, nhờ khách sạn gọi báo thức giúp.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {}, "concept_key": "service__human_assistance__natural_challenge_curated", "frame_id": null, "source_family": "natural_challenge_curated", "source_id": "NAT-0221", "gold_status": "GOLD", "training_priority": "primary", "targets": {"expected_department_id": null, "expected_agent_action": "create_service_request", "capability": "service", "route_group": "standard"}}
```

`datasets/evaluation/challenges/natural.jsonl`:120
```json
{"case_id": "NAT-0010", "classification": "naturalistic_challenge", "truth_status": "synthetic_curated_behavioral_oracle", "language": "ko", "utterance": "내일 새벽 다섯 시에 깨워 주실 수 있을까요?", "expected_route": "service", "service_code": "human_assistance", "expected_department_id": null, "expected_agent_action": "create_service_request", "must_not_5xx": true, "must_not_claim_completed": true, "origin": "natural_challenge_curated_2026_10"}
```

`datasets/evaluation/challenges/natural.jsonl`:200
```json
{"case_id": "NAT-0134", "classification": "naturalistic_challenge", "truth_status": "synthetic_curated_behavioral_oracle", "language": "en", "utterance": "Please schedule a wake-up call for five tomorrow morning; I have an early start.", "expected_route": "service", "service_code": "human_assistance", "expected_department_id": null, "expected_agent_action": "create_service_request", "must_not_5xx": true, "must_not_claim_completed": true, "origin": "natural_challenge_curated_2026_10"}
```

`datasets/evaluation/challenges/natural.jsonl`:269
```json
{"case_id": "NAT-0228", "classification": "naturalistic_challenge", "truth_status": "synthetic_curated_behavioral_oracle", "language": "ko", "utterance": "내일 오전 다섯 시에 모닝콜 등록 부탁드립니다.", "expected_route": "service", "service_code": "human_assistance", "expected_department_id": null, "expected_agent_action": "create_service_request", "must_not_5xx": true, "must_not_claim_completed": true, "origin": "natural_challenge_curated_2026_10"}
```

`datasets/evaluation/challenges/natural.jsonl`:358
```json
{"case_id": "NAT-0361", "classification": "naturalistic_challenge", "truth_status": "synthetic_curated_behavioral_oracle", "language": "zh", "utterance": "我明天需要五点起床，请帮我登记叫醒服务。", "expected_route": "service", "service_code": "human_assistance", "expected_department_id": null, "expected_agent_action": "create_service_request", "must_not_5xx": true, "must_not_claim_completed": true, "origin": "natural_challenge_curated_2026_10"}
```

`datasets/training/agent/vi_gold.jsonl`:123
```json
{"scenario_id": "TRAIN-91318A9B3B3C", "split": "train", "language": "vi", "utterance": "Báo thức giúp mình lúc 5 giờ sáng.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": null, "source_family": "reviewed_scenario_curated", "source_id": "SCN-HOSP-VI-60-08", "gold_status": "GOLD", "training_priority": "primary", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/training/agent/vi_gold.jsonl`:173
```json
{"scenario_id": "TRAIN-2FB6D460C901", "split": "train", "language": "vi", "utterance": "Năm giờ sáng mai nhờ gọi báo thức cho mình.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": null, "source_family": "reviewed_scenario_curated", "source_id": "SCN-HOSP-VI-60-09", "gold_status": "GOLD", "training_priority": "primary", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/training/agent/vi_gold.jsonl`:204
```json
{"scenario_id": "TRAIN-F1160C7F00B9", "split": "train", "language": "vi", "utterance": "À, tôi cần báo thức lúc 5 giờ sáng nha.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": null, "source_family": "reviewed_scenario_curated", "source_id": "SCN-HOSP-VI-60-04", "gold_status": "GOLD", "training_priority": "primary", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/training/agent/vi_gold.jsonl`:336
```json
{"scenario_id": "TRAIN-C4C2AC6DD795", "split": "train", "language": "vi", "utterance": "Sáng mai mình phải dậy sớm, nhờ cài cuộc gọi báo thức lúc 5:00 cho phòng mình.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {}, "concept_key": null, "frame_id": null, "source_family": "natural_challenge_curated", "source_id": "NAT-0300", "gold_status": "GOLD", "training_priority": "primary", "targets": {"expected_department_id": null, "expected_agent_action": "create_service_request"}}
```

`datasets/training/agent/vi_gold.jsonl`:440
```json
{"scenario_id": "TRAIN-674527BC49C4", "split": "train", "language": "vi", "utterance": "Sáng mai nhờ gọi báo thức cho phòng mình.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {}, "concept_key": null, "frame_id": null, "source_family": "service_contract", "source_id": "TOOL-08-VI-REQUEST", "gold_status": "GOLD", "training_priority": "primary", "targets": {"expected_service_id": "service.wake_up_calls", "expected_department_id": "FO_CONCIERGE", "interaction_type": "request", "expected_category": "front_desk", "expected_operating_hours": null, "expected_contact_extension": "0", "label_source": "canonical_service_catalog"}}
```

`datasets/training/agent/vi_gold.jsonl`:553
```json
{"scenario_id": "TRAIN-HUM-410C2241F8BB", "split": "train", "language": "vi", "utterance": "Nhờ wake-up call lúc 5 giờ sáng mai giúp mình với.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": "FRAME-WAKE_UP", "source_family": "web_research_humanized", "source_id": "WEB-HUMANIZED-VI-2026-10", "gold_status": "GOLD", "training_priority": "support", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0, "humanization_method": "web_observed_register_paraphrase", "humanization_batch": "2026-10-04"}
```

`datasets/training/agent/vi_gold.jsonl`:585
```json
{"scenario_id": "TRAIN-PH1-DF54406BA61E", "split": "train", "language": "vi", "utterance": "Mai mình có chuyến sớm, 5 giờ gọi báo thức giúp mình nhé.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": "FRAME-WAKE_UP", "source_family": "independent_generation", "source_id": "PHASE1-VI-NATURAL-CURATED-2026-10", "gold_status": "GOLD", "training_priority": "primary", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/training/agent/multilingual_support.jsonl`:174
```json
{"scenario_id": "TRAIN-C8D5BCBCD097", "split": "train", "language": "en", "utterance": "Please arrange a wake-up call for five tomorrow morning.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": "FRAME-WAKE_UP", "source_family": "semantic_frame_multilingual_support", "source_id": "GOLD-WAKE_UP-EN", "gold_status": "GOLD", "training_priority": "support", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/training/agent/multilingual_support.jsonl`:175
```json
{"scenario_id": "TRAIN-9AFA45B395A0", "split": "train", "language": "ko", "utterance": "내일 아침 5시에 모닝콜 부탁드립니다.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": "FRAME-WAKE_UP", "source_family": "semantic_frame_multilingual_support", "source_id": "GOLD-WAKE_UP-KO", "gold_status": "GOLD", "training_priority": "support", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/training/agent/multilingual_support.jsonl`:176
```json
{"scenario_id": "TRAIN-7D61CF78A281", "split": "train", "language": "zh", "utterance": "请明早五点给我安排叫醒服务。", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": "FRAME-WAKE_UP", "source_family": "semantic_frame_multilingual_support", "source_id": "GOLD-WAKE_UP-ZH", "gold_status": "GOLD", "training_priority": "support", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/training/agent/multilingual_support.jsonl`:225
```json
{"scenario_id": "TRAIN-MGUEST-D2BB5E8B4E7A", "split": "train", "language": "en", "utterance": "I've got an early flight tomorrow. Could you give my room a wake-up call at five? I'm worried I'll sleep through my alarm.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": "FRAME-WAKE_UP", "source_family": "simulated_realistic_multilingual", "source_id": "PHASE1-MULTILINGUAL-SIMULATED-GUEST-PERSONAS-2026-10-05", "gold_status": "GOLD", "training_priority": "support", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/training/agent/multilingual_support.jsonl`:226
```json
{"scenario_id": "TRAIN-MGUEST-CE6BEE186C46", "split": "train", "language": "ko", "utterance": "내일 아침 비행기가 일러서요. 혹시 5시에 모닝콜 부탁드려도 될까요? 알람을 못 들을까 봐 걱정돼요.", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": "FRAME-WAKE_UP", "source_family": "simulated_realistic_multilingual", "source_id": "PHASE1-MULTILINGUAL-SIMULATED-GUEST-PERSONAS-2026-10-05", "gold_status": "GOLD", "training_priority": "support", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/training/agent/multilingual_support.jsonl`:227
```json
{"scenario_id": "TRAIN-MGUEST-4131B8F1815D", "split": "train", "language": "zh", "utterance": "我明天一早的航班，可以五点给房间打个叫醒电话吗？我怕自己睡过头。", "expected_route": "service", "service_code": "human_assistance", "expected_slots": {"preferred_time": "05:00"}, "concept_key": "wake_up", "frame_id": "FRAME-WAKE_UP", "source_family": "simulated_realistic_multilingual", "source_id": "PHASE1-MULTILINGUAL-SIMULATED-GUEST-PERSONAS-2026-10-05", "gold_status": "GOLD", "training_priority": "support", "approval_path": "staff", "expected_staff_review": true, "safety_class": "normal", "truth_boundary": "guest_intent_plus_domain_authority", "write_expectation": "fixed", "expected_business_writes_before_confirmation": 0}
```

`datasets/evaluation/challenges/natural.jsonl`:318
```json
{"case_id":"NAT-0295","classification":"naturalistic_challenge","truth_status":"synthetic_curated_behavioral_oracle","language":"en","utterance":"Could you set a 5 a.m. wake-up call for me tomorrow morning?","expected_route":"service","service_code":"human_assistance","expected_department_id":null,"expected_agent_action":"create_service_request","must_not_5xx":true,"must_not_claim_completed":true,"origin":"natural_challenge_curated_2026_10"}
```
