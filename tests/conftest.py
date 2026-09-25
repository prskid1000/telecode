"""Suite-wide fixtures.

Every test gets its own ``telecode.db`` under its tmp dir, so nothing a test
submits to the task queue (write-through) ever lands in the real
``data/telecode.db``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True, scope="session")
def _session_db(tmp_path_factory):
    """Backstop for threads that outlive their test (a task-queue worker
    finishing after teardown still writes through): between and after tests
    the DB is a session tmp file, never ``data/telecode.db``. Deliberately not
    reset at session end."""
    from services.db import core
    core._override_path = tmp_path_factory.mktemp("db") / "telecode-session.db"
    yield


@pytest.fixture(autouse=True)
def _isolated_db(_session_db, tmp_path, monkeypatch):
    from services.db import core
    monkeypatch.setattr(core, "_override_path", tmp_path / "telecode.db")
    yield
