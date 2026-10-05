"""Explicit offline FTS repair after backup; not a guest-facing endpoint."""
from __future__ import annotations

import argparse
import json

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.rag import rebuild_knowledge_index, validate_knowledge_index


def main() -> None:
    cli = argparse.ArgumentParser(description='Inspect or repair hotel knowledge FTS')
    cli.add_argument('--repair', action='store_true', help='Explicitly rebuild from authoritative knowledge rows')
    args = cli.parse_args()
    store = Store(load_settings().db_path)
    state = rebuild_knowledge_index(store) if args.repair else validate_knowledge_index(store)
    print(json.dumps({'status': 'ok', **state}, sort_keys=True))


if __name__ == '__main__':
    main()
