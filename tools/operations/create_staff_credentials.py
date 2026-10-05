"""Provision one staff token offline; only the hash goes into the environment."""
import hashlib
import json
import secrets
import sys

if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) >= 2 else 'frontdesk-1'
    if not name or len(name)>80:
        raise SystemExit('Supply a stable staff name of <=80 characters')
    token = secrets.token_urlsafe(40)
    print('Issue this credential securely to staff (NOT to kiosk):', token)
    print('Production account record:')
    print(json.dumps({'name':name,'token_hash':hashlib.sha256(token.encode()).hexdigest(),
                      'scopes':['requests:read','requests:write']}))
