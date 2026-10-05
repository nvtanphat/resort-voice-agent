"""One-time offline secret provisioner, never prints credentials to build logs.

Securely transfer staff_operator_token to authorized staff and DELETE that token
file from the kiosk after provisioning the separate staff workstation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path


def create_secrets(folder: Path, *, staff_name: str) -> None:
    if not staff_name or len(staff_name) > 80 or not all(c.isalnum() or c in '-_' for c in staff_name):
        raise ValueError('Staff name must use letters, digits, hyphen or underscore')
    if folder.is_symlink() or folder.exists():
        raise FileExistsError('Provision a new, empty secret directory; never overwrite credentials')
    folder.mkdir(mode=0o700, parents=False)
    operator = secrets.token_urlsafe(48)
    credentials = json.dumps([{'name':staff_name,
        'token_hash':hashlib.sha256(operator.encode()).hexdigest(),
        'scopes':['requests:read','requests:write']}], separators=(',',':'))
    payloads = {
        'agent_token': secrets.token_urlsafe(48).encode(),
        'staff_gateway_token': secrets.token_urlsafe(48).encode(),
        'staff_credentials.json': credentials.encode(),
        'staff_operator_token': operator.encode(),
        'backup.key': os.urandom(32),
    }
    for name, data in payloads.items():
        fd = os.open(folder/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
    print('Secrets provisioned; no tokens printed. Transfer staff_operator_token and backup.key to secured, separate destinations.')


def main() -> None:
    parser=argparse.ArgumentParser(description='Create unique owner-only edge credentials')
    parser.add_argument('--directory',type=Path,default=Path('./secrets'))
    parser.add_argument('--staff-name',required=True)
    args=parser.parse_args()
    create_secrets(args.directory, staff_name=args.staff_name)


if __name__=='__main__':
    main()
