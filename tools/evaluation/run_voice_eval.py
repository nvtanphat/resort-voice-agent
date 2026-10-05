"""Run a consented voice-evaluation manifest through the real kiosk API.

The evaluator is deliberately black-box: it uses the public session, voice
WebSocket, ask, and speech endpoints instead of importing router internals.
It refuses missing audio/manifest rows and writes aggregate metrics only; raw
audio and full transcripts are never copied into the report.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import math
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from jsonschema import Draft202012Validator


LANGUAGES = frozenset({"vi", "en", "zh", "ko"})
ALLOWED_DISFLUENCY = frozenset({
    "none", "filler", "self_correction", "repetition", "hesitation", "noise", "out_of_scope",
})


def _tokens(value: str) -> list[str]:
    return re.findall(r"\w+", value.casefold(), flags=re.UNICODE)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = _tokens(reference), _tokens(hypothesis)
    previous = list(range(len(hyp) + 1))
    for index, token in enumerate(ref, 1):
        current = [index]
        for candidate, other in enumerate(hyp, 1):
            current.append(min(
                current[-1] + 1,
                previous[candidate] + 1,
                previous[candidate - 1] + (token != other),
            ))
        previous = current
    return previous[-1] / max(1, len(ref))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, math.ceil(len(values) * percentile) - 1))
    return round(values[index], 2)


def _json_error(status: int, raw: bytes) -> str:
    try:
        body = json.loads(raw.decode("utf-8"))
        return str(body.get("detail") or body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"HTTP {status}"


class HttpClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/") + "/"
        self.jar = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, *, body: object | None = None,
                headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(urljoin(self.base_url, path.lstrip("/")), data=payload, method=method)
        request.add_header("Accept", "application/json")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self.opener.open(request, timeout=75) as response:
                return response.status, response.read(), dict(response.headers.items())
        except HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers.items())
        except URLError as exc:
            raise RuntimeError(f"HTTP request failed: {exc.reason}") from exc

    def json(self, method: str, path: str, *, body: object | None = None,
             headers: dict[str, str] | None = None) -> dict:
        status, raw, _headers = self.request(method, path, body=body, headers=headers)
        if status < 200 or status >= 300:
            raise RuntimeError(_json_error(status, raw))
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid JSON response from {path}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"Unexpected response from {path}")
        return value

    def cookies(self) -> str:
        return "; ".join(f"{item.name}={item.value}" for item in self.jar)


def _websocket_url(base_url: str) -> tuple[str, str]:
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = urlunsplit((scheme, parsed.netloc, "/api/audio/stream", "", ""))
    return ws_url, base_url.rstrip("/")


def transcribe_websocket(client: HttpClient, audio: bytes, language: str,
                         turn_id: str, csrf: str, *, base_url: str,
                         chunk_bytes: int = 65536) -> tuple[str, dict, float, float]:
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise RuntimeError("websockets is required for the real voice evaluator") from exc
    ws_url, origin = _websocket_url(base_url)
    start = time.perf_counter()
    with connect(ws_url, origin=origin, proxy=None,
                 additional_headers={"Cookie": client.cookies()},
                 open_timeout=15, close_timeout=5, max_size=2_000_000) as socket:
        socket.send(json.dumps({
            "type": "start", "csrf": csrf, "turn_id": turn_id,
            "language": language, "mime": "audio/wav", "protocol": 1,
        }))
        ready = json.loads(socket.recv(timeout=20))
        if ready.get("type") != "ready":
            raise RuntimeError(f"Voice WebSocket rejected start: {ready.get('code', ready)}")
        for offset in range(0, len(audio), chunk_bytes):
            socket.send(audio[offset:offset + chunk_bytes])
        end_sent = time.perf_counter()
        socket.send(json.dumps({"type": "end"}))
        while True:
            message = json.loads(socket.recv(timeout=75))
            if message.get("type") == "final":
                return str(message.get("text") or ""), message, (end_sent - start) * 1000, (time.perf_counter() - end_sent) * 1000
            if message.get("type") == "error":
                raise RuntimeError(f"Voice WebSocket error: {message.get('code', 'unknown')}")


def _snapshot_db(path: Path) -> dict[str, int] | None:
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True,
                             timeout=5) as connection:
            return {
                "service_requests": int(connection.execute("SELECT COUNT(*) FROM service_requests").fetchone()[0]),
                "proposals": int(connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]),
            }
    except sqlite3.Error:
        return None


def _strings_by_key(value: object, keys: frozenset[str], output: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str):
                output.add(item)
            _strings_by_key(item, keys, output)
    elif isinstance(value, list):
        for item in value:
            _strings_by_key(item, keys, output)


def _tool_names(body: dict) -> set[str]:
    values: set[str] = set()
    _strings_by_key(body, frozenset({"tool_route", "tool", "tool_name", "capability"}), values)
    aliases = {"service": "service_action", "knowledge": "knowledge", "navigation": "navigation"}
    return {aliases.get(value, value) for value in values}


def _load_cases(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"Voice-eval manifest is missing: {path}. Add consented WAV rows first.")
    schema_path = path.with_name("manifest.schema.json")
    if not schema_path.is_file():
        raise SystemExit(f"Voice-eval schema is missing beside manifest: {schema_path}")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot load voice-eval schema: {schema_path}") from exc
    cases: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON at {path}:{line_number}") from exc
        errors = sorted(validator.iter_errors(case), key=lambda item: list(item.path))
        if errors:
            location = ".".join(str(item) for item in errors[0].path) or "row"
            raise SystemExit(f"Schema error at {path}:{line_number}:{location}: {errors[0].message}")
        required = {"id", "audio", "lang", "transcript", "disfluency", "expected_tools", "expected_slots", "expected_db_change"}
        if not required.issubset(case) or case["lang"] not in LANGUAGES:
            raise SystemExit(f"Invalid voice-eval row at {path}:{line_number}")
        if not isinstance(case["disfluency"], list) or not set(case["disfluency"]).issubset(ALLOWED_DISFLUENCY):
            raise SystemExit(f"Invalid disfluency labels at {path}:{line_number}")
        audio = path.parent / str(case["audio"])
        if not audio.is_file():
            raise SystemExit(f"Missing WAV for {case['id']}: {audio}")
        case = dict(case)
        case["_audio_path"] = audio
        cases.append(case)
    if not cases:
        raise SystemExit(f"Voice-eval manifest is empty: {path}")
    return cases


def _case_result(client: HttpClient, case: dict, *, base_url: str, db_path: Path) -> dict:
    language = str(case["lang"])
    before = _snapshot_db(db_path)
    session = client.json("POST", "/api/session")
    csrf = str(session.get("csrf_token") or "")
    if not csrf:
        raise RuntimeError("Session did not return CSRF token")
    headers = {"X-CSRF-Token": csrf}
    turn = client.json("POST", "/api/audio/turn/start", headers=headers)
    turn_id = str(turn.get("turn_id") or "")
    if not turn_id:
        raise RuntimeError("Voice turn did not return an ID")
    audio = Path(case["_audio_path"]).read_bytes()
    started = time.perf_counter()
    transcript, stt_message, _capture_ms, eot_to_stt_ms = transcribe_websocket(
        client, audio, language, turn_id, csrf, base_url=base_url)
    stt_reject_reason = stt_message.get("reject_reason")
    body: dict | None = None
    first_audio_ms: float | None = None
    if transcript.strip() and not stt_reject_reason:
        ask_headers = {**headers, "X-Voice-Turn-ID": turn_id}
        body = client.json("POST", "/api/ask", headers=ask_headers, body={
            "query": transcript, "language": language, "previous_query": "",
            "start_location": None, "turn_nonce": f"voice-eval-{uuid.uuid4().hex}",
        })
        chunks = ((body.get("speech_plan") or {}).get("chunks") or [])
        if chunks:
            status, raw, _ = client.request("POST", "/api/audio/speak", headers=headers,
                                            body={"chunk_id": chunks[0].get("id")})
            if status < 200 or status >= 300:
                raise RuntimeError(_json_error(status, raw))
            first_audio_ms = (time.perf_counter() - started) * 1000
    after = _snapshot_db(db_path)
    expected_db = str(case["expected_db_change"])
    actual_db_change = None
    if before is not None and after is not None:
        actual_db_change = "request_created" if after["service_requests"] > before["service_requests"] else "no_request"
    body_for_checks = body or {}
    actual_tools = _tool_names(body_for_checks)
    expected_tools = {str(item) for item in case["expected_tools"]}
    serialized = json.dumps(body_for_checks, ensure_ascii=False, sort_keys=True)
    slot_match = all(str(value) in serialized for value in (case["expected_slots"] or {}).values())
    tool_match = expected_tools.issubset(actual_tools)
    db_match = expected_db == "unknown" or actual_db_change == expected_db
    return {
        "id": case["id"],
        "lang": language,
        "wer": round(word_error_rate(str(case["transcript"]), transcript), 4),
        "stt_reject_reason": stt_reject_reason,
        "transcript_nonempty": bool(transcript.strip()),
        "tool_match": tool_match,
        "slot_match": slot_match,
        "db_match": db_match,
        "actual_tools": sorted(actual_tools),
        "actual_db_change": actual_db_change,
        "eot_to_stt_ms": round(eot_to_stt_ms, 2),
        "first_audio_ms": round(first_audio_ms, 2) if first_audio_ms is not None else None,
        "interruption_measurement": "not_available_without_duplex_trace",
    }


def run(*, base_url: str, manifest: Path, output: Path, db_path: Path) -> dict:
    cases = _load_cases(manifest)
    results: list[dict] = []
    for case in cases:
        client = HttpClient(base_url)
        try:
            results.append(_case_result(client, case, base_url=base_url, db_path=db_path))
        except Exception as exc:
            results.append({"id": case["id"], "lang": case["lang"], "error": type(exc).__name__, "detail": str(exc)})
    valid = [item for item in results if "error" not in item]
    first_audio = [item["first_audio_ms"] for item in valid if item.get("first_audio_ms") is not None]
    report = {
        "schema_version": 1,
        "base_url": base_url,
        "manifest": str(manifest),
        "cases": len(results),
        "completed": len(valid),
        "errors": len(results) - len(valid),
        "metrics": {
            "wer": round(sum(item["wer"] for item in valid) / len(valid), 4) if valid else None,
            "tool_accuracy": round(sum(item["tool_match"] for item in valid) / len(valid), 4) if valid else None,
            "slot_accuracy": round(sum(item["slot_match"] for item in valid) / len(valid), 4) if valid else None,
            "db_accuracy": round(sum(item["db_match"] for item in valid) / len(valid), 4) if valid else None,
            "first_audio_p50_ms": _percentile(first_audio, 0.50),
            "first_audio_p95_ms": _percentile(first_audio, 0.95),
            "early_cut_rate": None,
            "false_interruption_rate": None,
        },
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/voice-eval/latest.json"))
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("CONCIERGE_DB_PATH", "data/concierge.sqlite3")))
    args = parser.parse_args()
    report = run(base_url=args.base_url, manifest=args.manifest, output=args.output, db_path=args.db)
    print(json.dumps({"cases": report["cases"], "completed": report["completed"],
                      "errors": report["errors"], "metrics": report["metrics"]},
                     ensure_ascii=False, sort_keys=True))
    return 0 if report["errors"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
