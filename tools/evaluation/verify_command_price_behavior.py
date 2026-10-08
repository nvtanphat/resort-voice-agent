"""Run the real command model, price fallback, and query-cache timing checks."""
from __future__ import annotations

import hashlib
import json
import re
import statistics
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from concierge_kiosk.agent.understanding.commands import model_commands  # noqa: E402
from concierge_kiosk.agent.understanding.fast_router import FastRouter, TurnContext  # noqa: E402
from concierge_kiosk.agent.understanding.service_selector import (  # noqa: E402
    ServiceSelector,
    load_command_examples,
)
from concierge_kiosk.core.dataset_layout import (  # noqa: E402
    SERVICE_CATALOG,
    TRAIN_AGENT_CANDIDATES,
    TRAIN_AGENT_MULTILINGUAL,
    TRAIN_AGENT_VI_GOLD,
    dataset_path,
)
from concierge_kiosk.core.domain_profile import nlu_policy  # noqa: E402
from concierge_kiosk.domain.service_registry import ACTION_REQUEST_KINDS  # noqa: E402
from concierge_kiosk.rag.embedding.local import LocalEmbedder  # noqa: E402


MODEL = "qwen2.5:3b"
BASE_URL = "http://127.0.0.1:11434"
EMBEDDING_MODEL = "ollama://bge-m3"
EMBEDDING_MANIFEST = ROOT / "models" / "embeddings" / "bge-m3.ollama.manifest.json"
REPORT = ROOT / "reports" / "nlu" / "command-price-real-behavior.json"

COMMAND_CASES = (
    ("vi", "Xóa giúp phiếu dịch vụ đã chuyển cho bộ phận rồi.", "Cancel"),
    ("vi", "Dời yêu cầu đang xử lý sang 10 giờ 15 nhé.", "Modify"),
    ("vi", "Phiếu tôi gửi cho lễ tân tiến triển tới đâu?", "AskStatus"),
    ("en", "Please withdraw the service ticket I sent earlier.", "Cancel"),
    ("en", "Update the active booking to five guests.", "Modify"),
    ("en", "Can you tell me the progress of the ticket I submitted?", "AskStatus"),
    ("zh", "请撤回之前提交给工作人员的服务单。", "Cancel"),
    ("zh", "把正在处理的预订人数改成五位。", "Modify"),
    ("zh", "我提交的服务单现在处理到哪一步了？", "AskStatus"),
    ("ko", "직원에게 전달된 서비스 접수를 철회해 주세요.", "Cancel"),
    ("ko", "진행 중인 예약 인원을 다섯 명으로 바꿔 주세요.", "Modify"),
    ("ko", "제가 접수한 서비스 건은 지금 어디까지 처리됐나요?", "AskStatus"),
)

PRICE_CASES = (
    ("vi", "Chăm sóc chân sáu mươi phút tính phí thế nào?"),
    ("vi", "Đặt xe bảy chỗ đi sân bay hết khoảng bao nhiêu?"),
    ("en", "How much is a ninety-minute aromatherapy session?"),
    ("en", "Does the coastal day tour have an extra charge?"),
    ("zh", "九十分钟芳香护理的价格是多少？"),
    ("zh", "海岸一日游会另外收费吗？"),
    ("ko", "90분 아로마 관리는 비용이 얼마예요?"),
    ("ko", "해안 당일 투어는 추가 요금이 있나요?"),
)


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _dataset_utterances() -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for root in (ROOT / "datasets" / "training" / "agent", ROOT / "datasets" / "evaluation"):
        for path in root.rglob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                for key in ("utterance", "query"):
                    if isinstance(row.get(key), str):
                        result.append((_norm(row[key]), str(path.relative_to(ROOT))))
                for turn in row.get("turns") or ():
                    if not isinstance(turn, dict):
                        continue
                    for key in ("utterance", "query", "text"):
                        if isinstance(turn.get(key), str):
                            result.append((_norm(turn[key]), str(path.relative_to(ROOT))))
    return result


def _assert_unique_cases() -> dict:
    cases = [text for _language, text, *_rest in (*COMMAND_CASES, *PRICE_CASES)]
    normalized = [_norm(text) for text in cases]
    if len(set(normalized)) != len(normalized):
        raise AssertionError("real-behavior prompts contain duplicates")
    existing = _dataset_utterances()
    exact: list[dict] = []
    near: list[dict] = []
    for text, value in zip(cases, normalized):
        tokens = set(value.split())
        for known, path in existing:
            if value == known:
                exact.append({"query": text, "path": path})
            known_tokens = set(known.split())
            if len(tokens) >= 4 and known_tokens:
                score = len(tokens & known_tokens) / len(tokens | known_tokens)
                if score >= 0.85:
                    near.append({"query": text, "path": path, "jaccard": round(score, 4)})
    if exact or near:
        raise AssertionError(json.dumps({"exact": exact, "near": near}, ensure_ascii=False))
    return {"checked": len(cases), "exact": 0, "near_jaccard_0_85": 0}


def _reuse_calibration_vectors(selector: ServiceSelector) -> bool:
    key = json.dumps({
        "model": EMBEDDING_MODEL,
        "manifest": str(EMBEDDING_MANIFEST),
        "utterances": [example.utterance for example in selector.examples],
    }, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    path = ROOT / ".cache" / f"calibrate_{digest}.json"
    if not path.is_file():
        return False
    vectors = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(vectors, list) or len(vectors) != len(selector.examples):
        return False
    selector._example_vectors = tuple(vectors)
    return True


def _types(commands) -> list[str]:
    return [command.type for command in commands or ()]


def main() -> int:
    duplicate_check = _assert_unique_cases()
    examples = load_command_examples([
        dataset_path(TRAIN_AGENT_VI_GOLD),
        dataset_path(TRAIN_AGENT_MULTILINGUAL),
        dataset_path(TRAIN_AGENT_CANDIDATES),
    ])
    embedder = LocalEmbedder(EMBEDDING_MODEL, str(EMBEDDING_MANIFEST))
    selector = ServiceSelector(
        dataset_path(SERVICE_CATALOG), embedder, examples=examples,
        cache_dir=ROOT / ".cache",
    )
    reused_calibration_vectors = _reuse_calibration_vectors(selector)
    selector.warm()
    if reused_calibration_vectors:
        # Persist the combined catalog/example index for repeatable local probes.
        selector._save_cache()

    command_rows = []
    for language, query, expected in COMMAND_CASES:
        candidates, shots = selector.understand(
            query, language=language, enabled_request_kinds=ACTION_REQUEST_KINDS)
        commands = model_commands(
            query=query, language=language, base_url=BASE_URL, model=MODEL,
            enabled_request_kinds=ACTION_REQUEST_KINDS,
            service_candidates=candidates, examples=shots,
            timeout_seconds=10.0,
        )
        actual = _types(commands)
        command_rows.append({
            "language": language, "query": query, "expected": expected,
            "commands": [command.public() for command in commands or ()],
            "correct": actual == [expected],
        })

    policy = nlu_policy().service_selector
    fallback_rows = []
    for language, query in PRICE_CASES:
        commands = selector.fallback_commands(
            query, language=language, enabled_request_kinds=ACTION_REQUEST_KINDS,
            min_score=float(policy["fallback_min_score"]),
            min_margin=float(policy["fallback_min_margin"]),
        )
        public = list(commands or ())
        fallback_rows.append({
            "language": language, "query": query, "commands": public,
            "correct": not any(command.get("type") == "StartGoal" for command in public),
        })

    router = FastRouter(
        selector, min_score=float(policy["router_min_score"]),
        min_margin=float(policy["router_min_margin"]),
    )
    timing_query = "Could you outline the fee for a wellness treatment?"
    before_ms: list[float] = []
    after_ms: list[float] = []
    for _ in range(3):
        with selector._query_lock:
            selector._query_vectors.pop(timing_query, None)
        started = time.perf_counter()
        routed = router.route(timing_query, "en", TurnContext())
        with selector._query_lock:
            selector._query_vectors.pop(timing_query, None)
        selector.understand(
            timing_query, language="en", enabled_request_kinds=ACTION_REQUEST_KINDS)
        before_ms.append((time.perf_counter() - started) * 1000)
        if routed is not None:
            raise AssertionError("timing query must make the fast router abstain")

        with selector._query_lock:
            selector._query_vectors.pop(timing_query, None)
        started = time.perf_counter()
        routed = router.route(timing_query, "en", TurnContext())
        selector.understand(
            timing_query, language="en", enabled_request_kinds=ACTION_REQUEST_KINDS)
        after_ms.append((time.perf_counter() - started) * 1000)
        if routed is not None:
            raise AssertionError("timing query must make the fast router abstain")

    timing = {
        "query": timing_query,
        "runs": 3,
        "before_ms": [round(value, 1) for value in before_ms],
        "after_ms": [round(value, 1) for value in after_ms],
        "before_median_ms": round(statistics.median(before_ms), 1),
        "after_median_ms": round(statistics.median(after_ms), 1),
        "saved_median_ms": round(statistics.median(before_ms) - statistics.median(after_ms), 1),
    }
    report = {
        "model": MODEL,
        "embedding_model": EMBEDDING_MODEL,
        "duplicate_check": duplicate_check,
        "reused_calibration_vectors": reused_calibration_vectors,
        "commands": command_rows,
        "fallback_prices": fallback_rows,
        "timing": timing,
        "summary": {
            "commands_correct": sum(row["correct"] for row in command_rows),
            "commands_total": len(command_rows),
            "fallback_correct": sum(row["correct"] for row in fallback_rows),
            "fallback_total": len(fallback_rows),
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(row["correct"] for row in (*command_rows, *fallback_rows)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
