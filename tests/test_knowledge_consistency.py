from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class KnowledgeConsistencyTests(unittest.TestCase):
    def test_cafe_indochine_index_has_correct_hours(self):
        con = sqlite3.connect(ROOT / "data/concierge.sqlite3")
        try:
            bodies = "\n".join(
                row[0]
                for row in con.execute(
                    "SELECT body FROM knowledge WHERE property_id=? AND source=? AND language='en' AND active=1",
                    ("FURAMA_DANANG", "kb_restaurant_cafe_indochine"),
                )
            )
        finally:
            con.close()
        self.assertIn("06:30–10:30", bodies)
        self.assertIn("11:30–14:00", bodies)
        self.assertIn("18:00–22:00", bodies)
        self.assertIn("18:30–22:00", bodies)
        self.assertNotIn("06:30–22:00", bodies)
        self.assertNotIn("30:00", bodies)

    def test_spa_index_has_2200_close(self):
        con = sqlite3.connect(ROOT / "data/concierge.sqlite3")
        try:
            bodies = "\n".join(
                row[0]
                for row in con.execute(
                    "SELECT body FROM knowledge WHERE property_id=? AND source=? AND language='en' AND active=1",
                    ("FURAMA_DANANG", "kb_spa_v_senses_wellness"),
                )
            )
        finally:
            con.close()
        self.assertIn("09:00–22:00", bodies)


if __name__ == "__main__":
    unittest.main()
