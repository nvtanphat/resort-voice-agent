"""Compile locale-native Furama knowledge documents from canonical facts.

The approved editorial Markdown remains the human-review source. Runtime RAG uses
one language-specific document per entity so non-English rows are never clones of
an English body merely carrying a different language tag.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("CONCIERGE_CURATION_ROOT", Path(__file__).resolve().parents[2])).resolve()
import sys
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import ALIASES, ENTITIES, FACTS, dataset_path

APPROVED = ROOT / "knowledge/approved/furama"
OUTPUT = ROOT / "knowledge/compiled/furama"
FACTS = dataset_path(FACTS, ROOT / "datasets")
ENTITIES = dataset_path(ENTITIES, ROOT / "datasets")
ALIASES = dataset_path(ALIASES, ROOT / "datasets")
CONTEXT_LABELS_PATH = dataset_path("knowledge/canonical/context_labels.json", ROOT / "datasets")
ENTITY_LABELS_PATH = dataset_path("knowledge/canonical/entity_display_labels.json", ROOT / "datasets")
MAP_PATH = dataset_path("knowledge/canonical/map.json", ROOT / "datasets")
SERVICE_CATALOG_PATH = dataset_path("knowledge/canonical/service_catalog.json", ROOT / "datasets")
CONTACTS_PATH = dataset_path("knowledge/canonical/contacts.json", ROOT / "datasets")
DEPARTMENTS_PATH = dataset_path("knowledge/canonical/departments.json", ROOT / "datasets")
LANGUAGES = ("en", "vi", "ko", "zh")

HEADINGS = {
    "en": {"facts": "Verified facts", "note": "Release note", "note_text": "Structured facts below are derived from the approved Furama canonical fact layer and retain source provenance through the source fact identifiers."},
    "vi": {"facts": "Thông tin đã xác minh", "note": "Ghi chú bản phát hành", "note_text": "Các thông tin có cấu trúc dưới đây được tạo từ lớp dữ kiện chuẩn Furama đã phê duyệt và giữ liên kết nguồn qua mã dữ kiện."},
    "ko": {"facts": "검증된 정보", "note": "릴리스 참고", "note_text": "아래 구조화 정보는 승인된 푸라마 표준 사실 계층에서 생성되며 사실 ID를 통해 출처 연결을 유지합니다."},
    "zh": {"facts": "已核实信息", "note": "发布说明", "note_text": "以下结构化信息来自已批准的富丽华标准事实层，并通过事实编号保留来源关联。"},
}

TEXT_VALUES = {
    "Marble bathroom, private balcony/terrace": {
        "vi": "Phòng tắm lát đá cẩm thạch, ban công hoặc sân hiên riêng",
        "ko": "대리석 욕실, 전용 발코니 또는 테라스",
        "zh": "大理石浴室，私人阳台或露台",
    },
    "Spacious sitting area, polished timber floor": {"vi": "Khu vực tiếp khách rộng rãi, sàn gỗ đánh bóng", "ko": "넓은 휴식 공간과 광택 목재 바닥", "zh": "宽敞休息区与抛光木地板"},
    "Overlooking tropical garden and landscaped pool": {"vi": "Nhìn ra vườn nhiệt đới và hồ bơi cảnh quan", "ko": "열대 정원과 조경 수영장 전망", "zh": "俯瞰热带花园与景观泳池"},
    "Direct view of Danang beach coastline": {"vi": "Tầm nhìn trực diện ra bờ biển Đà Nẵng", "ko": "다낭 해변 해안선 정면 전망", "zh": "直面岘港海岸景观"},
    "Contemporary L-shaped lounge and sea views": {"vi": "Khu lounge chữ L hiện đại và tầm nhìn biển", "ko": "현대적인 L자형 라운지와 바다 전망", "zh": "现代L形休息区与海景"},
    "Separate living room, two balconies, French colonial decor": {"vi": "Phòng khách riêng, hai ban công, phong cách thuộc địa Pháp", "ko": "분리형 거실, 발코니 2개, 프랑스 식민지풍 인테리어", "zh": "独立客厅、两个阳台、法式殖民风装饰"},
    "Luxury master bedroom, grand dining and lounge area": {"vi": "Phòng ngủ chính sang trọng, khu ăn uống và lounge rộng", "ko": "고급 마스터 침실과 넓은 다이닝·라운지 공간", "zh": "豪华主卧与宽敞餐饮、休息区"},
    "2 bottles daily": {"vi": "2 chai mỗi ngày", "ko": "매일 2병", "zh": "每日2瓶"},
    "Yukata/Cotton bathrobes in wardrobe": {"vi": "Áo choàng Yukata/áo choàng cotton trong tủ", "ko": "옷장 내 유카타/면 목욕가운", "zh": "衣柜内提供浴衣/棉质浴袍"},
    "King or Twin": {"vi": "Giường King hoặc Twin", "ko": "킹 또는 트윈 침대", "zh": "特大床或双床"},
    "King": {"vi": "Giường King", "ko": "킹 침대", "zh": "特大床"},
    "Garden View": {"vi": "Hướng vườn", "ko": "정원 전망", "zh": "花园景观"},
    "Tropical Garden View": {"vi": "Hướng vườn nhiệt đới", "ko": "열대 정원 전망", "zh": "热带花园景观"},
    "Lagoon Pool View": {"vi": "Hướng hồ Lagoon", "ko": "라군 수영장 전망", "zh": "泻湖泳池景观"},
    "Bac My An Beach Ocean View": {"vi": "Hướng biển Bãi Bắc Mỹ An", "ko": "박미안 해변 오션뷰", "zh": "北美安海滩海景"},
    "Ocean View": {"vi": "Hướng biển", "ko": "오션뷰", "zh": "海景"},
    "Panoramic Ocean View": {"vi": "Toàn cảnh biển", "ko": "파노라마 오션뷰", "zh": "全景海景"},
    "Panoramic Sea View": {"vi": "Toàn cảnh biển", "ko": "파노라마 바다 전망", "zh": "全景海景"},
    "Complimentary luggage storage": {"vi": "Giữ hành lý miễn phí", "ko": "무료 수하물 보관", "zh": "免费行李寄存"},
    "24/7 automated & personal wake-up calls": {"vi": "Dịch vụ báo thức tự động và hỗ trợ 24/7", "ko": "24시간 자동 및 직원 지원 모닝콜", "zh": "24小时自动及人工叫醒服务"},
    "Foreign currency exchange at Front Desk": {"vi": "Đổi ngoại tệ tại quầy Lễ tân", "ko": "프런트 데스크 외화 환전", "zh": "前台提供外币兑换"},
    "24/7 Medical Centre & First Aid": {"vi": "Trung tâm y tế và sơ cứu 24/7", "ko": "24시간 의료센터 및 응급처치", "zh": "24小时医疗中心与急救"},
    "Complimentary parking for resident guests": {"vi": "Đỗ xe miễn phí cho khách lưu trú", "ko": "투숙객 무료 주차", "zh": "住店客人免费停车"},
    "Fresh bath towels provided by Housekeeping": {"vi": "Housekeeping cung cấp khăn tắm sạch", "ko": "하우스키핑에서 깨끗한 목욕 수건 제공", "zh": "客房服务提供干净浴巾"},
}

LABELS = {
    "opening_hours": {"en": "Opening hours", "vi": "Giờ phục vụ", "ko": "운영 시간", "zh": "营业时间"},
    "service_window": {"en": "Service window", "vi": "Khung giờ phục vụ", "ko": "서비스 시간", "zh": "服务时段"},
    "activity_schedule": {"en": "Schedule", "vi": "Lịch hoạt động", "ko": "일정", "zh": "活动时间"},
    "price_vnd": {"en": "Price", "vi": "Giá", "ko": "가격", "zh": "价格"},
    "area_sqm": {"en": "Area", "vi": "Diện tích", "ko": "면적", "zh": "面积"},
    "balcony_area_sqm": {"en": "Balcony area", "vi": "Diện tích ban công", "ko": "발코니 면적", "zh": "阳台面积"},
    "height_m": {"en": "Ceiling height", "vi": "Chiều cao trần", "ko": "천장 높이", "zh": "层高"},
    "capacity": {"en": "Capacity", "vi": "Sức chứa", "ko": "수용 인원", "zh": "容纳人数"},
    "phone": {"en": "Phone", "vi": "Điện thoại", "ko": "전화", "zh": "电话"},
    "extension": {"en": "Extension", "vi": "Số máy nhánh", "ko": "내선", "zh": "分机"},
    "email": {"en": "Email", "vi": "Email", "ko": "이메일", "zh": "电子邮箱"},
    "address": {"en": "Address", "vi": "Địa chỉ", "ko": "주소", "zh": "地址"},
    "distance_km": {"en": "Distance", "vi": "Khoảng cách", "ko": "거리", "zh": "距离"},
    "travel_time_min": {"en": "Travel time", "vi": "Thời gian di chuyển", "ko": "이동 시간", "zh": "行程时间"},
    "amenity_feature": {"en": "Amenity", "vi": "Tiện nghi", "ko": "객실 특징", "zh": "设施特点"},
    "bed_type": {"en": "Bed type", "vi": "Loại giường", "ko": "침대 유형", "zh": "床型"},
    "view_type": {"en": "View", "vi": "Hướng nhìn", "ko": "전망", "zh": "景观"},
    "service_feature": {"en": "Service", "vi": "Dịch vụ", "ko": "서비스", "zh": "服务"},
    "cuisine_type": {"en": "Cuisine", "vi": "Ẩm thực", "ko": "요리", "zh": "菜系"},
    "policy_rule": {"en": "Policy", "vi": "Quy định", "ko": "정책", "zh": "政策"},
    "room_count": {"en": "Rooms", "vi": "Số phòng", "ko": "객실 수", "zh": "客房数量"},
    "villa_count": {"en": "Villas", "vi": "Số biệt thự", "ko": "빌라 수", "zh": "别墅数量"},
    "electricity_voltage": {"en": "Voltage", "vi": "Điện áp", "ko": "전압", "zh": "电压"},
    "electricity_frequency": {"en": "Frequency", "vi": "Tần số", "ko": "주파수", "zh": "频率"},
}


CONTEXT_LABELS = {
    "check_in_time": {"en": "check-in", "vi": "nhận phòng", "ko": "체크인", "zh": "入住"},
    "check_out_time": {"en": "check-out", "vi": "trả phòng", "ko": "체크아웃", "zh": "退房"},
    "voltage": {"en": "electricity", "vi": "điện áp", "ko": "전압", "zh": "电压"},
    # Opening-window qualifiers must stay visible in every locale. Besides being
    # useful to guests, these labels prevent breakfast/lunch/dinner windows for
    # the same venue from being mistaken for contradictory assertions.
    "daily_hours": {"en": "daily", "vi": "hằng ngày", "ko": "매일", "zh": "每日"},
    "butler_availability": {"en": "butler availability", "vi": "dịch vụ quản gia", "ko": "버틀러 이용", "zh": "管家服务"},
    "room_service_reservation_desk": {"en": "room-service reservation desk", "vi": "bàn đặt dịch vụ phòng", "ko": "룸서비스 예약 데스크", "zh": "客房服务预订台"},
    "daily_access_window": {"en": "daily access", "vi": "khung giờ sử dụng hằng ngày", "ko": "일일 이용 시간", "zh": "每日使用时段"},
    "friday_saturday_hours": {"en": "Friday and Saturday", "vi": "Thứ Sáu và Thứ Bảy", "ko": "금요일 및 토요일", "zh": "周五及周六"},
    "regular_hours": {"en": "regular hours", "vi": "giờ thông thường", "ko": "일반 운영 시간", "zh": "常规时段"},
    "a_la_carte_dinner": {"en": "à-la-carte dinner", "vi": "bữa tối gọi món", "ko": "알라카르트 디너", "zh": "单点晚餐"},
    "a_la_carte_lunch": {"en": "à-la-carte lunch", "vi": "bữa trưa gọi món", "ko": "알라카르트 런치", "zh": "单点午餐"},
    "breakfast_hours": {"en": "breakfast", "vi": "bữa sáng", "ko": "조식", "zh": "早餐"},
    "seafood_steak_buffet_dinner": {"en": "seafood & steak buffet dinner", "vi": "buffet tối hải sản & bít tết", "ko": "해산물·스테이크 디너 뷔페", "zh": "海鲜牛排自助晚餐"},
    "daily_dinner": {"en": "daily dinner", "vi": "bữa tối hằng ngày", "ko": "매일 저녁", "zh": "每日晚餐"},
    "daily_lunch": {"en": "daily lunch", "vi": "bữa trưa hằng ngày", "ko": "매일 점심", "zh": "每日午餐"},
    "dinner_hours": {"en": "dinner", "vi": "bữa tối", "ko": "저녁", "zh": "晚餐"},
    "late_night_dishes": {"en": "late-night dishes", "vi": "món ăn đêm", "ko": "심야 메뉴", "zh": "深夜餐食"},
    "operating_hours": {"en": "operating hours", "vi": "giờ hoạt động", "ko": "운영 시간", "zh": "营业时段"},
    "staffed_hours": {"en": "staffed hours", "vi": "giờ có nhân viên trực", "ko": "직원 상주 시간", "zh": "值班时段"},
    "taxi_availability": {"en": "taxi availability", "vi": "thời gian có taxi", "ko": "택시 이용 시간", "zh": "出租车服务时段"},
}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_context_labels(facts: list[dict]) -> dict[str, dict[str, object]]:
    """Load the data-owned labels and fail closed on any missing context.

    Context is part of a fact's identity. Silently omitting it creates false
    collisions such as several capacity values for the same venue. The build
    therefore requires every approved context to have a label in every locale.
    """
    payload = json.loads(CONTEXT_LABELS_PATH.read_text(encoding="utf-8"))
    labels = payload.get("labels") if isinstance(payload, dict) else None
    if not isinstance(labels, dict):
        raise ValueError(f"Invalid context label table: {CONTEXT_LABELS_PATH}")
    contexts = {str(fact.get("context") or "") for fact in facts if fact.get("context")}
    missing = sorted(
        f"{context}:{language}"
        for context in contexts
        for language in LANGUAGES
        if not isinstance(labels.get(context), dict)
        or not str(labels[context].get(language) or "").strip()
    )
    if missing:
        raise ValueError("Missing context labels: " + ", ".join(missing[:20]))
    untranslated = sorted(
        f"{context}:{language}"
        for context in contexts
        for language in LANGUAGES[1:]
        if labels[context].get(language) == labels[context].get("en")
        and not labels[context].get("proper_name", False)
    )
    if untranslated:
        raise ValueError(
            "Untranslated context labels require a proper_name=true annotation: "
            + ", ".join(untranslated[:20])
        )
    return {
        context: {
            "labels": {language: str(labels[context][language]).strip() for language in LANGUAGES},
            "redundant_with_label": bool(labels[context].get("redundant_with_label", False)),
        }
        for context in contexts
    }


def load_entity_labels() -> dict[str, dict[str, dict[str, str]]]:
    payload = json.loads(ENTITY_LABELS_PATH.read_text(encoding="utf-8"))
    result = {}
    for group in ("entity_types", "domains", "card_labels"):
        values = payload.get(group)
        if not isinstance(values, dict):
            raise ValueError(f"Invalid entity display label group: {group}")
        result[group] = {}
        for key, labels in values.items():
            if not isinstance(labels, dict) or any(not str(labels.get(lang) or "").strip() for lang in LANGUAGES):
                raise ValueError(f"Missing entity display labels: {group}/{key}")
            result[group][key] = {lang: str(labels[lang]).strip() for lang in LANGUAGES}
    return result


def _normalized_label(value: str) -> tuple[str, ...]:
    folded = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return tuple(re.findall(r"[a-z0-9]+", folded.casefold()))


def _context_is_redundant(context_spec: dict[str, object], fact_label: str,
                          context_label: str) -> bool:
    if bool(context_spec.get("redundant_with_label", False)):
        return True
    fact_tokens = set(_normalized_label(fact_label))
    context_tokens = set(_normalized_label(context_label))
    if not fact_tokens or not context_tokens:
        return False
    # A condition label that only repeats the fact label (possibly with a
    # qualifier such as "floor" or "standard") adds no disambiguating value.
    shorter, longer = sorted((fact_tokens, context_tokens), key=len)
    return len(shorter) >= 1 and shorter.issubset(longer) and len(longer - shorter) <= 1


def load_card_sources() -> dict[str, object]:
    """Load optional entity-card fields only from canonical source tables."""
    map_payload = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    locations: dict[str, dict] = {}
    for zone in map_payload.get("zones", []):
        for location in zone.get("locations", []):
            entity_id = str(location.get("entity_id") or "")
            if entity_id and entity_id not in locations:
                locations[entity_id] = {
                    "zone": str(zone.get("name") or "").strip(),
                    "location": str(location.get("name") or "").strip(),
                    "level": str(location.get("level") or "").strip(),
                }
    services: dict[str, list[dict]] = defaultdict(list)
    for service in json.loads(SERVICE_CATALOG_PATH.read_text(encoding="utf-8")):
        entity_id = str(service.get("entity_id") or "")
        if entity_id:
            services[entity_id].append(service)
    contacts = {}
    for contact in json.loads(CONTACTS_PATH.read_text(encoding="utf-8")):
        entity_id = str(contact.get("entity_id") or contact.get("contact_id") or "")
        if entity_id:
            contacts[entity_id] = contact
    departments = {}
    departments_by_contact = {}
    for department in json.loads(DEPARTMENTS_PATH.read_text(encoding="utf-8")):
        department_id = str(department.get("department_id") or "")
        if department_id:
            departments[department_id] = department
            fallback_contact = str(department.get("fallback_contact_id") or "")
            if fallback_contact:
                departments_by_contact[fallback_contact] = department
    return {
        "locations": locations,
        "services": services,
        "contacts": contacts,
        "departments": departments,
        "departments_by_contact": departments_by_contact,
    }


def text_value(fact: dict, language: str) -> str:
    raw = str(fact.get("raw_value", ""))
    if language == "en":
        return raw
    translated = TEXT_VALUES.get(raw, {}).get(language)
    if translated:
        return translated
    locale = str((fact.get("locale_support") or {}).get(language) or "").strip()
    # Keep truly localized canonical values when present; otherwise numeric/basic
    # types are rendered by templates below rather than relabeling English text.
    if language == "vi" and re.search(r"[ăâđêôơưáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", locale.casefold()):
        return locale
    if language == "ko" and re.search(r"[가-힣]", locale):
        return locale
    if language == "zh" and re.search(r"[\u4e00-\u9fff]", locale):
        return locale
    return raw


def fact_line(fact: dict, language: str, context_labels: dict[str, dict[str, object]], *, subject: str,
              domain_label: str) -> tuple[str, str]:
    kind = fact.get("fact_type")
    context = str(fact.get("context") or "")
    value = fact.get("normalized_value")
    label = LABELS.get(kind, {lang: kind for lang in LANGUAGES})[language]
    context_spec = context_labels[context]
    context_label = context_spec["labels"][language]

    if kind == "opening_hours" and isinstance(value, dict):
        shown = f"{value.get('start')}–{value.get('end')}"
    elif kind == "price_vnd":
        amount = f"{int(value):,} VND"
        tax_basis = str(fact.get("tax_basis") or "").strip()
        price_basis = str(fact.get("price_basis") or "").strip()
        if tax_basis == "++":
            amount += "++"
        elif tax_basis == "net":
            amount += " net"
        elif tax_basis == "inclusive":
            amount += {"en": " inclusive", "vi": " đã gồm phí/thuế", "ko": " 세금·서비스료 포함", "zh": " 已含税费"}[language]
        if price_basis == "per_guest":
            amount += {"en": " / guest", "vi": " / khách", "ko": " / 1인", "zh": " / 位"}[language]
        shown = amount
    elif kind == "area_sqm":
        shown = f"{value:g} m²" if isinstance(value, (int, float)) else f"{value} m²"
    elif kind == "height_m":
        shown = f"{value:g} m" if isinstance(value, (int, float)) else f"{value} m"
    elif kind == "capacity":
        suffix = {"en": "guests", "vi": "khách", "ko": "명", "zh": "人"}[language]
        shown = f"{value} {suffix}"
    elif kind in {"service_window", "activity_schedule"} and isinstance(value, dict):
        start = value.get("start")
        end = value.get("end")
        shown = f"{start}–{end}" if start and end else str(start or fact.get("raw_value") or value)
    elif kind == "travel_time_min":
        suffix = {"en": "minutes", "vi": "phút", "ko": "분", "zh": "分钟"}[language]
        shown = f"{value} {suffix}"
    elif kind in {"room_count", "villa_count", "electricity_voltage", "electricity_frequency"}:
        shown = str(value)
    elif kind == "balcony_area_sqm":
        shown = f"{value:g} m²" if isinstance(value, (int, float)) else f"{value} m²"
    elif kind == "extension":
        shown = str(value)
    elif kind == "distance_km":
        shown = f"{value:g} km" if isinstance(value, (int, float)) else f"{value} km"
    elif kind in {"amenity_feature", "bed_type", "view_type", "service_feature", "cuisine_type"}:
        shown = text_value(fact, language)
    elif kind in {"phone", "email", "address"}:
        localized = str((fact.get("locale_support") or {}).get(language) or "").strip()
        shown = localized if kind == "address" and localized else str(value)
    elif kind == "policy_rule":
        if context == "check_in_time":
            shown = str(value)
        elif context == "check_out_time":
            shown = str(value)
        elif context == "voltage":
            shown = str(value)
        else:
            shown = text_value(fact, language)
    else:
        shown = text_value(fact, language)

    # Price/service item contexts are useful search terms and exact identifiers;
    # keep them as secondary labels rather than pretending they are translated.
    suffix = (f" ({context_label})"
              if context_label and not _context_is_redundant(context_spec, label, context_label)
              else "")
    # Each child passage is a self-contained proposition, not a label/value
    # fragment. This lets citations and dense retrieval stand alone.
    rendered = f"- **{subject} — {label}**: {shown}{suffix}"
    context_text = f"{subject} · {domain_label} · {label}: {shown}{suffix}"
    return rendered, context_text


def entity_card_line(entity: dict, language: str, entity_labels: dict[str, dict[str, dict[str, str]]],
                     card_sources: dict[str, object], *, title: str, entity_type: str,
                     type_label: str, domain_label: str) -> tuple[str, dict[str, object]]:
    labels = entity_labels["card_labels"]
    segments = [
        f"{labels['entity'][language]}: {title}",
        f"{labels['type'][language]}: {type_label}",
        f"{labels['domain'][language]}: {domain_label}",
    ]
    source_files = {"knowledge/canonical/entities.jsonl"}

    location = card_sources["locations"].get(entity["entity_id"])
    if location:
        area = " / ".join(value for value in (location.get("zone"), location.get("location")) if value)
        if area:
            segments.append(f"{labels['area'][language]}: {area}")
        if location.get("level"):
            segments.append(f"{labels['level'][language]}: {location['level']}")
        source_files.add("knowledge/canonical/map.json")

    services = card_sources["services"].get(entity["entity_id"], [])
    service = services[0] if services else None
    contact = card_sources["contacts"].get(entity["entity_id"])
    department = None
    if service:
        department = card_sources["departments"].get(str(service.get("department_id") or ""))
        source_files.update({"knowledge/canonical/service_catalog.json"})
    if department is None and contact:
        department = card_sources["departments_by_contact"].get(entity["entity_id"])
    if department:
        segments.append(f"{labels['department'][language]}: {department.get('name', '')}")
        source_files.add("knowledge/canonical/departments.json")

    extensions = []
    if service and service.get("contact_extension"):
        extensions.append(str(service["contact_extension"]))
    if department:
        for key in ("internal_extension", "operator_extension"):
            value = str(department.get(key) or "").strip()
            if value and value not in extensions:
                extensions.append(value)
    if contact:
        for value in contact.get("internal_extensions") or []:
            value = str(value).strip()
            if value and value not in extensions:
                extensions.append(value)
    if extensions:
        segments.append(f"{labels['extension'][language]}: {', '.join(extensions)}")
    if contact and department is None:
        contact_label = str((contact.get("languages") or {}).get(language)
                            or contact.get("title") or "").strip()
        if contact_label:
            segments.append(f"{labels['contact'][language]}: {contact_label}")
        source_files.add("knowledge/canonical/contacts.json")

    hours = str((service or {}).get("operating_hours") or (department or {}).get("operating_hours") or "").strip()
    if hours:
        segments.append(f"{labels['hours'][language]}: {hours}")

    context_text = f"{title} · {domain_label} · " + "; ".join(segments)
    rendered = "- " + "; ".join(segments) + "."
    metadata = {
        "entity_id": str(entity["entity_id"]),
        "chunk_kind": "entity_card",
        "entity_card": True,
        "context_text": context_text,
        "source_files": sorted(source_files),
        "entity_type": entity_type,
    }
    return rendered, metadata


def compile_documents() -> dict[str, int]:
    entities = {row["entity_id"]: row for row in load_jsonl(ENTITIES)}
    facts_by_entity: dict[str, list[dict]] = defaultdict(list)
    approved_facts = [fact for fact in load_jsonl(FACTS)
                      if fact.get("publication_status", "approved") == "approved"]
    context_labels = load_context_labels(approved_facts)
    entity_labels = load_entity_labels()
    card_sources = load_card_sources()
    for fact in approved_facts:
        if fact.get("publication_status", "approved") != "approved":
            continue
        facts_by_entity[fact["entity_id"]].append(fact)
    aliases_payload = json.loads(ALIASES.read_text(encoding="utf-8"))
    aliases_by_entity = aliases_payload.get("aliases_by_entity", aliases_payload)

    source_docs = []
    for path in sorted(APPROVED.rglob("*.md")):
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---\n") or "\n---\n" not in raw:
            raise ValueError(f"Invalid approved Markdown front matter: {path}")
        header, body = raw[4:].split("\n---\n", 1)
        meta = yaml.safe_load(header)
        if not isinstance(meta, dict) or not meta.get("entity_id"):
            raise ValueError(f"Missing entity_id: {path}")
        source_docs.append((path, meta, body))

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    for lang in LANGUAGES:
        (OUTPUT / lang).mkdir(parents=True, exist_ok=True)

    written = 0
    fact_lines = 0
    entity_card_count = 0
    for path, meta, _body in source_docs:
        entity_id = str(meta["entity_id"])
        entity = entities.get(entity_id)
        if entity is None:
            raise ValueError(f"Unknown entity in approved doc: {entity_id}")
        if entity.get("publication_status", "approved") != "approved":
            continue
        facts = sorted(facts_by_entity.get(entity_id, []),
                       key=lambda fact: (str(fact.get("fact_type", "")),
                                         str(fact.get("context", "")),
                                         str(fact.get("canonical_fact_id", ""))))
        names = entity.get("names_by_locale") or {}
        aliases = aliases_by_entity.get(entity_id) or {}
        base_document_id = str(meta["document_id"])
        domain = str(meta.get("domain") or entity.get("domain") or "general")
        effective_from = min(
            (str(fact.get("valid_from") or fact.get("effective_from") or "2026-01-01")
             for fact in facts), default="2026-01-01")
        for lang in LANGUAGES:
            title = str(names.get(lang) or entity.get("name") or entity_id)
            entity_type = str(entity.get("entity_type") or "entity")
            type_label = entity_labels["entity_types"].get(entity_type, {}).get(lang, entity_type)
            domain_label = entity_labels["domains"].get(domain, {}).get(lang, domain)
            localized_lines: list[str] = []
            for fact in facts:
                rendered_fact, context_text = fact_line(
                    fact, lang, context_labels, subject=title, domain_label=domain_label)
                metadata = {
                    "canonical_fact_id": str(fact["canonical_fact_id"]),
                    "entity_id": entity_id,
                    "fact_type": str(fact["fact_type"]),
                    "context": str(fact["context"]),
                    "effective_from": str(fact.get("valid_from") or fact.get("effective_from") or "2026-01-01"),
                    "effective_to": (str(fact.get("valid_until") or fact.get("effective_to"))
                                     if (fact.get("valid_until") or fact.get("effective_to")) else None),
                    "context_text": context_text,
                    "domain_review": fact.get("domain_review") or {},
                }
                localized_lines.append(
                    f"<!-- fact_metadata: {json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))} --> "
                    f"{rendered_fact}"
                )
                fact_lines += 1
            card_metadata = None
            if not facts:
                rendered_card, card_metadata = entity_card_line(
                    entity, lang, entity_labels, card_sources, title=title,
                    entity_type=entity_type, type_label=type_label, domain_label=domain_label)
                localized_lines.append(
                    f"<!-- entity_metadata: {json.dumps(card_metadata, ensure_ascii=False, separators=(',', ':'))} --> "
                    f"{rendered_card}"
                )
                entity_card_count += 1
            front = {
                "property_id": "FURAMA_DANANG",
                "document_id": base_document_id,
                "entity_id": entity_id,
                "domain": domain,
                "language": lang,
                "title": title,
                "classification": "public",
                "effective_from": effective_from,
                "source_document_ids": meta.get("source_document_ids", []),
                "source_fact_ids": [fact["canonical_fact_id"] for fact in facts],
                "entity_type": entity_type,
                "entity_type_label": type_label,
                "entity_domain_label": domain_label,
                "entity_card": bool(card_metadata),
            }
            alias_terms = []
            language_aliases = aliases.get(lang, []) if isinstance(aliases, dict) else aliases
            for item in language_aliases:
                if isinstance(item, str) and 1 < len(item) <= 80 and item.casefold() not in {x.casefold() for x in alias_terms}:
                    alias_terms.append(item)
                if len(alias_terms) == 12:
                    break
            front["search_aliases"] = alias_terms
            body = [
                f"# {title} {{#entity-title}}",
                "",
                (f"## {entity_labels['card_labels']['entity'][lang]} {{#entity-profile}}"
                 if card_metadata else f"## {HEADINGS[lang]['facts']} {{#verified-facts}}"),
                *localized_lines,
                "",
            ]
            rendered = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n" + "\n".join(body)
            out = OUTPUT / lang / path.name
            out.write_text(rendered, encoding="utf-8")
            written += 1
    return {"source_documents": len(source_docs), "entity_documents": written // len(LANGUAGES),
            "entity_cards": entity_card_count // len(LANGUAGES), "compiled_documents": written,
            "fact_renderings": fact_lines}


if __name__ == "__main__":
    print(json.dumps(compile_documents(), ensure_ascii=False, sort_keys=True))
