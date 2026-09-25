"""P1 Engine Runner end to end with a fake CLI (tests/team/fixtures/engine/fake_cli.py)
replaying recorded stream-json: events/progress/resume sinks, the raw log,
stdin delivery, stderr drain, CLI failure, cancel + timeout tree-kill (incl. a
grandchild orphaned from its parent — only the per-run Job reaches it), the
graceful CTRL_BREAK, Job binding before the CLI runs, .cmd/.bat shims, and the
task-queue cancel path."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from services.engine import EngineCancelled, EngineError, EngineRequest, EngineTimeout
from services.engine.adapters import get_adapter
from services.engine.runner import run_engine

FIX = Path(__file__).parent / "fixtures" / "engine"
FAKE = FIX / "fake_cli.py"
WIN = sys.platform == "win32"


def _alive(pid: int) -> bool:
    if not WIN:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        return code.value == 259
    finally:
        k32.CloseHandle(h)


def _wait(pred, deadline=10.0):
    end = time.time() + deadline
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def fake_cli(monkeypatch, engine, *args):
    """Keep the real adapter's stdin/env; swap argv for the fake CLI."""
    ad = get_adapter(engine)
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        launch.argv = [sys.executable, str(FAKE), *[str(a) for a in args]]
        return launch
    monkeypatch.setattr(ad, "build", build)
    return ad


class Sink:
    def __init__(self):
        self.events, self.progress, self.resume, self.spawned, self.exited = [], [], [], [], []
        self.stop = None

    def req(self, engine, cwd, **kw):
        def on_spawn(pid, stop):
            self.spawned.append(pid)
            self.stop = stop
        return EngineRequest(engine=engine, prompt=kw.pop("prompt", "the prompt ✓"), cwd=cwd,
                             on_event=self.events.append, on_progress=lambda p, m: self.progress.append((p, m)),
                             on_resume_id=self.resume.append, on_spawn=on_spawn, on_exit=self.exited.append,
                             **kw)


def test_runner_replays_recorded_claude_stream(tmp_path, monkeypatch):
    stdin_out = tmp_path / "stdin.txt"
    fake_cli(monkeypatch, "claude_code", "--replay", FIX / "claude_stream.jsonl", "--stdin-out", stdin_out)
    s = Sink()
    log = tmp_path / "task.jsonl"
    res = run_engine(s.req("claude_code", tmp_path, log_path=log, session_id="ws-1"))
    assert stdin_out.read_text(encoding="utf-8") == "the prompt ✓"          # prompt on stdin, verbatim
    assert log.read_text(encoding="utf-8") == (FIX / "claude_stream.jsonl").read_text(encoding="utf-8")
    kinds = [e["kind"] for e in s.events]
    assert kinds[0] == "start" and kinds[-1] == "done" and "error" not in kinds
    start = s.events[0]
    assert start["engine"] == "claude_code" and start["session_id"] == "ws-1" and start["prompt_len"] == 12
    assert s.resume == ["b91e2083-0212-41b0-97c7-7717c48820af"]
    msgs = [m for _, m in s.progress]
    assert msgs[0] == "launching claude" and "step 1: Edit" in msgs and msgs[-1] == "done"
    assert res.cost_usd == pytest.approx(0.731356) and res.log_path == str(log)
    assert s.events[-1]["cost_usd"] == pytest.approx(0.731356) and s.events[-1]["tool_count"] == 1
    assert s.spawned and s.exited == s.spawned


def test_runner_agy_stdin_message_and_codex_last_message(tmp_path, monkeypatch):
    stdin_out = tmp_path / "stdin.txt"
    fake_cli(monkeypatch, "antigravity", "--replay", FIX / "agy_stream.jsonl", "--stdin-out", stdin_out)
    s = Sink()
    res = run_engine(s.req("antigravity", tmp_path, prompt="hello"))
    assert json.loads(stdin_out.read_text(encoding="utf-8")) == {"event": "user", "message": {"content": "hello"}}
    assert res.engine_session_id == "bf4855a3-637f-4f95-ba85-53cfee7d0dfb"
    assert sum(1 for e in s.events if e["kind"] == "delta") == 6

    fake_cli(monkeypatch, "codex", "--replay", FIX / "codex_tools.jsonl")
    s = Sink()
    res = run_engine(s.req("codex", tmp_path, log_path=tmp_path / "cx.jsonl"))
    assert res.num_turns == 1 and res.tokens["cache_read"] == 21553
    assert [e["kind"] for e in s.events].count("usage") == 1


def test_runner_drains_stderr(tmp_path, monkeypatch):
    # 512 KB on stderr before any stdout would deadlock an undrained pipe.
    fake_cli(monkeypatch, "codex", "--stderr-kb", 512, "--replay", FIX / "codex_tools.jsonl")
    s = Sink()
    res = run_engine(s.req("codex", tmp_path, timeout_sec=30))
    assert res.num_turns == 1


def test_runner_cli_failure_raises_with_stderr(tmp_path, monkeypatch):
    fake_cli(monkeypatch, "codex", "--replay", FIX / "codex_turn_failed.jsonl", "--exit", 1)
    s = Sink()
    with pytest.raises(EngineError, match="codex exited with code 1"):
        run_engine(s.req("codex", tmp_path))
    assert s.events[-1]["kind"] == "error"
    assert sum(1 for e in s.events if e["kind"] == "retry") == 11


def test_runner_spawn_failure_is_engine_error(tmp_path, monkeypatch):
    ad = get_adapter("claude_code")
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        launch.argv = ["definitely-not-a-telecode-cli-xyz"]
        return launch
    monkeypatch.setattr(ad, "build", build)
    s = Sink()
    with pytest.raises(EngineError, match="not found on PATH"):
        run_engine(s.req("claude_code", tmp_path))
    assert s.events[-1]["kind"] == "error"


def _run_bg(req):
    out = {}

    def target():
        try:
            out["result"] = run_engine(req)
        except Exception as exc:  # noqa: BLE001
            out["exc"] = exc
    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, out


def test_cancel_kills_cli_and_grandchild(tmp_path, monkeypatch):
    pid_file = tmp_path / "gc.pid"
    fake_cli(monkeypatch, "claude_code", "--grandchild", pid_file, "--hang")
    cancel = threading.Event()
    s = Sink()
    t, out = _run_bg(s.req("claude_code", tmp_path, cancel_check=cancel.is_set, kill_grace_sec=0.5))
    assert _wait(lambda: pid_file.exists() and pid_file.read_text().strip())
    gc = int(pid_file.read_text())
    cli = s.spawned[0]
    assert _alive(gc) and _alive(cli)
    cancel.set()
    t.join(15)
    assert isinstance(out.get("exc"), EngineCancelled) and str(out["exc"]) == "Task cancelled"
    assert _wait(lambda: not _alive(gc)), "grandchild survived the cancel"
    assert not _alive(cli)
    assert s.events[-1] == {"kind": "error", "message": "cancelled"}


def test_timeout_kills_orphaned_grandchild(tmp_path, monkeypatch):
    """The grandchild's parent has exited, so there is no live parent link for
    taskkill /T to follow — only TerminateJobObject on the per-run Job reaches it."""
    pid_file = tmp_path / "orphan.pid"
    fake_cli(monkeypatch, "claude_code", "--orphan", pid_file, "--hang")
    s = Sink()
    t, out = _run_bg(s.req("claude_code", tmp_path, timeout_sec=2, kill_grace_sec=0.3))
    assert _wait(lambda: pid_file.exists() and pid_file.read_text().strip())
    orphan = int(pid_file.read_text())
    t.join(20)
    assert isinstance(out.get("exc"), EngineTimeout) and str(out["exc"]) == "timeout"
    if WIN:
        assert _wait(lambda: not _alive(orphan)), "orphaned grandchild survived the timeout"
    else:  # pragma: no cover - killpg covers the session
        assert _wait(lambda: not _alive(orphan))


@pytest.mark.skipif(not WIN, reason="CTRL_BREAK is Windows-only")
def test_cancel_is_graceful_first(tmp_path, monkeypatch):
    marker = tmp_path / "break.txt"
    fake_cli(monkeypatch, "claude_code", "--break-marker", marker, "--hang")
    s = Sink()
    t, out = _run_bg(s.req("claude_code", tmp_path, kill_grace_sec=5))
    assert _wait(lambda: s.stop is not None and any(True for _ in s.spawned))
    time.sleep(0.5)                                   # let the fake install its handler
    t0 = time.time()
    s.stop("cancelled")
    t.join(15)
    assert isinstance(out.get("exc"), EngineCancelled)
    assert marker.read_text() == "break"              # CTRL_BREAK reached the CLI
    assert time.time() - t0 < 4                       # it exited well inside the grace period


@pytest.mark.skipif(not WIN, reason="Job Objects are Windows-only")
def test_cli_and_its_children_are_in_the_lifetime_job(tmp_path, monkeypatch):
    import win32api
    import win32con
    import win32job
    import process as tc_process
    pid_file = tmp_path / "gc.pid"
    fake_cli(monkeypatch, "claude_code", "--grandchild", pid_file, "--hang")
    cancel = threading.Event()
    s = Sink()
    t, out = _run_bg(s.req("claude_code", tmp_path, cancel_check=cancel.is_set, kill_grace_sec=0))
    try:
        assert _wait(lambda: pid_file.exists() and pid_file.read_text().strip())
        job = tc_process._JOB_HANDLE
        assert job is not None
        for pid in (s.spawned[0], int(pid_file.read_text())):
            h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            try:
                assert win32job.IsProcessInJob(h, job), f"pid {pid} is not in the kill-on-close Job"
            finally:
                win32api.CloseHandle(h)
    finally:
        cancel.set()
        t.join(15)


# ── .cmd / .bat shims ───────────────────────────────────────────────────────

@pytest.mark.skipif(not WIN, reason="shims are Windows-only")
def test_npm_shim_is_unwrapped_to_node(tmp_path):
    from services.engine.spawn import _unwrap_shim
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    script = tmp_path / "node_modules" / "pkg" / "cli.js"
    script.write_text("")
    (tmp_path / "node.exe").write_text("")
    shim = tmp_path / "fakecli.cmd"
    shim.write_text('@ECHO off\r\nSET "_prog=%dp0%\\node.exe"\r\n'
                    '"%_prog%"  "%dp0%\\node_modules\\pkg\\cli.js" %*\r\n')
    argv = _unwrap_shim(shim, ["-p", "--model", "x"])
    assert argv == [str(tmp_path / "node.exe"), str(script.resolve()), "-p", "--model", "x"]


@pytest.mark.skipif(not WIN, reason="shims are Windows-only")
def test_batch_shim_runs_under_cmd_and_rejects_metacharacters(tmp_path, monkeypatch):
    from services.engine.spawn import resolve_argv, spawn
    bat = tmp_path / "fakeengine.bat"
    bat.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    argv = resolve_argv(["fakeengine", "--replay", str(FIX / "codex_tools.jsonl")])
    assert isinstance(argv, str) and " /d /s /c " in argv
    sp = spawn(["fakeengine", "--replay", str(FIX / "codex_tools.jsonl")], cwd=tmp_path)
    sp.proc.stdin.close()
    lines = sp.proc.stdout.read().splitlines()
    sp.proc.wait(10)
    sp.close()
    assert json.loads(lines[0])["type"] == "thread.started"
    with pytest.raises(EngineError, match="metacharacters"):
        resolve_argv(["fakeengine", "a&calc"])


# ── Through the task queue ──────────────────────────────────────────────────

@pytest.fixture
def _restore_handlers():
    from services.task.task_manager import get_task_queue
    q = get_task_queue()
    saved = dict(q.task_handlers)
    yield
    q.task_handlers.clear()
    q.task_handlers.update(saved)


def test_queue_cancel_stops_engine_tree_and_persists_events(tmp_data_root, tmp_path, monkeypatch,
                                                            _restore_handlers):
    from services.db import task_repo
    from services.engine.task_bridge import run_in_task, task_request
    from services.session import session_store
    from services.task.task_manager import TaskStatus, get_task_queue
    from services.task.task_utils import get_session_folder, get_session_id

    pid_file = tmp_path / "gc.pid"
    fake_cli(monkeypatch, "claude_code", "--replay", FIX / "codex_tools.jsonl", "--grandchild", pid_file, "--hang")

    def handler(**_kw):
        req = task_request("claude_code", prompt="x", cwd=get_session_folder(), sid=get_session_id(),
                           log_path=tmp_path / "raw.jsonl")
        req.kill_grace_sec = 0.3
        return run_in_task(req, sid=get_session_id(), ns=None)

    q = get_task_queue()
    q.register_handler("P1_FAKE", handler)
    session_store.create(session_id="ws-p1", data={})
    tid = q.submit_task("P1_FAKE", {}, session_id="ws-p1")
    assert _wait(lambda: pid_file.exists() and pid_file.read_text().strip())
    gc = int(pid_file.read_text())
    assert q.get_task(tid).killers, "runner stop not registered with the queue"
    assert q.cancel(tid) is True
    assert _wait(lambda: not _alive(gc)), "grandchild survived the queue cancel"
    assert _wait(lambda: not q.get_task(tid).pids)
    assert q.get_task(tid).status == TaskStatus.CANCELLED
    task_repo.flush()
    rec = task_repo.load_task(tid)
    assert rec["status"] == "cancelled"
    kinds = [e["kind"] for e in rec["metadata"]["events"]]
    assert kinds[0] == "start" and kinds[-1] == "error"
