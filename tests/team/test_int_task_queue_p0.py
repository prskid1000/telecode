"""Task queue regressions: B10 (shared cancel kills the real process tree,
never overwrites a finished task, stamps completed_at), B13 (timeout
enforced), B12 (separate interactive/background pools), B6 (eviction)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest

from services.task import task_manager as tm
from services.task.task_manager import TaskQueue, TaskStatus, cancel_task, get_task_queue


def _alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        return code.value == 259                      # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def _wait(pred, deadline=10.0):
    end = time.time() + deadline
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


@pytest.fixture(autouse=True)
def _restore_handlers():
    q = get_task_queue()
    saved = dict(q.task_handlers)
    yield
    q.task_handlers.clear()
    q.task_handlers.update(saved)


def _spawning_handler(pid_file):
    """Mimics a CLI handler: shell=True spawn whose child spawns a grandchild,
    registered via task_utils.track_process, reading stdout until EOF."""
    from services.task.task_utils import is_cancelled, track_process, untrack_process
    grandchild = (
        "import subprocess,sys,time;"
        f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']);"
        f"open(r'{pid_file}','w').write(str(p.pid));"
        "print('ready',flush=True);time.sleep(120)"
    )

    def handler(**_kw):
        proc = subprocess.Popen([sys.executable, "-c", grandchild], stdout=subprocess.PIPE,
                                text=True, shell=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pid = track_process(proc)
        try:
            for _line in proc.stdout:
                if is_cancelled():
                    break
            proc.wait(timeout=10)
        finally:
            untrack_process(pid)
        raise RuntimeError("cli exited")
    return handler


def test_b10_cancel_kills_cli_process_tree(tmp_data_root, tmp_path):
    q = get_task_queue()
    pid_file = tmp_path / "gc.pid"
    q.register_handler("P0_SPAWN", _spawning_handler(pid_file))
    tid = q.submit_task("P0_SPAWN", {}, session_id="ws-kill")
    assert _wait(lambda: pid_file.exists() and pid_file.read_text().strip())
    gc_pid = int(pid_file.read_text())
    shell_pid = q.get_task(tid).pids[0]
    assert _alive(gc_pid)

    assert cancel_task(tid) is True
    assert _wait(lambda: not _alive(gc_pid)), "grandchild CLI survived the cancel"
    assert not _alive(shell_pid)
    t = q.get_task(tid)
    assert t.status == TaskStatus.CANCELLED and t.completed_at is not None
    # the handler's own later failure does not overwrite the cancel
    time.sleep(0.3)
    assert q.get_task(tid).status == TaskStatus.CANCELLED


def test_b13_timeout_kills_tree_and_fails_task(tmp_data_root, tmp_path):
    q = get_task_queue()
    pid_file = tmp_path / "gc2.pid"
    q.register_handler("P0_SPAWN", _spawning_handler(pid_file))
    tid = q.submit_task("P0_SPAWN", {}, session_id="ws-timeout", task_timeout_seconds=2)
    assert _wait(lambda: pid_file.exists() and pid_file.read_text().strip())
    gc_pid = int(pid_file.read_text())
    assert _wait(lambda: q.get_task(tid).status == TaskStatus.FAILED, deadline=15)
    t = q.get_task(tid)
    assert t.error == "timeout" and t.completed_at is not None
    assert _wait(lambda: not _alive(gc_pid))


def test_b10_cancel_never_overwrites_finished_task(tmp_data_root):
    q = get_task_queue()
    q.register_handler("P0_OK", lambda **kw: {"ok": 1})
    q.register_handler("P0_ERR", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    ok = q.submit_task("P0_OK", {}, session_id="ws-fin")
    err = q.submit_task("P0_ERR", {}, session_id="ws-fin")
    assert _wait(lambda: q.get_task(ok).status == TaskStatus.COMPLETED)
    assert _wait(lambda: q.get_task(err).status == TaskStatus.FAILED)
    assert cancel_task(ok) is False and cancel_task(err) is False
    assert q.get_task(ok).status == TaskStatus.COMPLETED
    assert q.get_task(err).status == TaskStatus.FAILED and q.get_task(err).error == "boom"
    assert cancel_task("no-such-task") is False


def test_b10_api_cancel_finished_returns_409(tmp_data_root):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from proxy import api_tasks
    q = get_task_queue()
    q.register_handler("P0_OK", lambda **kw: {"ok": 1})
    tid = q.submit_task("P0_OK", {}, session_id="ws-api")
    assert _wait(lambda: q.get_task(tid).status == TaskStatus.COMPLETED)

    async def go():
        app = web.Application()
        api_tasks.register_routes(app)
        c = TestClient(TestServer(app))
        await c.start_server()
        try:
            r = await c.post(f"/api/tasks/{tid}/cancel")
            return r.status, await r.json()
        finally:
            await c.close()

    status, body = asyncio.run(go())
    assert status == 409 and body["task"]["status"] == "completed"


def test_b12_background_saturation_does_not_block_interactive(tmp_data_root):
    q = TaskQueue(max_workers=1, background_workers=1)
    release = threading.Event()
    q.register_handler("P0_BLOCK", lambda **kw: (release.wait(10), {"x": 1})[1])
    q.register_handler("P0_FAST", lambda **kw: {"fast": True})
    try:
        bg = [q.submit_task("P0_BLOCK", {}, metadata={"trigger_id": "r"}, session_id=f"bg{i}")
              for i in range(3)]
        assert all(q.get_task(t).pool == "background" for t in bg)
        fast = q.submit_task("P0_FAST", {}, session_id="ui")
        assert q.get_task(fast).pool == "interactive"
        assert _wait(lambda: q.get_task(fast).status == TaskStatus.COMPLETED, deadline=3)
        assert q.get_task(bg[-1]).status == TaskStatus.PENDING          # still queued behind bg[0]
    finally:
        release.set()


def test_b12_pool_classification_and_sizes(tmp_data_root, monkeypatch):
    import config
    assert tm._classify_pool({"trigger_id": "t"}) == "background"
    assert tm._classify_pool({"run_id": "x"}) == "background"
    assert tm._classify_pool({"source": "design"}) == "interactive"
    monkeypatch.setattr(config, "get_nested", lambda path, default=None:
                        {"tasks.pools.interactive_workers": 7, "tasks.pools.background_workers": 2}.get(path, default))
    assert config.tasks_interactive_workers() == 7 and config.tasks_background_workers() == 2
    q = TaskQueue()
    assert q.pools["interactive"]._max_workers == 7 and q.pools["background"]._max_workers == 2


def test_b6_evicts_old_finished_tasks_keeps_recent(tmp_data_root, monkeypatch):
    monkeypatch.setattr(tm, "FINISHED_KEEP_MIN", 2)
    q = TaskQueue(max_workers=1, background_workers=1)
    now = datetime.now()
    for i in range(5):
        t = tm.Task(task_id=f"old{i}", task_type="X", status=TaskStatus.COMPLETED,
                    completed_at=now - timedelta(hours=2, minutes=i))
        q.tasks[t.task_id] = t
    q.tasks["recent"] = tm.Task(task_id="recent", task_type="X", status=TaskStatus.COMPLETED,
                                completed_at=now - timedelta(minutes=1))
    q.tasks["live"] = tm.Task(task_id="live", task_type="X", status=TaskStatus.RUNNING,
                              created_at=now - timedelta(days=1))
    removed = q.evict_finished(force=True)
    # keep-min 2 newest finished (recent, old0); rest are > 1h old → evicted; running kept
    assert removed == 4
    assert set(q.tasks) == {"recent", "old0", "live"}
