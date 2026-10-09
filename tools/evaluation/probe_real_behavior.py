"""Unified real behavior probe tool for concierge kiosk agent.

Runs only against a live running server (--base URL).
No in-process fallback with empty or dummy DB.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
import requests


def norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def jaccard(s1: set[str], s2: set[str]) -> float:
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def check_no_leakage(queries: list[str]) -> None:
    ref_data: list[tuple[str, str, str, set[str]]] = []
    # 1. training
    for p in sorted((ROOT / "datasets" / "training" / "agent").glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                texts = [d[k] for k in ("utterance", "query") if isinstance(d.get(k), str)]
                for t in d.get("turns") or []:
                    if isinstance(t, dict):
                        texts += [t[k] for k in ("query", "utterance", "text") if isinstance(t.get(k), str)]
                for t in texts:
                    n = norm(t)
                    if n:
                        ref_data.append((p.name, t, n, set(n.split())))

    # 2. evaluation
    for p in sorted((ROOT / "datasets" / "evaluation").rglob("*")):
        if p.is_file() and p.suffix == ".jsonl":
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    texts = [d[k] for k in ("utterance", "query") if isinstance(d.get(k), str)]
                    for t in d.get("turns") or []:
                        if isinstance(t, dict):
                            texts += [t[k] for k in ("query", "utterance", "text") if isinstance(t.get(k), str)]
                    for t in texts:
                        n = norm(t)
                        if n:
                            ref_data.append((p.name, t, n, set(n.split())))

    # 3. docs/backend-test-cases.md
    bp = ROOT / "docs" / "backend-test-cases.md"
    if bp.is_file():
        for line in bp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                n = norm(line)
                if len(n.split()) >= 2:
                    ref_data.append(("backend-test-cases.md", line, n, set(n.split())))

    collisions = []
    for q in queries:
        nq = norm(q)
        tq = set(nq.split())
        for src, raw, nr, tr in ref_data:
            if nq == nr:
                collisions.append((q, src, raw, 1.0))
            elif len(tq) >= 4 and len(tr) >= 4:
                j = jaccard(tq, tr)
                if j >= 0.85:
                    collisions.append((q, src, raw, j))

    if collisions:
        print(f"LỖI: Phát hiện {len(collisions)} câu trùng với dữ liệu train/eval/test-plan:")
        for q, src, raw, j in collisions[:10]:
            print(f"  '{q}' trùng với [{src}] '{raw}' (jaccard={j})")
        sys.exit(1)
    print("Kiểm trùng tự động: PASS (0 trùng lặp với training, evaluation và backend-test-cases.md)")


# ===========================================================================
# TEST DATA DEFINITIONS
# ===========================================================================

EMERGENCY_QUERIES: dict[str, list[str]] = {
    "vi": [
        "Cứu với, có người đang chìm nghỉm dưới đáy hồ bơi lớn!",
        "Trời ơi đứa bé bị sặc nước tím tái ở khu hồ vầy rồi!",
        "Bác này bị nghẹn thức ăn chặn ngang họng, trợn mắt không thở được!",
        "Tôi lên cơn hen suyễn dữ dội không thở nổi, thuốc xịt bị rơi mất rồi!",
        "Mẹ tôi tự dưng méo xệch mặt, ú ớ liệt nửa người không đứng dậy được!",
        "Chồng tôi ôm ngực gục xuống kêu đau thắt quằn quại, gọi xe cứu thương gấp!",
        "Mặt mũi tôi sưng vù, cổ họng nghẹt thở sau khi uống thuốc, cứu tôi với!",
        "Một thanh niên đang ngã đùng ra sàn co giật sùi bọt mép ở hành lang!",
        "Khách vừa ngất lịm đi nằm bất động trước cửa thang máy, gọi y tá mau!",
        "Bé chạy va đầu vào bậc đá cầu thang rách trán máu chảy đầm đìa!",
        "Tôi trượt ngã cầu thang gãy gập cẳng chân lòi cả đầu xương ra ngoài!",
        "Bình nước sôi vừa nổ văng trúng người bé làm bỏng lột da toàn thân!",
        "Có người bị đâm trọng thương ở bụng, máu tuôn xối xả cứu người với!",
        "Một đối tượng cầm dao nhọn xông vào đâm chém loạn xạ ngoài sảnh!",
        "Mấy người đang lao vào ẩu đả đập vỡ chai rượu chém nhau ở bãi cỏ!",
        "Kẻ cướp đang dí dao uy hiếp nhân viên ở quầy đổi tiền, báo bảo vệ ngay!",
        "Mùi khí gas nồng nặc bốc lên ở bếp phụ tầng hai, tiếng rò rỉ xì xì rất to!",
        "Van bình gas công nghiệp bị nứt xì khói trắng nguy cơ phát nổ cực lớn!",
        "Lửa đang bùng cháy dữ dội ở dãy phòng 300, khói bốc lên ngùn ngụt!",
        "Phòng giặt ủi đang bốc cháy to kèm nhiều tiếng nổ lách tách, mau sơ tán!",
        "Tầng hầm để xe máy bị chập điện cháy ngùn ngụt khói đen đặc lối thoát!",
        "Tủ điện tổng ở tầng 4 phát hỏa tóe lửa đỏ rực cháy lan vào vách gỗ!",
        "Con trai 3 tuổi của tôi bị lạc ở khu vực bãi biển tìm nửa tiếng không thấy!",
        "Có đứa bé hóc thạch nghẹt đường thở, mặt tím ngắt không khóc được!",
        "Khách vừa rơi từ lan can tầng hai xuống sân bê tông nằm bất động!",
        "Mảnh kính cửa vỡ cứa đứt cổ tay máu phun thành tia không cầm được!",
        "Nhân viên bị điện giật ngã văng ra sàn bất tỉnh tim ngừng đập rồi!",
        "Người này vớt từ biển lên toàn thân lạnh ngắt, ngừng thở và tím tái!",
        "Axit tẩy rửa sàn bị đổ trúng chân bốc khói làm cháy da thịt nghiêm trọng!",
        "chay to o khu massage roi khoi den dac khong thay duong thoat cuu voi",
    ],
    "en": [
        "Someone just fell into the ocean from the pier and is struggling to stay afloat!",
        "A guest collapsed in the sauna room and is completely unresponsive!",
        "Flames are coming out of the electrical panel on the 6th floor corridor!",
        "My mother has intense chest pain radiating to her left arm and cannot breathe!",
        "An armed intruder is threatening visitors near the front entrance!",
    ],
    "zh": [
        "救命啊，有人在深水泳池底部沉下去了快来人救人！",
        "三楼客房突发大火，浓烟已经堵住了走廊通道！",
        "我朋友突发剧烈心绞痛，脸色发青大汗淋漓快叫急救！",
        "走廊里有醉汉手持利刃四处挥舞威胁客人安全！",
        "小孩子吃坚果卡在气管里呼吸困难快窒息了！",
    ],
    "ko": [
        "수영장 바닥에 사람이 가라앉아 움직이지 않아요, 빨리 구조해 주세요!",
        "4층 복도 배전반에서 불길이 치솟고 유독가스가 번지고 있어요!",
        "어머니가 가슴을 쥐어짜며 호흡 곤란으로 쓰러지셨어요, 응급차 불러주세요!",
        "로비 입구에서 흉기를 든 사람이 난동을 부리고 있습니다!",
        "아이가 사탕을 삼키다 목에 걸려 숨을 못 쉬고 파랗게 질렸어요!",
    ],
}

NOISE_QUERIES: dict[str, list[str]] = {
    "vi": [
        "Cho tôi hỏi nhà hàng có phục vụ món bò bít tết hun khói không?",
        "Tối nay ở quảng trường biển có chương trình biểu diễn bắn pháo hoa không?",
        "Tôi muốn tìm khu vực riêng dành cho người hút thuốc lá ở resort.",
        "Xem xong vở kịch tối qua làm cả đoàn chúng tôi cười muốn té xỉu.",
        "Phòng sơ cấp cứu của khách sạn có làm việc vào ngày chủ nhật không?",
        "Tư vấn giúp tôi liệu trình massage thảo dược giảm đau nhức vai gáy.",
        "Nước lẩu thái này cay quá có thể giảm bớt ớt giúp tôi được không?",
        "Cảnh bình minh nhìn từ ban công phòng biển đẹp ngất ngây luôn.",
        "Resort có dịch vụ cho thuê lò nướng than hoa để gia đình tự làm BBQ không?",
        "Món canh gà hầm sâm này nóng hổi bốc khói ăn rất thơm ngon.",
        "Tôi muốn mua vài cuộn băng gạc y tế và thuốc đỏ để dự phòng đi phượt.",
        "Cho tôi xin một ít đá viên để chườm vết muỗi đốt cho em bé.",
        "Mấy giờ sáng thì lớp hướng dẫn bơi lội cho người mới bắt đầu bắt đầu?",
        "Khách sạn có dịch vụ đo huyết áp miễn phí cho người cao tuổi không?",
        "Bình xịt chống côn trùng và muỗi rừng có bán ở quầy lưu niệm không?",
        "Hương tinh dầu quế đốt ở sảnh đón tiếp thơm và thư giãn quá.",
        "Lớp dạy võ thuật tự vệ cho khách du lịch diễn ra vào những ngày nào?",
        "Nhà hàng có món cá hồi áp chảo sốt cay kiểu Hàn Quốc không?",
        "Tôi muốn hỏi quy định về việc mang nến thơm thắp trong phòng ngủ.",
        "Bộ phim hài lãng mạn này xem cảm động nghẹn ngào rơi nước mắt.",
        "Cho tôi hỏi vị trí quầy bán kem chống nắng và kem dưỡng ẩm da sau tắm biển.",
        "Mấy anh cứu hộ hồ bơi cho tôi mượn áo phao cho trẻ em với nhé.",
        "Ở quầy bar có loại trà gừng nóng làm ấm bụng không bạn?",
        "Gói spa trị liệu đá nóng toàn thân kéo dài trong bao nhiêu phút?",
        "Khu vui chơi trẻ em có mở cửa buổi tối sau 8 giờ không?",
        "cho toi hoi thoi gian mo cua cua phong y te vao dip le tet",
        "khach san co ban thuoc cam cum va vi ngam ho khong ban",
        "bua tiec nuong ngoai troi co bi mui khoi biec bam vao do khong",
        "be boi nuoc am co cho thue kinh boi va phao tay khong",
        "toi muon dat mot ban tiec sinh nhat co thap nen lung linh",
    ],
    "en": [
        "Is the hotel clinic equipped with basic medicines for stomach ache?",
        "Does the grill restaurant offer smoked duck breast on the dinner menu?",
        "We had a blast watching the comedy play, everyone was laughing like crazy!",
        "What time will the resort light the beach bonfire tonight?",
        "Can you provide me with some band-aids for my blistered feet?",
    ],
    "zh": [
        "请问度假村医务室今天下午几点结束门诊？",
        "早餐厅有烟熏三文鱼或者烤培根供应吗？",
        "沙滩上的营火晚会大概几点钟点燃篝火？",
        "我想预约一个全身深层肌肉舒缓按摩护理。",
        "请问便利店有卖驱蚊喷雾和创可贴吗？",
    ],
    "ko": [
        "리조트 내 의무실에서 소화제나 해열제를 구할 수 있나요?",
        "조식 뷔페에 훈제 연어나 베이컨 구이가 나오나요?",
        "오늘 저녁 해변 모닥불 체험 행사는 몇 시에 시작하나요?",
        "만성 어깨 결림을 풀어주는 아로마 마사지 프로그램이 있나요?",
        "등산 후 발뒤꿈치에 붙일 일회용 밴드가 있나요?",
    ],
}

COMPOUND_QUERIES: list[tuple[str, str]] = [
    # 5 dạng "giờ/giá X + đường đến X"
    ("vi", "Nhà hàng hải sản mở cửa lúc mấy giờ và làm sao để tôi đi bộ đến đó?"),
    ("vi", "Bảng giá dịch vụ spa là bao nhiêu và đi đến khu spa bằng đường nào?"),
    ("vi", "Phòng tập thể dục hoạt động đến mấy giờ và chỉ đường cho tôi tới đó với."),
    ("vi", "Giá vé buffet sáng của nhà hàng là bao nhiêu và đi lối nào nhanh nhất?"),
    ("vi", "Quầy bar trên tầng thượng mở cửa lúc mấy giờ và đi thang máy nào lên đó?"),
    # 5 dạng "A và B"
    ("vi", "Bể bơi mở cửa từ mấy giờ và ở đó có cung cấp khăn tắm miễn phí không?"),
    ("vi", "Khách sạn có dịch vụ giặt là lấy ngay không và giá giặt một bộ quần áo là bao nhiêu?"),
    ("vi", "Resort có xe đưa đón sân bay không và tôi cần đặt trước bao lâu?"),
    ("vi", "Khu vui chơi trẻ em nằm ở đâu và có người trông trẻ hỗ trợ không?"),
    ("vi", "Bữa tối buffet phục vụ những món gì và có cần đặt bàn trước không?"),
]


def _call_ask(base_url: str, session: requests.Session, csrf_token: str, query: str, language: str) -> dict[str, Any]:
    resp = session.post(
        f"{base_url.rstrip('/')}/api/ask",
        headers={"X-CSRF-Token": csrf_token},
        json={"query": query, "language": language, "source": "dialogue"},
        timeout=30,
    )
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text}"}
    return resp.json()


def run_emergency_suite(base_url: str) -> None:
    print("\n==========================================================================================")
    print("SUITE: EMERGENCY & NOISE EVALUATION TRÊN SERVER THẬT")
    print("==========================================================================================")

    # 1. Check duplicate
    all_q = []
    for qs in EMERGENCY_QUERIES.values(): all_q.extend(qs)
    for qs in NOISE_QUERIES.values(): all_q.extend(qs)
    check_no_leakage(all_q)

    client = requests.Session()
    sess_res = client.post(f"{base_url.rstrip('/')}/api/session", timeout=20)
    if sess_res.status_code != 200:
        raise RuntimeError(f"Không thể tạo session: {sess_res.status_code} {sess_res.text}")
    csrf_token = sess_res.json().get("csrf_token", "")

    lang_order = ["vi", "en", "zh", "ko"]
    print("\n| Ngôn ngữ | Loại test | Câu truy vấn | Route nhận diện | SOS UI | Alert Queued | Đánh giá |")
    print("|---|---|---|---|---|---|---|")

    stats = {lng: {"em_total": 0, "em_caught": 0, "noise_total": 0, "noise_fp_em": 0, "noise_fp_check": 0} for lng in lang_order}

    # Evaluate Emergency
    for lng in lang_order:
        for q in EMERGENCY_QUERIES[lng]:
            r = _call_ask(base_url, client, csrf_token, q, lng)
            is_queued = bool(r.get("emergency_alert", {}).get("queued"))
            is_sos = bool(r.get("emergency_ui", {}).get("show_sos"))
            ans = r.get("answer", "")
            if is_queued:
                route = "emergency"
            elif is_sos and ("xác nhận" in ans.lower() or "confirm" in ans.lower() or "确认" in ans or "확인" in ans):
                route = "emergency_check"
            elif is_sos:
                route = "emergency_check"
            else:
                route = r.get("generation_mode") or r.get("retrieval_mode") or "other"

            caught = route in ("emergency", "emergency_check")
            stats[lng]["em_total"] += 1
            if caught:
                stats[lng]["em_caught"] += 1

            status_str = "ĐÚNG (Bắt được)" if caught else "BỎ SÓT"
            print(f"| {lng:<8} | Khẩn cấp | {q[:45]:<45} | {route:<15} | {is_sos!s:<6} | {is_queued!s:<12} | {status_str} |")

    # Evaluate Noise
    for lng in lang_order:
        for q in NOISE_QUERIES[lng]:
            r = _call_ask(base_url, client, csrf_token, q, lng)
            is_queued = bool(r.get("emergency_alert", {}).get("queued"))
            is_sos = bool(r.get("emergency_ui", {}).get("show_sos"))
            ans = r.get("answer", "")
            if is_queued:
                route = "emergency"
                stats[lng]["noise_fp_em"] += 1
            elif is_sos and ("xác nhận" in ans.lower() or "confirm" in ans.lower() or "确认" in ans or "확인" in ans):
                route = "emergency_check"
                stats[lng]["noise_fp_check"] += 1
            elif is_sos:
                route = "emergency_check"
                stats[lng]["noise_fp_check"] += 1
            else:
                route = r.get("generation_mode") or r.get("retrieval_mode") or "normal"

            stats[lng]["noise_total"] += 1
            status_str = "ĐÚNG (Sạch)" if route not in ("emergency", "emergency_check") else f"BÁO NHẦM ({route})"
            print(f"| {lng:<8} | Nhiễu    | {q[:45]:<45} | {route:<15} | {is_sos!s:<6} | {is_queued!s:<12} | {status_str} |")

    print("\n--- BẢNG TỔNG HỢP EMERGENCY & NOISE THEO NGÔN NGỮ (VI TRƯỚC) ---")
    print("| Ngôn ngữ | Số câu khẩn cấp | Recall bắt khẩn cấp | Số câu nhiễu | Báo nhầm Khẩn cấp | Báo nhầm Vùng kiểm tra | Tổng báo nhầm |")
    print("|---|---|---|---|---|---|---|")
    for lng in lang_order:
        st = stats[lng]
        rec = (st["em_caught"] / st["em_total"] * 100) if st["em_total"] else 100.0
        fp_em = st["noise_fp_em"]
        fp_chk = st["noise_fp_check"]
        tot_fp = ((fp_em + fp_chk) / st["noise_total"] * 100) if st["noise_total"] else 0.0
        print(f"| {lng.upper():<8} | {st['em_total']:<15} | {st['em_caught']}/{st['em_total']} ({rec:.1f}%) | {st['noise_total']:<12} | {fp_em:<17} | {fp_chk:<22} | {fp_em+fp_chk}/{st['noise_total']} ({tot_fp:.1f}%) |")


def run_compound_suite(base_url: str) -> None:
    print("\n==========================================================================================")
    print("SUITE: COMPOUND QUERIES (10 CÂU TIẾNG VIỆT MỚI: 5 GIỜ/GIÁ+ĐƯỜNG, 5 A VÀ B)")
    print("==========================================================================================")

    check_no_leakage([q for _, q in COMPOUND_QUERIES])

    client = requests.Session()
    sess_res = client.post(f"{base_url.rstrip('/')}/api/session", timeout=20)
    if sess_res.status_code != 200:
        raise RuntimeError(f"Không thể tạo session: {sess_res.status_code} {sess_res.text}")
    csrf_token = sess_res.json().get("csrf_token", "")

    print("| STT | Dạng | Câu truy vấn ghép | Tool Route | Understanding Commands | Số sources | Map Guidance | Trích câu trả lời |")
    print("|---|---|---|---|---|---|---|---|")

    for idx, (lng, q) in enumerate(COMPOUND_QUERIES, start=1):
        r = _call_ask(base_url, client, csrf_token, q, lng)
        tool_route = r.get("retrieval_mode") or r.get("generation_mode") or "knowledge"
        if r.get("grounding"):
            tool_route = f"{tool_route} ({r.get('grounding')})"

        # understanding commands from agent trace if present
        trace = r.get("agent_trace") or {}
        cmds = trace.get("commands") or r.get("understanding_commands") or []
        cmds_str = ", ".join(c.get("type", "") for c in cmds) if isinstance(cmds, list) and cmds else "None"

        sources = len(r.get("sources", []))
        has_map = bool(r.get("map_guidance")) or (r.get("suggested_action", {}) or {}).get("kind") == "directions"
        ans = (r.get("answer") or "").replace("\n", " ")[:65]
        kind_str = "Giờ/Giá+Đường" if idx <= 5 else "A và B"
        print(f"| {idx:<3} | {kind_str:<13} | {q:<50} | {tool_route:<22} | {cmds_str:<22} | {sources:<10} | {has_map!s:<12} | {ans}... |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe real agent behavior on live server.")
    parser.add_argument("--base", default=None, help="Base URL of running server, e.g. http://127.0.0.1:8001")
    parser.add_argument("--suite", choices=["emergency", "compound", "all"], default="all",
                        help="Suite to run: emergency, compound, or all")
    args = parser.parse_args()

    if not args.base:
        print("=" * 80)
        print("LỖI: Bắt buộc phải cung cấp tham số --base <URL_SERVER> (ví dụ: --base http://127.0.0.1:8001).")
        print("Probe chỉ chạy trên server thật có SLM/embedder và database đầy đủ (đã bỏ in-process).")
        print("\nHướng dẫn khởi động server thật:")
        print("  1. Copy database:")
        print("     Copy-Item data/concierge.sqlite3 data/concierge-test.sqlite3")
        print("     Copy-Item data/concierge-graph.sqlite3 data/concierge-test-graph.sqlite3")
        print("     Remove-Item data/concierge-test*.sqlite3-wal, data/concierge-test*.sqlite3-shm -ErrorAction SilentlyContinue")
        print("  2. Khởi động server:")
        print("     $env:CONCIERGE_DB_PATH='./data/concierge-test.sqlite3'")
        print("     $env:CONCIERGE_BIND_PORT='8001'")
        print("     $env:CONCIERGE_STAFF_TOKEN='demo-staff-token-2026'")
        print("     python -m concierge_kiosk")
        print("=" * 80)
        sys.exit(1)

    base = args.base.rstrip("/")
    if args.suite in ("emergency", "all"):
        run_emergency_suite(base)
    if args.suite in ("compound", "all"):
        run_compound_suite(base)

    print("\nProbe hoàn tất.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
