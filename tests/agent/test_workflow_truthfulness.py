from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class WorkflowTruthfulnessTests(unittest.TestCase):
    def test_guest_service_workflows_do_not_claim_direct_integrations(self):
        forbidden = (
            "transmits order ticket to",
            "checks dnd",
            "registers wake-up reminder on resort pbx",
            "high-priority alarm alerts",
        )
        rows = [json.loads(line) for line in (ROOT / "datasets/synthetic/operations/workflows/guest_service_workflows.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        for workflow in rows:
            self.assertEqual(workflow.get("automation_level"), "staff_assisted")
            blob = json.dumps(workflow, ensure_ascii=False).casefold()
            for phrase in forbidden:
                self.assertNotIn(phrase, blob, workflow["workflow_id"])


if __name__ == "__main__":
    unittest.main()
