"""Render the supplied edge proxy template with real operator values.

No default demo host, default private subnet or generated credential can reach
output. A protected staff-gateway include is created from an existing secret
file; neither the secret nor certificate bytes are logged. Outputs are exclusive
create/no-overwrite and must be reviewed with ``nginx -t`` on the target host.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
from pathlib import Path
from tools._shared.paths import project_root
from urllib.parse import urlsplit

ROOT = project_root()
HOST = re.compile(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z')
SECRET = re.compile(r'[A-Za-z0-9_\-]{32,512}\Z')


def _host(origin: str) -> str:
    parsed = urlsplit(origin)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or not HOST.fullmatch(host) or '..' in host or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ('', '/') or parsed.port not in (None, 443) or
            host.endswith('.example')):
        raise ValueError('A real canonical HTTPS hostname must be provided')
    return host


def _existing(path: Path, description: str) -> Path:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ValueError(description + ' must be an existing non-symlink absolute file')
    return path


def render(*, guest_origin: str, staff_origin: str, staff_allow: tuple[str, ...],
           certificate: Path, private_key: Path, gateway_secret_file: Path,
           gateway_include: Path, output: Path) -> None:
    guest, staff = _host(guest_origin), _host(staff_origin)
    if guest == staff:
        raise ValueError('Guest and staff must have different DNS hostnames')
    if not staff_allow:
        raise ValueError('Explicit staff VPN/VLAN CIDR required')
    allow = []
    for raw in staff_allow:
        network = ipaddress.ip_network(raw, strict=True)
        if network.prefixlen == 0 or not (network.is_private or network.is_loopback):
            raise ValueError('Staff CIDRs must be restricted to private/loopback networks')
        allow.append('    allow ' + str(network) + ';')
    for path, label in ((certificate, 'TLS full chain'), (private_key, 'TLS private key'),
                        (gateway_secret_file, 'Existing staff gateway secret')):
        _existing(path, label)
    if (not output.is_absolute() or not gateway_include.is_absolute() or
            output == gateway_include or output.exists() or gateway_include.exists() or
            output.is_symlink() or gateway_include.is_symlink()):
        raise ValueError('Use distinct, new absolute output paths; existing files are never overwritten')
    secret = gateway_secret_file.read_text(encoding='ascii').strip()
    if not SECRET.fullmatch(secret):
        raise ValueError('The existing staff gateway secret is invalid')
    source = (ROOT / 'deploy/edge/nginx.conf.template').read_text(encoding='utf-8')
    replaced = (source.replace('__GUEST_HOST__', guest)
                .replace('__STAFF_HOST__', staff)
                .replace('__TLS_CERT__', str(certificate))
                .replace('__TLS_KEY__', str(private_key))
                .replace('__STAFF_GATEWAY_INCLUDE__', str(gateway_include))
                .replace('__STAFF_ALLOW_RULES__', '\n'.join(allow)))
    if any(token in replaced for token in ('__GUEST_HOST__', '__STAFF_HOST__', '__TLS_CERT__',
                                           '__TLS_KEY__', '__STAFF_GATEWAY_INCLUDE__', '__STAFF_ALLOW_RULES__')):
        raise ValueError('Unrendered deployment placeholder')
    if secret in replaced:
        raise ValueError('Staff secret must never be embedded in the vhost configuration')
    output.parent.mkdir(parents=True, exist_ok=True)
    gateway_include.parent.mkdir(parents=True, exist_ok=True)
    include_fd = os.open(gateway_include, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(include_fd, 'w', encoding='ascii') as file:
            file.write(f'proxy_set_header X-Concierge-Staff-Gateway "{secret}";\n')
            file.flush()
            os.fsync(file.fileno())
        output_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(output_fd, 'w', encoding='utf-8') as file:
            file.write(replaced)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        # A half-rendered config is not an accepted staff ingress.
        gateway_include.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guest-origin', required=True)
    parser.add_argument('--staff-origin', required=True)
    parser.add_argument('--staff-allow', action='append', required=True)
    parser.add_argument('--certificate', type=Path, required=True)
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--gateway-secret-file', type=Path, required=True)
    parser.add_argument('--gateway-include', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        render(guest_origin=args.guest_origin, staff_origin=args.staff_origin,
               staff_allow=tuple(args.staff_allow), certificate=args.certificate,
               private_key=args.private_key, gateway_secret_file=args.gateway_secret_file,
               gateway_include=args.gateway_include, output=args.output)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.error('Proxy rendering blocked: ' + str(exc))
    print('Rendered guest/staff proxy files. Run nginx -t and verify actual network isolation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
