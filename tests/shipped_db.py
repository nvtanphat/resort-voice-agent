"""Private copies of the tracked knowledge DB for tests.

Opening ``data/concierge.sqlite3`` directly rewrites it (headers, WAL) and dirties the
working tree on every run.  Tests that only read it use ``shipped_store()`` instead.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from concierge_kiosk.persistence.sqlite_store import Store

_SOURCE = Path(__file__).resolve().parents[1] / "data" / "concierge.sqlite3"


def copy_shipped_db(directory: Path) -> Path:
    target = Path(directory) / "shipped.sqlite3"
    shutil.copyfile(_SOURCE, target)
    return target


def shipped_store() -> Store:
    directory = tempfile.mkdtemp(prefix="shipped-db-")
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    return Store(str(copy_shipped_db(Path(directory))))
