"""Run Current curation in an isolated workspace and publish only after validation.

The legacy curator is deterministic but writes several files. This launcher keeps
those writes away from the live dataset until semantic/schema/manifest checks pass.
It then swaps the dataset and approved-knowledge directories with rollback support.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_furama_source_artifacts import build as build_source_artifacts
from tools.refresh_furama_manifest import refresh_manifest
from tools.validate_furama_schemas import validate as validate_schemas
from tools.validate_furama_semantics import validate_dataset


def _swap_with_backup(prepared: Path, live: Path, backup: Path) -> None:
    if backup.exists():
        shutil.rmtree(backup)
    live.rename(backup)
    prepared.rename(live)


def run() -> dict[str, object]:
    live_data = ROOT / "datasets"
    live_knowledge = ROOT / "knowledge/approved/furama"
    token = uuid.uuid4().hex[:10]
    backup_data = live_data.with_name(f"{live_data.name}.backup-{token}")
    backup_knowledge = live_knowledge.with_name(f"{live_knowledge.name}.backup-{token}")

    with tempfile.TemporaryDirectory(prefix="furama-curation-") as tmp:
        stage_root = Path(tmp) / "repo"
        stage_data = stage_root / "datasets"
        stage_knowledge = stage_root / "knowledge/approved/furama"
        stage_data.parent.mkdir(parents=True, exist_ok=True)
        stage_knowledge.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(live_data, stage_data)
        shutil.copytree(live_knowledge, stage_knowledge)

        env = os.environ.copy()
        env["CONCIERGE_CURATION_ROOT"] = str(stage_root)
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools/curate_furama_web_verified.py")],
            cwd=ROOT, env=env, text=True, capture_output=True, check=True,
        )
        source_result = build_source_artifacts(stage_data / "knowledge/canonical")
        refresh_manifest(stage_data)
        semantic_result = validate_dataset(stage_data)
        schema_result = validate_schemas(stage_data)

        data_swapped = knowledge_swapped = False
        try:
            _swap_with_backup(stage_data, live_data, backup_data)
            data_swapped = True
            _swap_with_backup(stage_knowledge, live_knowledge, backup_knowledge)
            knowledge_swapped = True
        except Exception:
            if knowledge_swapped and live_knowledge.exists():
                shutil.rmtree(live_knowledge)
            if backup_knowledge.exists():
                backup_knowledge.rename(live_knowledge)
            if data_swapped and live_data.exists():
                shutil.rmtree(live_data)
            if backup_data.exists():
                backup_data.rename(live_data)
            raise
        else:
            shutil.rmtree(backup_data, ignore_errors=True)
            shutil.rmtree(backup_knowledge, ignore_errors=True)

    return {
        "curator_output": proc.stdout.strip(),
        "source_artifacts": source_result,
        "semantic_validation": semantic_result,
        "schema_validation": schema_result,
        "published": True,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
