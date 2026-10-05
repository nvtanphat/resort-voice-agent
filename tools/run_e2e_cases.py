"""End-to-End Evaluation Suite for Furama Resort Danang Concierge Kiosk.

Tests the 10 canonical E2E test cases:
1. Café Indochine mở mấy giờ?
2. Garden Superior có gì?
3. Danang Grand Ballroom chứa bao nhiêu người?
4. Tôi cần thêm khăn.
5. Tôi muốn dọn phòng.
6. Tôi muốn gọi room service.
7. Tôi muốn đặt spa.
8. Gọi taxi giúp tôi.
9. V-Senses Spa ở đâu?
10. Một câu hỏi không tồn tại trong dataset.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Remove tools/ directory from sys.path so tools/packaging does not shadow third-party packaging
script_dir = str(Path(__file__).resolve().parent)
while script_dir in sys.path:
    sys.path.remove(script_dir)
if 'packaging' in sys.modules and not hasattr(sys.modules['packaging'], '__file__'):
    del sys.modules['packaging']

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["CONCIERGE_ENV"] = "development"
os.environ["PYTHONIOENCODING"] = "utf-8"

from fastapi.testclient import TestClient
from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.main import create_app
from concierge_kiosk.agent.understanding.intent import suggest_service_request
from concierge_kiosk.agent.understanding.routing import classify_dialogue
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag import retrieve, RAGPolicy
from concierge_kiosk.agent.tools.navigation import map_guidance


def run_e2e_tests() -> bool:
    print("=" * 70)
    print("STARTING E2E EVALUATION FOR FURAMA RESORT DANANG")
    print("=" * 70)

    cfg = load_settings()
    print(f"Runtime Property ID:   {cfg.property_id}")
    print(f"Runtime Property Name: {cfg.property_name}")
    print(f"Database Path:         {cfg.db_path}")
    print(f"Map Release:           {cfg.map_release_path}")
    print(f"Planning Release:      {cfg.planning_release_path}")
    print(f"Property Profile:      {cfg.property_profile_path}")
    print("-" * 70)

    store = Store(cfg.db_path)
    policy = RAGPolicy()

    test_cases = [
        {
            "id": 1,
            "query": "Café Indochine mở mấy giờ?",
            "type": "rag",
            "expected_branch": "knowledge",
            "check": lambda res: (
                len(res.get("sources", [])) > 0
                and "indochine" in res.get("answer", "").lower()
            ),
            "desc": "Café Indochine operating hours & dining knowledge",
        },
        {
            "id": 2,
            "query": "Garden Superior có gì?",
            "type": "rag",
            "expected_branch": "knowledge",
            "check": lambda res: (
                len(res.get("sources", [])) > 0
                and "garden superior" in str(res).lower()
            ),
            "desc": "Garden Superior room amenities & description",
        },
        {
            "id": 3,
            "query": "Danang Grand Ballroom chứa bao nhiêu người?",
            "type": "rag",
            "expected_branch": "knowledge",
            "check": lambda res: (
                len(res.get("sources", [])) > 0
                and "grand ballroom" in str(res).lower()
            ),
            "desc": "Danang Grand Ballroom meeting capacity & details",
        },
        {
            "id": 4,
            "query": "Tôi cần thêm khăn.",
            "type": "service",
            "expected_kind": "facilities",
            "check": lambda act: act and act.get("kind") == "facilities",
            "desc": "Fresh bath towels replenishment service request",
        },
        {
            "id": 5,
            "query": "Tôi muốn dọn phòng.",
            "type": "service",
            "expected_kind": "housekeeping",
            "check": lambda act: act and act.get("kind") == "housekeeping",
            "desc": "Housekeeping room cleaning service request",
        },
        {
            "id": 6,
            "query": "Tôi muốn gọi room service.",
            "type": "service",
            "expected_kind": "dining",
            "check": lambda act: act and act.get("kind") == "dining",
            "desc": "In-room dining (room service) order request",
        },
        {
            "id": 7,
            "query": "Tôi muốn đặt spa.",
            "type": "service",
            "expected_kind": "facilities",
            "check": lambda act: act and act.get("kind") == "facilities",
            "desc": "V-Senses Spa appointment booking request",
        },
        {
            "id": 8,
            "query": "Gọi taxi giúp tôi.",
            "type": "service",
            "expected_kind": "human",
            "check": lambda act: act and act.get("kind") == "human",
            "desc": "Concierge taxi arrangement assistance request",
        },
        {
            "id": 9,
            "query": "V-Senses Spa ở đâu?",
            "type": "rag_or_nav",
            "check": lambda res: (
                ("v-senses" in str(res).lower() or "spa" in str(res).lower())
            ),
            "desc": "V-Senses Spa location & navigation wayfinding",
        },
        {
            "id": 10,
            "query": "Một câu hỏi không tồn tại trong dataset.",
            "type": "abstention",
            "expected_branch": "knowledge",
            "check": lambda res: (
                len(res.get("sources", [])) == 0
                and "chưa tìm thấy thông tin" in res.get("answer", "").lower()
            ),
            "desc": "Clean abstention for out-of-dataset query",
        },
    ]

    all_passed = True
    passed_count = 0
    total_count = len(test_cases)

    # 1. Component Level Verification
    print("DIRECT COMPONENT EVALUATION")
    print("-" * 70)

    for case in test_cases:
        cid = case["id"]
        q = case["query"]
        ctype = case["type"]
        desc = case["desc"]

        print(f"[{cid}/10] Query: '{q}' ({desc})")

        if ctype in {"rag", "abstention"}:
            route = classify_dialogue(q, "vi")
            retrieval = retrieve(
                store,
                property_id=cfg.property_id,
                language="vi",
                query=q,
                policy=policy,
                effective_date="2026-10-01",
                mode="lexical",
            )
            res_dict = {
                "route": route.branch,
                "sources": retrieval.sources,
                "answer": retrieval.answer,
            }
            ok = case["check"](res_dict)
            if ok:
                passed_count += 1
                print(f"      Result: PASS (Sources: {len(retrieval.sources)}, Route: {route.branch})")
            else:
                all_passed = False
                print(f"      Result: FAIL (Sources: {len(retrieval.sources)}, Answer: {retrieval.answer[:60]}...)")

        elif ctype == "service":
            sugg = suggest_service_request(q, "vi")
            act_dict = {"kind": sugg.kind, "details": sugg.details} if sugg else None
            ok = case["check"](act_dict)
            if ok:
                passed_count += 1
                print(f"      Result: PASS (Suggestion Kind: {sugg.kind})")
            else:
                all_passed = False
                print(f"      Result: FAIL (Got: {sugg})")

        elif ctype == "rag_or_nav":
            # Test both retrieval and navigation map guidance
            retrieval = retrieve(
                store,
                property_id=cfg.property_id,
                language="vi",
                query=q,
                policy=policy,
                effective_date="2026-10-01",
                mode="lexical",
            )
            nav = map_guidance(
                store,
                path=cfg.map_release_path,
                expected_sha256=cfg.map_release_sha256,
                property_id=cfg.property_id,
                query=q,
                language="vi",
            )
            res_dict = {"retrieval": retrieval.sources, "nav": nav}
            ok = case["check"](res_dict) and (len(retrieval.sources) > 0 or nav.get("status") == "verified")
            if ok:
                passed_count += 1
                print(f"      Result: PASS (RAG sources: {len(retrieval.sources)}, Nav Status: {nav.get('status')})")
                if nav.get("status") == "verified":
                    print(f"      Steps: {nav.get('steps')}")
            else:
                all_passed = False
                print(f"      Result: FAIL (RAG: {len(retrieval.sources)}, Nav: {nav})")

    print("-" * 70)
    print(f"COMPONENT SCORE: {passed_count}/{total_count} PASSED")
    print("-" * 70)

    # 2. FastAPI End-to-End API Turn Verification
    print("FASTAPI INTEGRATION & GUEST TURNS EVALUATION")
    print("-" * 70)

    app = create_app(cfg)
    client = TestClient(app)

    # Health check
    h_resp = client.get("/healthz")
    assert h_resp.status_code == 200, f"Healthz failed: {h_resp.text}"
    print(f"GET /healthz -> 200 OK: {h_resp.json()}")

    # Config check
    c_resp = client.get("/api/config")
    assert c_resp.status_code == 200, f"Config failed: {c_resp.text}"
    c_data = c_resp.json()
    print(f"GET /api/config -> Property Name: '{c_data.get('property_name')}'")
    assert c_data.get("property_name") == "Furama Resort Danang", "Config property_name mismatch!"
    assert cfg.property_id == "FURAMA_DANANG", "Runtime property_id mismatch!"

    # Services catalog check
    s_resp = client.get("/api/services")
    assert s_resp.status_code == 200, f"Services failed: {s_resp.text}"
    s_items = s_resp.json().get("items", [])
    print(f"GET /api/services -> Loaded {len(s_items)} services")
    assert len(s_items) >= 18, f"Expected at least 18 services, got {len(s_items)}"

    # Map places check
    m_resp = client.get("/api/map/places?language=vi")
    # map/places requires guest session, let's create a session first
    sess_resp = client.post("/api/session")
    assert sess_resp.status_code == 200, f"Session create failed: {sess_resp.text}"
    sess_data = sess_resp.json()
    session_id = sess_data["session_id"]
    csrf_token = sess_data["csrf_token"]
    print(f"POST /api/session -> Session ID: {session_id[:8]}... (CSRF acquired)")

    m_resp = client.get(
        "/api/map/places?language=vi",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert m_resp.status_code == 200, f"Map places failed: {m_resp.text}"
    places = m_resp.json().get("places", [])
    print(f"GET /api/map/places -> Loaded {len(places)} places (verified)")
    assert len(places) > 0, "No places found in map release!"

    # Run API turn for each of the 10 queries
    api_passed = 0
    for case in test_cases:
        cid = case["id"]
        q = case["query"]
        ctype = case["type"]

        t_resp = client.post(
            "/api/ask",
            json={"query": q, "language": "vi"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert t_resp.status_code == 200, f"Turn {cid} failed: {t_resp.text}"
        t_data = t_resp.json()
        ans = t_data.get("answer", "")
        action = t_data.get("suggested_action")
        agent_action = t_data.get("agent_action")
        act_info = None
        if action:
            act_info = {"kind": action.get("kind"), "details": action.get("details")}
        elif agent_action:
            act_info = {"kind": agent_action.get("service_kind"), "details": str(agent_action.get("service_mode"))}
        sources = t_data.get("sources", [])

        if ctype in {"rag", "abstention"}:
            if case["check"]({"sources": sources, "answer": ans}):
                api_passed += 1
                print(f"API Turn [{cid}/10]: PASS ('{q}') -> {ans[:50]}...")
            else:
                print(f"API Turn [{cid}/10]: FAIL ('{q}') -> {ans[:50]}...")
        elif ctype == "service":
            if case["check"](act_info):
                api_passed += 1
                print(f"API Turn [{cid}/10]: PASS ('{q}') -> Action: {act_info.get('kind')} ({act_info.get('details')})")
            else:
                print(f"API Turn [{cid}/10]: FAIL ('{q}') -> Action: {act_info}")
        elif ctype == "rag_or_nav":
            if case["check"](t_data):
                api_passed += 1
                print(f"API Turn [{cid}/10]: PASS ('{q}') -> Answer / Map guidance verified")
            else:
                print(f"API Turn [{cid}/10]: FAIL ('{q}') -> {t_data}")

    print("-" * 70)
    print(f"API SCORE: {api_passed}/{total_count} PASSED")
    print("=" * 70)

    final_ok = (passed_count == total_count) and (api_passed == total_count)
    return final_ok


if __name__ == "__main__":
    success = run_e2e_tests()
    sys.exit(0 if success else 1)
