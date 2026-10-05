"""Create the deterministic offline hash embedder and integrity manifest.

The artifact contains configuration only; no learned weights or network access.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))
from concierge_kiosk.rag.model_manifest import rag_model_manifest

MODEL = ROOT / 'models/embeddings/hash-multilingual'
MANIFEST = ROOT / 'models/embeddings/hash-multilingual.manifest.json'

def build() -> tuple[Path, Path]:
    MODEL.mkdir(parents=True, exist_ok=True)
    payload = {'format': 'concierge-hash-embedding', 'dimension': 384, 'min_n': 2, 'max_n': 5}
    (MODEL / 'embedder.json').write_text(json.dumps(payload, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')
    manifest = rag_model_manifest(str(MODEL))
    MANIFEST.write_text(json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')
    return MODEL, MANIFEST

if __name__ == '__main__':
    model, manifest = build()
    print(model)
    print(manifest)
