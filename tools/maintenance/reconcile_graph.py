"""Reconstruct / advance minimal graph checkpoints from authoritative staff records.

Safe after a restore or temporary LangGraph checkpoint failure. Does not approve,
reject, confirm, or complete any real-world request. Run from an admin terminal.
"""
from __future__ import annotations

import argparse

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.persistence.sqlite_store import Store
from concierge_kiosk.agent.orchestration.graph import ConciergeGraph
from concierge_kiosk.domain.service_requests import Workflows


def main():
    parser = argparse.ArgumentParser(description='Reconcile checkpoint-only Concierge graph state')
    parser.add_argument('--request-id', default='', help='One request; default reconciles all retained requests')
    parser.add_argument('--rebuild-checkpoint', action='store_true',
        help='Admin-only destructive graph projection rebuild for one request; stop the API first')
    args = parser.parse_args()
    if args.rebuild_checkpoint and not args.request_id:
        parser.error('--rebuild-checkpoint requires an explicit --request-id')
    cfg = load_settings()
    store = Store(cfg.db_path)
    workflows = Workflows(store, cfg.property_id, cfg.proposal_ttl_seconds)
    graph = ConciergeGraph(workflows, cfg.db_path.with_name(cfg.db_path.stem + '-graph.sqlite3'))
    try:
        with store.connection() as con:
            requests = [row[0] for row in con.execute(
                'SELECT id FROM service_requests WHERE property_id=? AND (?="" OR id=?) ORDER BY created_at',
                (cfg.property_id, args.request_id, args.request_id))]
        if args.request_id and not requests:
            parser.error('Request was not found in the configured property')
        for request_id in requests:
            if args.rebuild_checkpoint:
                graph.rebuild_projection(request_id)
            else:
                graph.sync_staff(request_id)
        removed = graph.purge_orphans()
        print(f'Reconciled {len(requests)} requests; deleted {removed} orphan graph threads')
    finally:
        graph.close()


if __name__ == '__main__':
    main()
