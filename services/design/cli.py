"""Headless TeleDesign CLI over the REST API (docs/teledesign-parity.md B "Headless CLI").

    python -m services.design.cli [--base URL] [--json] <command> …

Talks to a running telecode proxy (`http://127.0.0.1:<proxy.port>`, or `--base`, or
`TELECODE_DESIGN_URL`) — it never imports the design services, so it drives the same
projects, chats and exports the web UI shows, and works from any shell or script.

Commands
    projects  [--query Q] [--archived]                  list projects
    create    TITLE [--kind K] [--system ID]            create a project (prints its id)
    get       PROJECT                                   project record, chats, files
    files     PROJECT [--prefix P]                      list files
    send      PROJECT TEXT [--chat ID] [--engine E] [--model M] [--effort E] [--local]
              [--no-wait] [--timeout S]                 send a turn and stream its events
    run       --prompt TEXT [--project ID | --title T] [--kind K] [--in FILE.pen]
              [--tasks FILE] [--engine E] [--model M] [--effort E] [--export KIND] [--out PATH]
                                                        batch: create/import, run turn(s), export
    export    PROJECT KIND [--file F] [--options JSON] [--out PATH] [--timeout S]
    screenshot PROJECT [--file F] [--width W] [--height H] [--full-page] [--selector S] --out PATH
    verify    PROJECT [--task T] [--files F …]          verifier report (directed when --task)
    console   PROJECT [--file F] [--source auto|live|headless]

`--json` prints machine-readable JSON (one object per command; `send`/`run` print one
JSON object per event line, then the final turn). Exit status: 0 ok, 1 API/turn
failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

TERMINAL = ("done", "failed", "cancelled", "error")
EXPORT_KINDS = ("html", "zip", "pdf", "pptx", "png", "mp4", "handoff")


class CLIError(RuntimeError):
    pass


def default_base() -> str:
    env = os.environ.get("TELECODE_DESIGN_URL")
    if env:
        return env.rstrip("/")
    port = 1235
    try:
        import config
        port = int(config.get_nested("proxy.port", 1235))
    except Exception:
        pass
    return f"http://127.0.0.1:{port}"


class Client:
    def __init__(self, base: str, timeout: float = 60.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _open(self, method: str, path: str, *, body: Any = None, data: Optional[bytes] = None,
              ctype: Optional[str] = None, params: Optional[Dict[str, Any]] = None,
              timeout: Optional[float] = None, accept: Optional[str] = None):
        url = self.base + path
        if params:
            q = {k: v for k, v in params.items() if v not in (None, "", False)}
            if q:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(q)
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            ctype = "application/json"
        if ctype:
            headers["Content-Type"] = ctype
        if accept:
            headers["Accept"] = accept
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")[:600]
            try:
                raw = json.loads(raw).get("error", raw)
            except Exception:
                pass
            raise CLIError(f"HTTP {e.code} {method} {path}: {raw}") from None
        except urllib.error.URLError as e:
            raise CLIError(f"telecode is not reachable at {self.base} ({e.reason}) — is it running with "
                           f"proxy.enabled?") from None

    def json(self, method: str, path: str, **kw: Any) -> Any:
        with self._open(method, path, **kw) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}

    def raw(self, method: str, path: str, **kw: Any) -> Tuple[bytes, str]:
        with self._open(method, path, **kw) as r:
            return r.read(), r.headers.get("Content-Type", "")

    def events(self, pid: str, chat_id: Optional[str] = None, timeout: float = 3600) -> Iterator[Tuple[str, Any]]:
        """SSE `…/events` → (type, data) pairs; heartbeats skipped."""
        resp = self._open("GET", f"/api/design/projects/{pid}/events", params={"chat_id": chat_id},
                          timeout=timeout, accept="text/event-stream")
        etype, buf = "message", []
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if buf:
                        payload = "\n".join(buf)
                        try:
                            yield etype, json.loads(payload)
                        except ValueError:
                            yield etype, payload
                    etype, buf = "message", []
                elif line.startswith(":"):
                    continue
                elif line.startswith("event:"):
                    etype = line[6:].strip()
                elif line.startswith("data:"):
                    buf.append(line[5:].lstrip())


# ── output ───────────────────────────────────────────────────────────────

class Out:
    def __init__(self, as_json: bool) -> None:
        self.as_json = as_json

    def result(self, obj: Any, human: Optional[str] = None) -> None:
        if self.as_json or human is None:
            print(json.dumps(obj, indent=None if self.as_json else 2, ensure_ascii=False, default=str))
        else:
            print(human)

    def event(self, etype: str, data: Any) -> None:
        if self.as_json:
            print(json.dumps({"event": etype, "data": data}, ensure_ascii=False, default=str), flush=True)
            return
        if etype == "delta" and isinstance(data, dict):
            sys.stdout.write(str(data.get("text") or ""))
            sys.stdout.flush()
        elif etype == "tool" and isinstance(data, dict):
            print(f"\n  · {data.get('name')} {str(data.get('input_preview') or '')[:100]}", flush=True)
        elif etype == "files" and isinstance(data, dict):
            print(f"\n  ✎ {', '.join(data.get('changed') or [])} (v{data.get('version')})", flush=True)
        elif etype == "check" and isinstance(data, dict):
            print(f"\n  ✓ {data.get('stage')}: {data.get('status')}", flush=True)
        elif etype == "error":
            print(f"\n  ! {data}", file=sys.stderr, flush=True)

    def info(self, msg: str) -> None:
        if not self.as_json:
            print(msg, file=sys.stderr, flush=True)


# ── commands ─────────────────────────────────────────────────────────────

def _pid(c: Client, ref: str) -> str:
    """A project id, or a unique title match."""
    ref = (ref or "").strip()
    if len(ref) == 32 and all(ch in "0123456789abcdef" for ch in ref.lower()):
        return ref.lower()
    rows = c.json("GET", "/api/design/projects").get("projects", [])
    hits = [p for p in rows if (p.get("title") or "").lower() == ref.lower()] or \
           [p for p in rows if ref.lower() in (p.get("title") or "").lower()]
    if len(hits) != 1:
        raise CLIError(f"project '{ref}' matches {len(hits)} projects — pass its id")
    return hits[0]["id"]


def cmd_projects(c: Client, a: argparse.Namespace, out: Out) -> int:
    rows = c.json("GET", "/api/design/projects", params={"include_archived": "1" if a.archived else None})
    projects = [p for p in rows.get("projects", []) if not a.query or a.query.lower() in (p.get("title") or "").lower()]
    lines = [f"{p['id']}  {p.get('kind', ''):<14} {p.get('title') or ''}" for p in projects]
    out.result({"projects": projects}, "\n".join(lines) or "(no projects)")
    return 0


def cmd_create(c: Client, a: argparse.Namespace, out: Out) -> int:
    body: Dict[str, Any] = {"title": a.title, "kind": a.kind}
    if a.system:
        body["design_system_id"] = a.system
    proj = c.json("POST", "/api/design/projects", body=body)["project"]
    out.result({"project": proj, "url": f"{c.base}/design?project={proj['id']}"}, proj["id"])
    return 0


def cmd_get(c: Client, a: argparse.Namespace, out: Out) -> int:
    pid = _pid(c, a.project)
    res = {"project": c.json("GET", f"/api/design/projects/{pid}")["project"],
           "chats": c.json("GET", f"/api/design/projects/{pid}/chats").get("chats"),
           "files": c.json("GET", f"/api/design/projects/{pid}/files").get("files")}
    out.result(res)
    return 0


def cmd_files(c: Client, a: argparse.Namespace, out: Out) -> int:
    pid = _pid(c, a.project)
    files = c.json("GET", f"/api/design/projects/{pid}/files", params={"prefix": a.prefix}).get("files") or []
    out.result({"files": files}, "\n".join(f"{f['size']:>10}  {f['path']}" for f in files) or "(no files)")
    return 0


def send_turn(c: Client, pid: str, text: str, out: Out, *, chat_id: Optional[str] = None,
              engine: str = "", model: str = "", effort: str = "", local: bool = False,
              wait: bool = True, timeout: float = 1800) -> Dict[str, Any]:
    if not chat_id:
        chat_body: Dict[str, Any] = {"title": "From CLI"}
        if engine:
            chat_body["engine"] = engine
        chat_id = c.json("POST", f"/api/design/projects/{pid}/chats", body=chat_body)["chat"]["id"]
    body: Dict[str, Any] = {"text": text}
    for k, v in (("engine", engine), ("model", model), ("effort", effort)):
        if v:
            body[k] = v
    if local:
        body["is_local"] = True
    if not wait:
        res = c.json("POST", f"/api/design/projects/{pid}/chats/{chat_id}/turns", body=body)
        return {"chat_id": chat_id, "turn": res.get("turn"), "status": (res.get("turn") or {}).get("status")}

    # Subscribe first, so no event of the turn is missed, then start it.
    import queue
    import threading
    q: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
    stop = threading.Event()

    def _pump() -> None:
        try:
            for ev in c.events(pid, chat_id, timeout=timeout + 60):
                q.put(ev)
                if stop.is_set():
                    return
        except Exception as e:
            q.put(("__error__", str(e)))
    threading.Thread(target=_pump, daemon=True).start()
    time.sleep(0.3)
    res = c.json("POST", f"/api/design/projects/{pid}/chats/{chat_id}/turns", body=body)
    turn = res.get("turn") or {}
    tid = turn.get("id")
    deadline = time.monotonic() + timeout
    final: Optional[Dict[str, Any]] = None
    last_poll = 0.0
    while time.monotonic() < deadline:
        try:
            etype, data = q.get(timeout=2.0)
        except Exception:
            etype, data = "", None
        if etype == "__error__":
            out.info(f"(event stream ended: {data}; polling)")
        elif etype:
            if isinstance(data, dict) and data.get("turn_id") not in (None, tid) and etype != "turn":
                pass
            else:
                out.event(etype, data)
            if etype == "turn" and isinstance(data, dict) and data.get("id") == tid and data.get("status") in TERMINAL:
                final = data
                break
        if time.monotonic() - last_poll > 10:  # belt and braces: the stream may drop
            last_poll = time.monotonic()
            turns = c.json("GET", f"/api/design/projects/{pid}/chats/{chat_id}/turns").get("turns", [])
            hit = next((t for t in turns if t.get("id") == tid), None)
            if hit and hit.get("status") in TERMINAL:
                final = hit
                break
    stop.set()
    if final is None:
        return {"chat_id": chat_id, "turn": turn, "status": "timeout"}
    return {"chat_id": chat_id, "turn": {k: final.get(k) for k in ("id", "status", "text", "changed_files",
                                                                    "version", "usage", "error", "form")},
            "status": final.get("status")}


def cmd_send(c: Client, a: argparse.Namespace, out: Out) -> int:
    pid = _pid(c, a.project)
    res = send_turn(c, pid, a.text, out, chat_id=a.chat, engine=a.engine, model=a.model, effort=a.effort,
                    local=a.local, wait=not a.no_wait, timeout=a.timeout)
    if not out.as_json:
        print()
    out.result(res, f"turn {res.get('status')} · chat {res['chat_id']} · changed: "
                    f"{', '.join((res.get('turn') or {}).get('changed_files') or []) or '-'}")
    return 0 if res.get("status") in ("done", "queued", "running", None) else 1


def export_project(c: Client, pid: str, kind: str, out: Out, *, file: str = "", options: Optional[dict] = None,
                   dest: str = "", timeout: float = 600) -> Dict[str, Any]:
    if kind not in EXPORT_KINDS:
        raise CLIError(f"kind must be one of {', '.join(EXPORT_KINDS)}")
    body: Dict[str, Any] = {"options": options or {}}
    if file:
        body["file"] = file
    job = c.json("POST", f"/api/design/projects/{pid}/export/{kind}", body=body)
    jid = job.get("job_id")
    deadline = time.monotonic() + timeout
    st: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        st = c.json("GET", f"/api/design/projects/{pid}/export/jobs/{jid}")
        if st.get("status") in TERMINAL:
            break
        out.info(f"  exporting… {int(float(st.get('progress') or 0) * 100)}%")
        time.sleep(1.5)
    if st.get("status") != "done":
        raise CLIError(f"export {st.get('status') or 'timed out'}: {st.get('error') or ''}")
    res: Dict[str, Any] = {"job_id": jid, **st}
    if dest:
        data, _ = c.raw("GET", f"/api/design/projects/{pid}/export/download/{jid}", timeout=300)
        p = Path(dest)
        if p.is_dir():
            p = p / (st.get("filename") or f"export.{kind}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        res["saved"] = str(p.resolve())
    return res


def cmd_export(c: Client, a: argparse.Namespace, out: Out) -> int:
    pid = _pid(c, a.project)
    try:
        options = json.loads(a.options) if a.options else {}
    except ValueError:
        raise CLIError("--options must be JSON")
    res = export_project(c, pid, a.kind, out, file=a.file, options=options, dest=a.out, timeout=a.timeout)
    out.result(res, res.get("saved") or c.base + str(res.get("download_url") or ""))
    return 0


def cmd_screenshot(c: Client, a: argparse.Namespace, out: Out) -> int:
    pid = _pid(c, a.project)
    fmt = "jpeg" if a.out.lower().endswith((".jpg", ".jpeg")) else "png"
    body: Dict[str, Any] = {"file": a.file, "width": a.width, "height": a.height, "full_page": a.full_page,
                            "format": fmt}
    if a.selector:
        body["selector"] = a.selector
    data, ctype = c.raw("POST", f"/api/design/projects/{pid}/screenshot", body=body, timeout=180)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    out.result({"saved": str(p.resolve()), "bytes": len(data), "content_type": ctype}, str(p.resolve()))
    return 0


def cmd_verify(c: Client, a: argparse.Namespace, out: Out) -> int:
    pid = _pid(c, a.project)
    body: Dict[str, Any] = {"task": a.task or ""}
    if a.files:
        body["files"] = a.files
    rep = c.json("POST", f"/api/design/projects/{pid}/verify", body=body, timeout=900)
    lines = [f"{rep.get('mode')} check: {rep.get('status')}"]
    for i in rep.get("issues") or []:
        lines.append(f"  [{i.get('severity')}] {i.get('file', '')} {i.get('where', '')}: {i.get('what')}")
    if rep.get("note"):
        lines.append(rep["note"])
    out.result(rep, "\n".join(lines))
    return 0 if rep.get("status") == "pass" else 1


def cmd_console(c: Client, a: argparse.Namespace, out: Out) -> int:
    pid = _pid(c, a.project)
    res: Dict[str, Any] = {}
    if a.source in ("auto", "live"):
        res["live"] = c.json("GET", f"/api/design/projects/{pid}/console/live", params={"file": a.file})
    if a.source in ("auto", "headless"):
        res["headless"] = c.json("GET", f"/api/design/projects/{pid}/console",
                                 params={"file": a.file or "index.html"}, timeout=120)
    out.result(res)
    return 0


def _multipart(field: str, path: Path) -> Tuple[bytes, str]:
    boundary = "----td" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode("utf-8")
    return head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8"), \
        f"multipart/form-data; boundary={boundary}"


def cmd_run(c: Client, a: argparse.Namespace, out: Out) -> int:
    """Batch mode: project (existing, or new from --title / --in) → one turn per prompt → optional export."""
    prompts: List[str] = [a.prompt] if a.prompt else []
    if a.tasks:
        text = Path(a.tasks).read_text(encoding="utf-8")
        try:
            data = json.loads(text)
            prompts += [str(t.get("prompt") if isinstance(t, dict) else t) for t in data]
        except ValueError:
            prompts += [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not prompts:
        raise CLIError("run needs --prompt and/or --tasks")
    if a.project:
        pid = _pid(c, a.project)
    else:
        title = a.title or (Path(a.input).stem if a.input else prompts[0][:60])
        pid = c.json("POST", "/api/design/projects", body={"title": title, "kind": a.kind})["project"]["id"]
        out.info(f"created project {pid}")
    if a.input:
        data, ctype = _multipart("file", Path(a.input))
        res = c.json("POST", f"/api/design/projects/{pid}/import/pen", data=data, ctype=ctype, timeout=300)
        out.info(f"imported {res.get('path')}")
    chat_id = None
    results = []
    status = "done"
    for i, text in enumerate(prompts, 1):
        out.info(f"turn {i}/{len(prompts)}")
        r = send_turn(c, pid, text, out, chat_id=chat_id, engine=a.engine, model=a.model, effort=a.effort,
                      local=a.local, timeout=a.timeout)
        chat_id = r["chat_id"]
        results.append(r)
        if r.get("status") != "done":
            status = r.get("status") or "failed"
            break
    report: Dict[str, Any] = {"project_id": pid, "chat_id": chat_id, "status": status,
                              "turns": [r.get("turn") for r in results]}
    if a.export and status == "done":
        report["export"] = export_project(c, pid, a.export, out, dest=a.out or "", timeout=a.timeout)
    if not out.as_json:
        print()
    out.result(report, f"{status} · project {pid}" + (f" · {report['export'].get('saved')}"
                                                      if report.get("export", {}).get("saved") else ""))
    return 0 if status == "done" else 1


# ── argparse ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m services.design.cli", description="Headless TeleDesign over REST.")
    ap.add_argument("--base", default=None, help="telecode proxy URL (default: settings proxy.port)")
    ap.add_argument("--json", action="store_true", help="machine-readable JSON output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("projects"); p.add_argument("--query", default=""); p.add_argument("--archived", action="store_true")
    p.set_defaults(fn=cmd_projects)
    p = sub.add_parser("create"); p.add_argument("title"); p.add_argument("--kind", default="prototype")
    p.add_argument("--system", default=""); p.set_defaults(fn=cmd_create)
    p = sub.add_parser("get"); p.add_argument("project"); p.set_defaults(fn=cmd_get)
    p = sub.add_parser("files"); p.add_argument("project"); p.add_argument("--prefix", default=""); p.set_defaults(fn=cmd_files)

    def turn_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--engine", default="", help="claude_code | codex | antigravity")
        p.add_argument("--model", default="")
        p.add_argument("--effort", default="")
        p.add_argument("--local", action="store_true", help="route the turn through the local model")
        p.add_argument("--timeout", type=float, default=1800)

    p = sub.add_parser("send"); p.add_argument("project"); p.add_argument("text")
    p.add_argument("--chat", default=None); p.add_argument("--no-wait", action="store_true"); turn_opts(p)
    p.set_defaults(fn=cmd_send)
    p = sub.add_parser("run"); p.add_argument("--prompt", default=""); p.add_argument("--tasks", default="",
                                                                                      help="JSON list or one prompt per line")
    p.add_argument("--project", default=""); p.add_argument("--title", default=""); p.add_argument("--kind", default="prototype")
    p.add_argument("--in", dest="input", default="", help="a .pen file to import first")
    p.add_argument("--export", default="", choices=("",) + EXPORT_KINDS); p.add_argument("--out", default="")
    turn_opts(p); p.set_defaults(fn=cmd_run)
    p = sub.add_parser("export"); p.add_argument("project"); p.add_argument("kind", choices=EXPORT_KINDS)
    p.add_argument("--file", default=""); p.add_argument("--options", default=""); p.add_argument("--out", default="")
    p.add_argument("--timeout", type=float, default=600); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("screenshot"); p.add_argument("project"); p.add_argument("--file", default="index.html")
    p.add_argument("--width", type=int, default=1280); p.add_argument("--height", type=int, default=800)
    p.add_argument("--full-page", action="store_true"); p.add_argument("--selector", default="")
    p.add_argument("--out", required=True); p.set_defaults(fn=cmd_screenshot)
    p = sub.add_parser("verify"); p.add_argument("project"); p.add_argument("--task", default="")
    p.add_argument("--files", nargs="*", default=None); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("console"); p.add_argument("project"); p.add_argument("--file", default="")
    p.add_argument("--source", choices=("auto", "live", "headless"), default="auto"); p.set_defaults(fn=cmd_console)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    out = Out(a.json)
    c = Client(a.base or default_base())
    try:
        return int(a.fn(c, a, out) or 0)
    except CLIError as e:
        if a.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    sys.exit(main())
