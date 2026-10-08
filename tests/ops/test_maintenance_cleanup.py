from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from concierge_kiosk.i18n import SUPPORTED_LANGUAGES, text
from concierge_kiosk.persistence.sqlite_store import Store

ROOT = Path(__file__).resolve().parents[2]


def test_guest_copy_catalog_has_all_supported_languages():
    for language in SUPPORTED_LANGUAGES:
        assert text('knowledge.abstain', language)
        assert text('emergency.alert_queued', language)
        assert text('request.draft_cleared', language)


def test_importing_main_does_not_create_or_migrate_database(tmp_path):
    db = tmp_path / 'must-not-exist.sqlite3'
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': str(ROOT / 'src'),
        'CONCIERGE_DB_PATH': str(db),
    })
    code = (
        'from pathlib import Path; '
        'import concierge_kiosk.main; '
        f'print(Path({str(db)!r}).exists())'
    )
    result = subprocess.run(
        [sys.executable, '-c', code], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    assert result.stdout.strip().splitlines()[-1] == 'False'
    assert not db.exists()


def test_metric_buffer_batches_diagnostic_writes(tmp_path):
    store = Store(tmp_path / 'metrics.sqlite3')
    store.metric('rag.test', 'en', 1_700_000_000)
    with store.connection() as con:
        assert con.execute('SELECT COUNT(*) FROM metric_counts').fetchone()[0] == 0
    store.flush_metrics()
    with store.connection() as con:
        row = con.execute(
            'SELECT language,metric,count FROM metric_counts WHERE metric=?', ('rag.test',)
        ).fetchone()
    assert dict(row) == {'language': 'en', 'metric': 'rag.test', 'count': 1}


def test_metric_buffer_auto_flushes_at_batch_threshold(tmp_path):
    store = Store(tmp_path / 'metrics-batch.sqlite3')
    for _ in range(store._METRIC_FLUSH_BATCH):
        store.metric('voice.test', 'vi', 1_700_000_000)
    with store.connection() as con:
        count = con.execute(
            'SELECT count FROM metric_counts WHERE metric=? AND language=?',
            ('voice.test', 'vi'),
        ).fetchone()[0]
    assert count == store._METRIC_FLUSH_BATCH


