"""Validate the checksum-pinned agent-domain profile used by the runtime."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from concierge_kiosk.core.domain_profile import (
    default_domain_profile_binding,
    load_domain_profile,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', default='')
    parser.add_argument('--sha256', default='')
    parser.add_argument('--schema', default='')
    args = parser.parse_args()

    if bool(args.path) != bool(args.sha256):
        parser.error('--path and --sha256 must be supplied together')
    path, checksum = ((args.path, args.sha256)
                      if args.path else default_domain_profile_binding())
    profile = load_domain_profile(path, checksum, schema_path=args.schema or None)
    print(
        f'agent-domain ok profile={profile.profile_id} sha256={profile.sha256} '
        f'languages={len(profile.languages)} request_kinds={len(profile.request_kinds)} '
        f'services={len(profile.services)} preferences={len(profile.preferences.fields)}'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
