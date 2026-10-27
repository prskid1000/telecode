"""LlamaSupervisor model switching: drain before switch, one switch per batch,
starvation cap, and no spawn while the old server still holds the GPU.

No llama-server runs: `_spawn_locked` is replaced by a fake that installs a
FakeProc, `kill_process_tree` marks it exited, and nvidia-smi is a FakeGpu.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from types import SimpleNamespace

import pytest

import process
from llamacpp import auto_cache, vram
from process import LlamaSupervisor, ModelLoadError


class FakeProc:
    _next_pid = 40000

    def __init__(self) -> None:
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("llama-server", timeout)
        return self.returncode


class FakeGpu:
    def __init__(self) -> None:
        self.base = 500
        self.held: dict[int, int] = {}       # pid -> MiB
        self.keep_after_kill: set[int] = set()

    def used(self) -> int:
        return self.base + sum(self.held.values())

    def pids(self) -> set[int]:
        return set(self.held)


@pytest.fixture
def sup(monkeypatch):
    t = SimpleNamespace(gpu=FakeGpu(), spawns=[], procs=[], order=[], fail=set(),
                        settings={"switch_max_wait_sec": 120.0, "unload_confirm_timeout_sec": 30.0})

    monkeypatch.setattr(process.cfg, "resolve_model", lambda n: n)
    monkeypatch.setattr(process.cfg, "switch_max_wait_sec", lambda: t.settings["switch_max_wait_sec"])
    monkeypatch.setattr(process.cfg, "unload_confirm_timeout_sec", lambda: t.settings["unload_confirm_timeout_sec"])
    monkeypatch.setattr(process.cfg, "idle_unload_sec", lambda: 0.0)
    monkeypatch.setattr(process.cfg, "watchdog_sec", lambda: 0.0)
    monkeypatch.setattr(process.cfg, "vram_ceiling_mib", lambda: 0)
    monkeypatch.setattr(process.cfg, "prevent_sleep", lambda: False)
    monkeypatch.setattr(process.llama_state, "save", lambda m: None)
    monkeypatch.setattr(auto_cache, "enabled", lambda m: False)
    monkeypatch.setattr(vram, "used_vram_mib", t.gpu.used)
    monkeypatch.setattr(vram, "compute_app_pids", t.gpu.pids)

    def fake_kill(pid, force=False, timeout=5.0):
        t.order.append(("kill", pid))
        for p in t.procs:
            if p.pid == pid:
                p.returncode = 1
        if pid not in t.gpu.keep_after_kill:
            t.gpu.held.pop(pid, None)
        return True

    monkeypatch.setattr(process, "kill_process_tree", fake_kill)

    async def fake_spawn(self, model_name, cache_scale=1.0):
        t.spawns.append(model_name)
        await asyncio.sleep(0.05)  # a load takes a while
        if model_name in t.fail:
            raise RuntimeError(f"llama-server exited during startup ({model_name})")
        proc = FakeProc()
        t.procs.append(proc)
        t.gpu.held[proc.pid] = 8000
        self._proc = proc
        self._active_model = model_name

    monkeypatch.setattr(LlamaSupervisor, "_spawn_locked", fake_spawn)

    s = LlamaSupervisor()
    s.t = t
    yield s


async def _hold(sup, model: str, release: asyncio.Event, tag: str) -> None:
    async with sup.use_model(model):
        sup.t.order.append(("start", tag, sup.active_model()))
        await release.wait()
        sup.t.order.append(("end", tag))


async def _until(pred, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


def _started(sup, tag: str) -> bool:
    return any(e[0] == "start" and e[1] == tag for e in sup.t.order)


@pytest.mark.asyncio
async def test_switch_waits_for_inflight_requests(sup, caplog):
    await sup.ensure_model("A")
    old_pid = sup.t.procs[0].pid
    r1 = asyncio.Event()
    a1 = asyncio.create_task(_hold(sup, "A", r1, "a1"))
    await _until(lambda: sup.inflight_count() == 1)

    b = asyncio.create_task(sup.ensure_model("B"))
    await asyncio.sleep(0.3)
    assert not b.done()
    assert sup.t.spawns == ["A"]
    assert ("kill", old_pid) not in sup.t.order, "an in-flight request's server was killed"

    with caplog.at_level(logging.INFO, logger="telecode.llamacpp"):
        r1.set()
        assert await b == "B"
    await a1
    assert sup.t.spawns == ["A", "B"]
    assert sup.t.order.index(("end", "a1")) < sup.t.order.index(("kill", old_pid))
    assert "===== llama-server unloaded (VRAM freed: 8000 MiB) =====" in caplog.text


@pytest.mark.asyncio
async def test_same_model_requests_run_before_the_switch(sup):
    await sup.ensure_model("A")
    old_pid = sup.t.procs[0].pid
    r1, r2 = asyncio.Event(), asyncio.Event()
    a1 = asyncio.create_task(_hold(sup, "A", r1, "a1"))
    await _until(lambda: sup.inflight_count() == 1)
    b = asyncio.create_task(sup.ensure_model("B"))
    await asyncio.sleep(0.1)

    # a request for the loaded model, arriving while B waits, still starts on A
    a2 = asyncio.create_task(_hold(sup, "A", r2, "a2"))
    await _until(lambda: _started(sup, "a2"))
    assert ("start", "a2", "A") in sup.t.order

    r1.set()
    await asyncio.sleep(0.3)
    assert not b.done(), "switched while a2 was still in flight"
    r2.set()
    assert await b == "B"
    await asyncio.gather(a1, a2)
    assert sup.t.spawns == ["A", "B"]
    assert sup.t.order.index(("end", "a2")) < sup.t.order.index(("kill", old_pid))


@pytest.mark.asyncio
async def test_starvation_cap_stops_new_requests_for_the_loaded_model(sup):
    sup.t.settings["switch_max_wait_sec"] = 0.3
    await sup.ensure_model("A")
    r1, rb, r2 = asyncio.Event(), asyncio.Event(), asyncio.Event()
    a1 = asyncio.create_task(_hold(sup, "A", r1, "a1"))
    await _until(lambda: sup.inflight_count() == 1)
    b = asyncio.create_task(_hold(sup, "B", rb, "b"))
    await asyncio.sleep(0.5)  # past the cap

    a2 = asyncio.create_task(_hold(sup, "A", r2, "a2"))
    await asyncio.sleep(0.3)
    assert not _started(sup, "a2"), "a new A request started after the switch passed its max wait"
    assert sup.inflight_count() == 1

    r1.set()
    await _until(lambda: _started(sup, "b"))
    assert sup.t.spawns == ["A", "B"]
    assert not _started(sup, "a2")

    rb.set()
    await _until(lambda: _started(sup, "a2"))
    assert sup.t.spawns == ["A", "B", "A"]
    r2.set()
    await asyncio.gather(a1, b, a2)


@pytest.mark.asyncio
async def test_waiters_for_one_model_share_one_spawn(sup):
    # from nothing loaded
    assert await asyncio.gather(sup.ensure_model("A"), sup.ensure_model("A")) == ["A", "A"]
    assert sup.t.spawns == ["A"]

    # and as a switch away from a busy model
    r1, rb = asyncio.Event(), asyncio.Event()
    a1 = asyncio.create_task(_hold(sup, "A", r1, "a1"))
    await _until(lambda: sup.inflight_count() == 1)
    b1 = asyncio.create_task(sup.ensure_model("B"))
    b2 = asyncio.create_task(_hold(sup, "B", rb, "b2"))
    await asyncio.sleep(0.1)
    r1.set()
    assert await b1 == "B"
    await _until(lambda: _started(sup, "b2"))
    assert sup.t.spawns == ["A", "B"]
    rb.set()
    await asyncio.gather(a1, b2)


@pytest.mark.asyncio
async def test_failed_load_reaches_every_waiter_with_one_attempt(sup):
    sup.t.fail.add("C")
    results = await asyncio.gather(sup.ensure_model("C"), sup.ensure_model("C"), return_exceptions=True)
    assert all(isinstance(r, ModelLoadError) for r in results), results
    assert sup.t.spawns == ["C"]
    assert sup.inflight_count() == 0


@pytest.mark.asyncio
async def test_no_spawn_while_the_old_pid_holds_vram(sup, caplog):
    sup.t.settings["unload_confirm_timeout_sec"] = 0.3
    await sup.ensure_model("A")
    old_pid = sup.t.procs[0].pid
    sup.t.gpu.keep_after_kill.add(old_pid)  # exits, but nvidia-smi still lists it

    with caplog.at_level(logging.INFO, logger="telecode.llamacpp"):
        with pytest.raises(ModelLoadError, match="has not released the GPU"):
            await sup.ensure_model("B")
    assert sup.t.spawns == ["A"]
    assert "still holds the GPU" in caplog.text

    # still held: the next request refuses too, without spawning
    with pytest.raises(ModelLoadError, match="has not released the GPU"):
        await sup.ensure_model("B")
    assert sup.t.spawns == ["A"]

    # released: the load goes ahead
    sup.t.gpu.held.pop(old_pid)
    assert await sup.ensure_model("B") == "B"
    assert sup.t.spawns == ["A", "B"]


@pytest.mark.asyncio
async def test_unload_waits_for_vram_to_drop(sup):
    sup.t.settings["unload_confirm_timeout_sec"] = 2.0
    await sup.ensure_model("A")
    old_pid = sup.t.procs[0].pid
    sup.t.gpu.keep_after_kill.add(old_pid)

    async def release_later():
        await asyncio.sleep(0.4)
        sup.t.gpu.held.pop(old_pid)

    releaser = asyncio.create_task(release_later())
    assert await sup.ensure_model("B") == "B"
    await releaser
    assert sup.t.spawns == ["A", "B"]
    assert old_pid not in sup.t.gpu.held


@pytest.mark.asyncio
async def test_cancelled_request_gives_its_lease_back(sup):
    await sup.ensure_model("A")
    never = asyncio.Event()
    a1 = asyncio.create_task(_hold(sup, "A", never, "a1"))
    await _until(lambda: sup.inflight_count() == 1)
    a1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a1
    assert sup.inflight_count() == 0
    assert await sup.ensure_model("B") == "B"
