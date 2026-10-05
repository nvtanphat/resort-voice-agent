"""Build a source-artifact registry from pinned canonical provenance.

This command is intentionally offline. It does not claim that raw HTML/PDF bytes
were archived when they were not. Existing provenance becomes an explicit
``verified_evidence`` / ``unverified_evidence`` registry; a future crawler/archive job can upgrade an
entry to ``raw_archived`` by adding raw_path/raw_sha256.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from concierge_kiosk.core.dataset_layout import FACTS, QUARANTINE_FACTS, dataset_path

DATA = dataset_path(FACTS).parent
QUARANTINE = dataset_path(QUARANTINE_FACTS)
OUTPUT = dataset_path("knowledge/sources/source_artifacts.jsonl")


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build(data_root: Path = DATA) -> dict[str, int]:
    facts_path = data_root / Path(FACTS).name
    quarantine_path = QUARANTINE if data_root == DATA else data_root / "audit/facts-quarantine.jsonl"
    output_path = OUTPUT if data_root == DATA else data_root / "source-artifacts.jsonl"
    artifacts: dict[str, dict] = {}
    for fact in [*_load_jsonl(facts_path), *_load_jsonl(quarantine_path)]:
        fact_verified = fact.get("verified_at")
        for source in fact.get("provenance_sources", []):
            artifact_id = str(source.get("document_id") or "").strip()
            url = str(source.get("source_url") or "").strip()
            evidence_hash = str(source.get("evidence_sha256") or "").strip()
            if not artifact_id or not url or not evidence_hash:
                continue
            row = artifacts.setdefault(artifact_id, {
                "artifact_id": artifact_id,
                "property_id": "FURAMA_DANANG",
                "publisher": "Furama Resort Danang",
                "source_kind": "official_pdf" if url.lower().split("?", 1)[0].endswith(".pdf") else "official_html",
                "source_url": url,
                "verified_at": source.get("verified_at") or fact_verified,
                "verification_state": "unverified_evidence",
                "evidence_count": 0,
                "evidence_hashes": [],
                "raw_path": None,
                "raw_sha256": None,
                "extracted_text_path": None,
            })
            if row["source_url"] != url:
                raise ValueError(f"Source artifact {artifact_id} maps to multiple URLs")
            if evidence_hash not in row["evidence_hashes"]:
                row["evidence_hashes"].append(evidence_hash)
            row["evidence_count"] += 1
            verified = source.get("verified_at") or fact_verified
            if verified and (not row.get("verified_at") or str(verified) > str(row["verified_at"])):
                row["verified_at"] = verified

    ordered = []
    for artifact_id in sorted(artifacts):
        row = artifacts[artifact_id]
        row["evidence_hashes"] = sorted(row["evidence_hashes"])
        if row.get("verification_state") != "raw_archived":
            row["verification_state"] = "verified_evidence" if row.get("verified_at") else "unverified_evidence"
        ordered.append(row)
    output_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in ordered) + "\n", encoding="utf-8")
    return {
        "source_artifacts": len(ordered),
        "verified_evidence": sum(row["verification_state"] == "verified_evidence" for row in ordered),
        "unverified_evidence": sum(row["verification_state"] == "unverified_evidence" for row in ordered),
        "raw_archived": sum(row["verification_state"] == "raw_archived" for row in ordered),
    }


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True))
