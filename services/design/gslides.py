"""Send a deck to Google Slides through the `gws` Google Workspace CLI.

    status()            {installed, authed, user?, binary?, instructions?}
    start(pid, body)    background job: PPTX export → Drive upload with conversion
    get_job(job_id)     {status, progress, message, url?, error?}

Auth is never initiated here. `gws auth status` is checked first (read-only, no
network login); when the CLI is missing or not signed in, the caller gets the
exact instructions to run `gws auth login` themselves in a terminal. The upload
is `gws drive files create --upload <deck.pptx>` with the Google Slides MIME type
in the metadata, so Drive converts the PowerPoint into a native presentation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.design import store

log = logging.getLogger("telecode.services.design.gslides")

SLIDES_MIME = "application/vnd.google-apps.presentation"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
STATUS_TTL_SEC = 30
_status_cache: Dict[str, Any] = {"at": 0.0, "value": None}
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()

INSTALL_HINT = ("Install the Google Workspace CLI (`npm install -g @googleworkspace/cli`), then run "
                "`gws auth login` in a terminal and sign in with the Google account that should own the deck.")
LOGIN_HINT = ("`gws` is installed but not signed in. Run `gws auth login` in a terminal (it opens a browser), "
              "then try again. TeleDesign never signs in for you.")


def binary() -> Optional[str]:
    """`gws` on PATH (Windows: the npm .cmd shim), via shutil.which."""
    return shutil.which("gws")


def _argv(args: List[str]) -> List[str]:
    """argv for `gws <args>` without a shell: an npm shim is unwrapped to
    `node <cli.js>` so JSON arguments never pass through cmd.exe."""
    b = binary()
    if not b:
        raise FileNotFoundError("gws not found")
    try:
        from services.engine.spawn import resolve_argv
        out = resolve_argv(["gws", *args])
        if isinstance(out, list):
            return out
    except Exception:
        pass
    if sys.platform == "win32" and b.lower().endswith((".cmd", ".bat")):
        raise RuntimeError("gws is a batch shim that could not be unwrapped; install the native binary")
    return [b, *args]


def _run(args: List[str], timeout: float = 60.0) -> subprocess.CompletedProcess:
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    env = {**os.environ, "NO_COLOR": "1"}
    return subprocess.run(_argv(args), capture_output=True, timeout=timeout, creationflags=flags, env=env,
                          stdin=subprocess.DEVNULL)


def _json_tail(text: str) -> Any:
    """First JSON value in the output (gws prints a 'Using keyring…' line first)."""
    i = text.find("{")
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[i:])[0]
    except ValueError:
        return None


def status(refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not refresh and _status_cache["value"] and now - _status_cache["at"] < STATUS_TTL_SEC:
        return _status_cache["value"]
    b = binary()
    if not b:
        val = {"installed": False, "authed": False, "instructions": INSTALL_HINT}
    else:
        try:
            cp = _run(["auth", "status"], timeout=25)
            data = _json_tail((cp.stdout or b"").decode("utf-8", "replace")) or {}
        except Exception as exc:
            log.info("gslides: gws auth status failed: %s", exc)
            data = {}
        authed = bool(data.get("token_valid")) or (bool(data.get("has_refresh_token"))
                                                  and bool(data.get("encrypted_credentials_exists")
                                                           or data.get("plain_credentials_exists")))
        scopes = data.get("scopes") or []
        can_upload = any(s.endswith(("/auth/drive", "/auth/drive.file")) for s in scopes) if scopes else authed
        val = {"installed": True, "authed": authed, "user": data.get("user"), "binary": b,
               "can_upload": can_upload}
        if not authed:
            val["instructions"] = LOGIN_HINT
        elif not can_upload:
            val["instructions"] = ("Signed in, but without Drive access. Run `gws auth login` again and grant the "
                                   "Drive scope.")
    _status_cache.update(at=now, value=val)
    return val


# ── Jobs ─────────────────────────────────────────────────────────────────

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def _set(job: Dict[str, Any], **kw: Any) -> None:
    with _jobs_lock:
        job.update(kw)


def upload(path: Path, name: str) -> Dict[str, Any]:
    """Upload `path` (a .pptx) as a Google Slides presentation → {id, url, name}."""
    meta = json.dumps({"name": name[:200], "mimeType": SLIDES_MIME})
    cp = _run(["drive", "files", "create", "--json", meta, "--upload", str(path),
               "--upload-content-type", PPTX_MIME, "--params", json.dumps({"fields": "id,name,webViewLink"})],
              timeout=300)
    out = (cp.stdout or b"").decode("utf-8", "replace")
    data = _json_tail(out)
    if cp.returncode != 0 or not isinstance(data, dict) or not data.get("id"):
        err = (cp.stderr or b"").decode("utf-8", "replace").strip() or out.strip()
        raise RuntimeError("Google Drive upload failed: " + (err[-400:] or f"exit {cp.returncode}"))
    fid = str(data["id"])
    url = data.get("webViewLink") or f"https://docs.google.com/presentation/d/{fid}/edit"
    return {"id": fid, "url": url, "name": data.get("name") or name}


async def _run_job(job: Dict[str, Any], pid: str, body: Dict[str, Any]) -> None:
    from services.design import export
    try:
        _set(job, status="running", progress=0.05, message="Exporting to PowerPoint")
        opts = body.get("options") if isinstance(body.get("options"), dict) else {}
        opts = {**opts, "mode": opts.get("mode") or "editable"}
        opts.pop("save_to_project_path", None)
        ej = export.start(pid, "pptx", {"file": body.get("file"), "options": opts})
        while ej["status"] in ("queued", "running"):
            _set(job, progress=0.05 + 0.6 * float(ej.get("progress") or 0), message=ej.get("message") or "Exporting")
            await asyncio.sleep(0.3)
        if ej["status"] != "done":
            raise RuntimeError(ej.get("error") or "PowerPoint export failed")
        path = export.result_path(ej)
        if not path:
            raise RuntimeError("PowerPoint export produced no file")
        _set(job, progress=0.7, message="Uploading to Google Drive", flags=ej.get("flags") or [])
        title = (store.get_project(pid) or {}).get("title") or "TeleDesign deck"
        res = await asyncio.to_thread(upload, path, title)
        _set(job, status="done", progress=1.0, message="Sent to Google Slides", url=res["url"], file_id=res["id"],
             finished_at=time.time())
    except Exception as exc:
        log.warning("gslides: send failed: %s", exc)
        _set(job, status="failed", error=str(exc) or exc.__class__.__name__, message="Failed", finished_at=time.time())


def start(pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if not store.get_project(pid):
        raise LookupError("project not found")
    st = status(refresh=True)
    if not st.get("installed") or not st.get("authed") or not st.get("can_upload", True):
        raise PermissionError(st.get("instructions") or LOGIN_HINT)
    job = {"id": uuid.uuid4().hex, "pid": pid, "kind": "google_slides", "status": "queued", "progress": 0.0,
           "message": "Queued", "created_at": time.time(), "flags": []}
    with _jobs_lock:
        # Forget finished jobs older than a day.
        for k in [k for k, v in _jobs.items() if v.get("finished_at") and time.time() - v["finished_at"] > 86400]:
            _jobs.pop(k, None)
        _jobs[job["id"]] = job
    task = asyncio.get_running_loop().create_task(_run_job(job, pid, body))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return get_job(job["id"])


_TASKS: set = set()
