"""Daily retention cleanup; remove orphaned opaque LangGraph checkpoints too."""
import time
from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.persistence.sqlite_store import Store

if __name__ == "__main__":
    cfg = load_settings()
    store = Store(cfg.db_path)
    store.purge(int(time.time()), cfg.retention_days)
    if cfg.orchestrator == 'langgraph':
        from concierge_kiosk.agent.orchestration.graph import ConciergeGraph
        from concierge_kiosk.domain.service_requests import Workflows
        graph = ConciergeGraph(Workflows(store, cfg.property_id, cfg.proposal_ttl_seconds),
                               cfg.db_path.with_name(cfg.db_path.stem + '-graph.sqlite3'))
        try:
            removed = graph.purge_orphans()
        finally:
            graph.close()
        print(f'Database retention completed; removed {removed} orphan graph threads')
    else:
        print('Expired sessions and eligible retained data purged')
