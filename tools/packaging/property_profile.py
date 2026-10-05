"""Sign an operator-reviewed property profile for offline deployment."""
from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path


def sign(profile: Path, private_key: Path, signature: Path) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = profile.read_bytes()
    key = serialization.load_pem_private_key(private_key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Only Ed25519 private keys are supported')
    signature.write_bytes(base64.b64encode(key.sign(raw)))
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--signature', type=Path, required=True)
    args = parser.parse_args()
    print(sign(args.profile, args.private_key, args.signature))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
