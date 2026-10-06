"""Offline operator-side package creation. Private signing key NEVER goes on kiosk.

Generate once on a secured admin computer, e.g.
  openssl genpkey -algorithm ED25519 -out hotel-update-private.pem
  openssl pkey -in hotel-update-private.pem -pubout -out hotel-update-public.pem
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import time
import zipfile
from pathlib import Path


def package(directory: Path, archive: Path, private_key: Path, property_id: str, version: int,
            *, issued_at: int | None = None, expires_at: int | None = None):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if version < 1 or not property_id or not archive.name.endswith('.zip'):
        raise ValueError('Invalid signed release metadata')
    if archive.exists() or archive.with_suffix('.manifest.json').exists():
        raise FileExistsError('Refusing to overwrite an existing release')
    docs=sorted(directory.glob('*.md'))
    if not 1 <= len(docs) <= 500:
        raise ValueError('Knowledge directory must contain 1..500 Markdown documents')
    key=load_pem_private_key(private_key.read_bytes(),password=None)
    if not isinstance(key,Ed25519PrivateKey):
        raise ValueError('Only Ed25519 keys are supported')
    issued_at = int(time.time()) if issued_at is None else int(issued_at)
    expires_at = issued_at + 30 * 24 * 3600 if expires_at is None else int(expires_at)
    if issued_at < 1 or expires_at <= issued_at:
        raise ValueError('Knowledge release expiry must be after issuance')
    try:
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as zf:
            for path in docs:
                if path.stat().st_size > 1_000_000:
                    raise ValueError('Oversized knowledge document')
                if path.is_symlink():
                    raise ValueError('Knowledge symlinks are not permitted')
                # Stable metadata makes identical approved input produce identical
                # archive bytes, which in turn gives operators a reproducible SHA.
                info = zipfile.ZipInfo('knowledge/' + path.name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zf.writestr(info, path.read_bytes())
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        from concierge_kiosk.rag.ingestion import CHUNK_POLICY, chunk_policy_hash
        manifest={'product':'concierge-knowledge','property_id':property_id,
                  'release_version':version,'filename':archive.name,
                  'sha256':digest,'size_bytes':archive.stat().st_size,
                  'issued_at': issued_at, 'expires_at': expires_at,
                  'chunk_policy':CHUNK_POLICY,'chunk_policy_hash':chunk_policy_hash()}
        raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
        manifest_path=archive.with_suffix('.manifest.json')
        signature_path=archive.with_suffix('.manifest.sig')
        manifest_path.write_bytes(raw)
        signature_path.write_bytes(base64.b64encode(key.sign(raw)))
        return manifest_path,signature_path
    except Exception:
        archive.unlink(missing_ok=True)
        archive.with_suffix('.manifest.json').unlink(missing_ok=True)
        archive.with_suffix('.manifest.sig').unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description='Sign an offline hotel knowledge release')
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--private-key',type=Path,required=True)
    parser.add_argument('--property-id',required=True)
    parser.add_argument('--version',type=int,required=True)
    parser.add_argument('--issued-at',type=int)
    parser.add_argument('--expires-at',type=int)
    args=parser.parse_args()
    files=package(args.directory,args.archive,args.private_key,args.property_id,args.version,
                  issued_at=args.issued_at, expires_at=args.expires_at)
    print('Signed release:',args.archive,*files)

if __name__ == '__main__':
    main()


def read_signed_package(archive: Path, manifest_path: Path, signature_path: Path,
                        public_key_path: Path, *, property_id: str) -> tuple[dict, dict[str, str]]:
    """Authenticate and read a signed offline knowledge package without extracting it."""
    import re
    import stat
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature

    if manifest_path.stat().st_size > 16_384:
        raise ValueError('Oversized manifest')
    raw = manifest_path.read_bytes()
    try:
        pub = serialization.load_pem_public_key(public_key_path.read_bytes())
        if not isinstance(pub, Ed25519PublicKey):
            raise ValueError('Only Ed25519 public keys are accepted')
        pub.verify(base64.b64decode(signature_path.read_bytes(), validate=True), raw)
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError('Invalid update signature or public key') from exc

    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or manifest.get('product') != 'concierge-knowledge'
        or manifest.get('property_id') != property_id
        or type(manifest.get('release_version')) is not int or manifest['release_version'] < 1
        or manifest.get('filename') != archive.name
        or not re.fullmatch('[0-9a-f]{64}', str(manifest.get('sha256','')))
        or type(manifest.get('size_bytes')) is not int
        or manifest['size_bytes'] != archive.stat().st_size
        or manifest['size_bytes'] > 15_000_000):
        raise ValueError('Manifest property, release or archive size/metadata invalid')
    if ('issued_at' in manifest or 'expires_at' in manifest):
        if (type(manifest.get('issued_at')) is not int or type(manifest.get('expires_at')) is not int
                or manifest['issued_at'] < 1 or manifest['expires_at'] <= manifest['issued_at']):
            raise ValueError('Invalid knowledge release freshness metadata')

    from concierge_kiosk.rag.ingestion import CHUNK_POLICY, chunk_policy_hash
    if (manifest.get('chunk_policy') != CHUNK_POLICY or
            manifest.get('chunk_policy_hash') != chunk_policy_hash()):
        raise ValueError('Signed knowledge chunk policy is unsupported')

    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Knowledge archive SHA-256 mismatch')

    documents: dict[str, str] = {}
    try:
        with zipfile.ZipFile(archive) as zf:
            items = zf.infolist()
            if not 1 <= len(items) <= 500:
                raise ValueError('Invalid number of knowledge files')
            total = 0
            for info in items:
                name = info.filename
                if (info.is_dir() or not re.fullmatch(r'knowledge/[A-Za-z0-9_-]+\.md', name)
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or info.file_size > 1_000_000 or info.compress_size > 1_000_000):
                    raise ValueError('Unsafe knowledge archive entry')
                total += info.file_size
                if total > 10_000_000 or name in documents:
                    raise ValueError('Oversized or repeated knowledge document')
                # Signature/SHA above authenticate the ORIGINAL archive bytes. Only
                # after authentication do we canonicalize decoded Markdown for
                # front-matter parsing, revision identity and chunking.
                decoded = zf.read(info).decode('utf-8-sig')
                documents[name] = decoded.replace('\r\n', '\n').replace('\r', '\n')
    except (zipfile.BadZipFile, UnicodeDecodeError, RuntimeError) as exc:
        raise ValueError('Invalid knowledge zip') from exc
    return manifest, documents
