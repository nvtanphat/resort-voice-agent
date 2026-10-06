from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.validate.property_dataset import verify_dataset_manifest
from tools.validate.semantics import validate_cross_artifact_invariants
from concierge_kiosk.core.dataset_layout import (
    ENTITIES,
    FACTS,
    KNOWLEDGE_MANIFEST,
    PROPERTY,
    RELATIONS,
    QUARANTINE_FACTS,
    dataset_path,
)

ROOT = Path(__file__).resolve().parents[2]


class DataIntegrityRegressionTests(unittest.TestCase):
    def test_manifest_matches_every_curated_build_input(self):
        result = verify_dataset_manifest(dataset_path(""))
        self.assertEqual(result["manifest_errors"], 0)
        manifest = json.loads(dataset_path(KNOWLEDGE_MANIFEST).read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(manifest["artifacts"]), 10)
        self.assertGreaterEqual(manifest["metrics"]["entities"], 100)
        self.assertGreaterEqual(manifest["metrics"]["facts"], 300)

    def test_cross_artifact_guest_facing_invariants(self):
        errors = validate_cross_artifact_invariants(dataset_path(""))
        self.assertEqual(errors, [])

    def test_validation_report_matches_current_release_artifacts(self):
        manifest = json.loads(dataset_path(KNOWLEDGE_MANIFEST).read_text(encoding="utf-8"))
        facts = [json.loads(line) for line in dataset_path(FACTS).read_text(encoding="utf-8").splitlines() if line.strip()]
        entities = [json.loads(line) for line in dataset_path(ENTITIES).read_text(encoding="utf-8").splitlines() if line.strip()]
        relations = [json.loads(line) for line in dataset_path(RELATIONS).read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(manifest["metrics"]["entities"], len(entities))
        self.assertEqual(manifest["metrics"]["facts"], len(facts))
        self.assertEqual(manifest["metrics"]["entity_relations"], len(relations))
        compiled = sum(1 for _ in (ROOT / "knowledge/compiled/furama").glob("*/*.md"))
        self.assertEqual(compiled, 476)

    def test_manifest_verifier_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "datasets"
            import shutil
            shutil.copytree(dataset_path(""), target)
            profile = dataset_path(PROPERTY, target)
            profile.write_text(profile.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Dataset manifest integrity validation failed"):
                verify_dataset_manifest(target)

    def test_manifest_verifier_accepts_platform_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "datasets"
            import shutil
            shutil.copytree(dataset_path(""), target)
            profile = dataset_path(PROPERTY, target)
            content = profile.read_bytes().replace(b"\r\n", b"\n")
            profile.write_bytes(content.replace(b"\n", b"\r\n"))
            self.assertEqual(verify_dataset_manifest(target)["manifest_errors"], 0)


if __name__ == "__main__":
    unittest.main()
