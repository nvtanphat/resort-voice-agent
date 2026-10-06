"""Report whether a real python-tuf client/repository is provisioned."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(output: Path, *, local_fixture_verified: bool = False) -> dict:
    installed = importlib.util.find_spec("tuf") is not None
    result = {
        "type": "knowledge_ota_tuf_readiness",
        "adapter": "tools.operations.knowledge_tuf",
        "python_tuf_installed": installed,
        "repository_fixture_verified": False,
        "local_fixture_verified": bool(local_fixture_verified),
        "root_bootstrap_required": True,
        "status": "BLOCKED",
        "release_gate": False,
        "blockers": [
            "No operator-provisioned TUF metadata/targets repository was verified in this workspace.",
            "A pinned root.json and HTTPS repository endpoint are required before enabling remote OTA.",
        ],
    }
    if not installed:
        result["blockers"].insert(0, "Optional ota-tuf dependency is not installed in the current environment.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/gates/knowledge-tuf.json")
    parser.add_argument("--local-fixture-verified", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.output, local_fixture_verified=args.local_fixture_verified),
                     ensure_ascii=False, indent=2))
