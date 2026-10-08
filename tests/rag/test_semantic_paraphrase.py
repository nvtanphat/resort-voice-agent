from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from concierge_kiosk.agent.understanding.semantic import _normalized_literal_values, semantic_grounded_response


class SemanticParaphraseTests(unittest.TestCase):

    def test_model_verifier_can_admit_verified_paraphrase(self):
        evidence = [{"content": "Hồ bơi trẻ em mở từ 7h đến 19h."}]
        generated = json.dumps({"claims": [{
            "source": "S1", "quote": evidence[0]["content"],
            "text": "Có, hồ bơi trẻ em mở từ 7h đến 19h.",
        }]}, ensure_ascii=False)
        verdict = json.dumps({
            "verdict": "SUPPORTED",
            "quote": evidence[0]["content"],
            "claim": "Có, hồ bơi trẻ em mở từ 7h đến 19h.",
        }, ensure_ascii=False)
        with patch("concierge_kiosk.agent.understanding.semantic._chat", side_effect=[generated, verdict]):
            result = semantic_grounded_response(
                base_url="http://127.0.0.1:11434", model="local-test",
                question="Có hồ bơi trẻ em không?", evidence=evidence,
                language="vi", require_independent_nli=False,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.answer, "Có, hồ bơi trẻ em mở từ 7h đến 19h.")

    def test_can_synthesize_multiple_verified_sources_without_stitching_facts(self):
        evidence = [
            {"content": "Hồ bơi trẻ em mở từ 7h đến 19h."},
            {"content": "Kids Club nằm cạnh khu hồ bơi."},
        ]
        generated = json.dumps({"claims": [
            {"source": "S1", "quote": evidence[0]["content"],
             "text": "Hồ bơi trẻ em mở từ 7h đến 19h."},
            {"source": "S2", "quote": evidence[1]["content"],
             "text": "Kids Club ở cạnh khu hồ bơi."},
        ]}, ensure_ascii=False)
        verdict = json.dumps({
            "verdict": "SUPPORTED",
            "quote": evidence[1]["content"],
            "claim": "Kids Club ở cạnh khu hồ bơi.",
        }, ensure_ascii=False)
        with patch("concierge_kiosk.agent.understanding.semantic._chat", side_effect=[generated, verdict]):
            result = semantic_grounded_response(
                base_url="http://127.0.0.1:11434", model="local-test",
                question="Hồ bơi trẻ em và Kids Club ở đâu?", evidence=evidence,
                language="vi", require_independent_nli=False,
            )
        self.assertIsNotNone(result)
        self.assertIn("7h", result.answer)
        self.assertIn("Kids Club ở cạnh", result.answer)
        self.assertEqual(len(result.claims), 2)



if __name__ == "__main__":
    unittest.main()
