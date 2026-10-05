"""Private, offline signed/admin-managed file ingestion. No public upload route."""
import argparse
from pathlib import Path

from concierge_kiosk.core.settings import load_settings
from ..core.clock import property_today
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag import LocalEmbedder, ingest_text, ingest_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Import approved hotel Markdown files")
    parser.add_argument("directory", nargs='?', type=Path, help="Directory of approved Markdown files (dev only)")
    parser.add_argument('--bundle', type=Path, help='Signed zip of hotel knowledge')
    parser.add_argument('--manifest', type=Path, help='Detached signed JSON manifest')
    parser.add_argument('--signature', type=Path, help='Detached base64 Ed25519 signature')
    parser.add_argument('--public-key', type=Path, help='Pinned hotel-update Ed25519 public key')
    args = parser.parse_args()
    cfg = load_settings()
    if cfg.property_id in {"DEMO-HOTEL", "UNCONFIGURED"}:
        parser.error("Configure the real property identity; test fixtures cannot be ingested with this CLI")
    if cfg.environment == 'production' and not args.bundle:
        parser.error('Production knowledge changes require a signed bundle')
    embedding = LocalEmbedder(cfg.embedding_model_path, cfg.embedding_manifest_path) if cfg.embedding_model_path else None
    store = Store(cfg.db_path)
    if args.bundle:
        if not all((args.manifest, args.signature, args.public_key)):
            parser.error('Signed update requires manifest, signature and pinned public key')
        from tools.packaging.knowledge import read_signed_package
        manifest, documents = read_signed_package(args.bundle, args.manifest, args.signature,
                                                  args.public_key, property_id=cfg.property_id)
        count = ingest_bundle(store, documents, property_id=cfg.property_id, embedder=embedding,
                              release_version=manifest['release_version'], bundle_sha256=manifest['sha256'],
                              signed_chunk_policy_hash=manifest['chunk_policy_hash'],
                              effective_date=property_today(cfg.property_timezone))
        print(f"Applied signed release {manifest['release_version']}: {count} knowledge chunks")
    else:
        if not args.directory:
            parser.error('Supply directory or --bundle')
        count=0
        for path in sorted(args.directory.rglob('*.md')):
            count += ingest_text(store, path.read_text(encoding='utf-8'),
                                 property_id=cfg.property_id, embedder=embedding)
        print(f'Imported {count} versioned chunks from {args.directory}')


if __name__ == "__main__":
    main()
