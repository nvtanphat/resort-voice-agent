"""Normalize checked-in synthetic history to the escalation policy contract.

This is a data migration, not a simulator.  Older generated rows could contain
two automatic reminders after a reopen; the current policy permits at most one
per request.  The migration changes only that derived counter and records the
same synthetic provenance in the existing metadata.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "datasets" / "synthetic" / "operations" / "history"
REQUESTS = HISTORY / "requests.jsonl"


def main() -> None:
    rows = [json.loads(line) for line in REQUESTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = 0
    for row in rows:
        if row.get("automatic_escalations", 0) > 1:
            row["automatic_escalations"] = 1
            changed += 1
    REQUESTS.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"rows": len(rows), "rows_changed": changed}, sort_keys=True))


if __name__ == "__main__":
    main()
