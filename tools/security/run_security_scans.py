"""Run Bandit and pip-audit and preserve honest release evidence.

The command never turns a scanner execution error into a clean result.  A
managed Python installation can make pip-audit unable to enumerate packages;
that is recorded as ``blocked_environment`` and remains a release blocker.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def _run(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
    return completed.returncode, completed.stdout, completed.stderr


def _bandit() -> dict:
    code, stdout, stderr = _run([
        "bandit", "-r", "src", "tools", "-q", "-f", "json",
    ])
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {"status": "blocked_environment", "exit_code": code,
                "error": (stderr or stdout)[-4000:]}
    totals = payload.get("metrics", {}).get("_totals", {})
    severe = int(totals.get("SEVERITY.HIGH", 0)) + int(totals.get("SEVERITY.MEDIUM", 0))
    return {
        "status": ("pass" if code == 0 else
                    "pass_with_low_findings" if severe == 0 else "findings"),
        "exit_code": code,
        "high": int(totals.get("SEVERITY.HIGH", 0)),
        "medium": int(totals.get("SEVERITY.MEDIUM", 0)),
        "low": int(totals.get("SEVERITY.LOW", 0)),
        "confidence_high": int(totals.get("CONFIDENCE.HIGH", 0)),
        "findings": payload.get("results", []),
        "stderr_tail": stderr[-2000:],
    }


def _pip_audit() -> dict:
    code, stdout, stderr = _run([
        sys.executable, "-m", "pip_audit", "--format", "json",
        "--progress-spinner", "off",
    ])
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {"status": "blocked_environment", "exit_code": code,
                "error": (stderr or stdout)[-4000:]}
    vulnerabilities = [item for item in payload if item.get("vulns")]
    return {"status": "pass" if code == 0 and not vulnerabilities else "vulnerabilities",
            "exit_code": code, "packages": len(payload),
            "vulnerable_packages": vulnerabilities, "stderr_tail": stderr[-2000:]}


def run(output: Path) -> dict:
    report = {
        "type": "workspace_security_scan",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "commands": {
            "bandit": "bandit -r src tools -q -f json",
            "pip_audit": f"{sys.executable} -m pip_audit --format json --progress-spinner off",
        },
        "scans": {"bandit": _bandit(), "pip_audit": _pip_audit()},
    }
    statuses = {scan["status"] for scan in report["scans"].values()}
    report["status"] = "pass" if statuses <= {"pass", "pass_with_low_findings"} else "blocked_or_findings"
    report["release_gate"] = report["status"] == "pass" and report["scans"]["pip_audit"]["status"] == "pass"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/gates/security-scans.json")
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
