"""Print the active knowledge-release evidence record for operator review."""
from __future__ import annotations
import json

from concierge_kiosk.core.settings import load_settings
from concierge_kiosk.persistence.sqlite_store import Store


def main() -> None:
    cfg = load_settings()
    store = Store(cfg.db_path)
    with store.connection() as con:
        row = con.execute(
            'SELECT * FROM knowledge_release_evidence WHERE property_id=?',
            (cfg.property_id,),
        ).fetchone()
    if row is None:
        raise SystemExit('No signed knowledge release evidence is recorded for this property')
    print(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
