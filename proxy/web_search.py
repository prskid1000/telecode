"""Web search backend + tool_result rewriter for the WebSearch tool.

Claude Code's built-in WebSearch executor returns an empty placeholder when no
Anthropic-side search backend is reachable (which is always the case when CC is
proxied to LM Studio). The proxy detects that placeholder in the conversation
history on each /v1/messages request and queries a local SearXNG instance to
fill in real results before forwarding to the local model. CC's UI still shows
the WebSearch call; the model now sees real data instead of an empty stub.

SearXNG is a free metasearch engine — it relays the query to ~70 real search
engines (Google, Bing, DDG, Brave, Wikipedia, etc.) and merges the results.
No API key, runs locally, returns clean JSON via `?format=json`.

For Windows users without WSL or Docker, install the community fork
`mbaozi/SearXNGforWindows` (https://github.com/mbaozi/SearXNGforWindows) —
download a release, double-click the bundled `.bat`, and it serves
`http://localhost:8888` out of the box. Make sure `formats: [html, json]` is
enabled in its `settings.yml` so the JSON endpoint works.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import subprocess
import sys
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from typing import Any

import aiohttp

from proxy import config as proxy_config

log = logging.getLogger("telecode.proxy.web_search")


_REMINDER = (
    "REMINDER: You MUST cite the sources above in your response to the user "
    "using markdown hyperlinks."
)


def _format_results(
    query: str,
    results: list[dict[str, Any]],
    answer: str = "",
    infoboxes: list[dict[str, Any]] | None = None,
) -> str:
    """Format search results in the Anthropic web_search-style result string.

    Surfaces three sources of content from SearXNG's response:
      - `answer` (str): one-line summary, e.g. currency conversion result
      - `infoboxes` (list): structured per-entity data, e.g. Wikipedia/Wikidata
        infoboxes for queries like "Albert Einstein"
      - `results` (list): the standard ranked link list

    Some engines (currency, wikipedia/wikidata for entity queries) put their
    primary content into `answers`/`infoboxes` and return an empty `results`
    array. Surfacing all three means those engines aren't silently dropped.
    """
    lines = [f'Web search results for query: "{query}"', ""]
    if answer:
        lines.append(f"Summary: {answer}")
        lines.append("")
    for ib in infoboxes or []:
        if not isinstance(ib, dict):
            continue
        title = (ib.get("infobox") or ib.get("title") or "").strip()
        content = (ib.get("content") or "").strip()
        if not title and not content:
            continue
        lines.append(f"[infobox] {title}")
        if content:
            lines.append(content[:600])
        for url_entry in (ib.get("urls") or [])[:3]:
            if isinstance(url_entry, dict):
                u = url_entry.get("url", "")
                t = url_entry.get("title", "")
                if u:
                    lines.append(f"  - {t}: {u}" if t else f"  - {u}")
        lines.append("")
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "(no title)").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or r.get("snippet") or "").strip()
        engine = (r.get("engine") or "").strip()
        lines.append(f"[{i}] {title}")
        if url:
            lines.append(f"URL: {url}")
        if snippet:
            lines.append(f"Snippet: {snippet}")
        if engine:
            lines.append(f"Engine: {engine}")
        lines.append("")
    lines.append(_REMINDER)
    return "\n".join(lines)


def _format_error(query: str, message: str) -> str:
    return (
        f'Web search results for query: "{query}"\n\n'
        f"ERROR: {message}\n\n"
        f"Tell the user the web search failed and continue without these results."
    )


# ── Provider: SearXNG (local, no key) ──────────────────────────────────────

_SEARXNG_HEADERS = {
    # Some SearXNG instances reject requests without a normal browser UA.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


async def _search_searxng(
    query: str,
    max_results: int,
    categories: str = "",
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    base = proxy_config.web_search_url()
    params: dict[str, str] = {
        "q": query,
        "format": "json",
        "safesearch": "0",
        "language": "en",
    }
    if categories:
        params["categories"] = categories
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers=_SEARXNG_HEADERS) as session:
        async with session.get(f"{base}/search", params=params) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"SearXNG HTTP {resp.status} from {base}: {body[:200]}"
                )
            data = await resp.json(content_type=None)

    raw = data.get("results", []) or []
    # SearXNG sometimes returns the same URL from multiple engines — dedupe
    # while preserving the first (best-ranked) occurrence.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in raw:
        url = r.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(r)
        if len(deduped) >= max_results:
            break

    # SearXNG can surface content via three channels — `results`,
    # `infoboxes` (structured per-entity data), and `answers` (one-line
    # summaries from currency conversion / quick facts). Pass them all
    # so the formatter can render whichever the engine populated.
    answers = data.get("answers") or []
    answer = answers[0] if answers else ""
    if isinstance(answer, dict):
        answer = answer.get("answer", "") or ""
    infoboxes = data.get("infoboxes") or []

    return deduped, str(answer), list(infoboxes)


_PROVIDERS = {
    "searxng": _search_searxng,
}


# ── Cross-request cache ────────────────────────────────────────────────────
# CC re-sends the same conversation history every turn, so the same empty
# WebSearch result reappears N times across N turns. Caching by query keeps
# load on the local SearXNG instance flat regardless of conversation length.

_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAX = 256


def _cache_get(key: str) -> str | None:
    val = _CACHE.get(key)
    if val is not None:
        _CACHE.move_to_end(key)
    return val


def _cache_put(key: str, value: str) -> None:
    _CACHE[key] = value
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)


# ── Public search entrypoint ───────────────────────────────────────────────

async def search(
    query: str,
    categories: list[str] | None = None,
    max_results: int | None = None,
) -> tuple[str, int]:
    """Run a web search and return (formatted_result_string, result_count).

    `categories` maps to SearXNG's `&categories=` param via the
    `CATEGORY_TO_SEARXNG` dict in `proxy.tool_registry`. Multiple
    categories are joined with `,` so SearXNG fans out to all matched
    engines in one call.

    Always returns a string (never raises) — errors become a visible error
    message in the tool_result so the model can keep going.
    """
    from proxy.tool_registry import CATEGORY_TO_SEARXNG

    query = (query or "").strip()
    if not query:
        return _format_error("", "empty query"), 0
    n = int(max_results if max_results is not None else proxy_config.web_search_max_results())
    cats = categories or ["general"]
    searxng_cats = ",".join(
        CATEGORY_TO_SEARXNG.get(c, c) for c in cats
    )

    cache_key = f"{n}:{searxng_cats}:{query}"
    cached = _cache_get(cache_key)
    if cached is not None:
        count = cached.count("\nURL: ")
        return cached, count

    provider_name = proxy_config.web_search_provider()
    fn = _PROVIDERS.get(provider_name)
    if fn is None:
        return _format_error(query, f"unknown provider {provider_name!r}"), 0

    try:
        results, answer, infoboxes = await fn(query, n, categories=searxng_cats)
    except Exception as exc:
        log.warning("WebSearch %s failed: %s", provider_name, exc)
        return _format_error(query, str(exc)), 0

    if not results and not answer and not infoboxes:
        return _format_error(query, "no results"), 0

    out = _format_results(query, results, answer, infoboxes)
    _cache_put(cache_key, out)
    return out, len(results)


# ── Auto-setup: clone, venv, generate config, launch as managed child ─────
# Triggered from `proxy.server.start_proxy_background()` whenever the
# searxng provider is enabled. Native Python install — no .exe, no Docker:
#
#   data/searxng/
#     repo/      ← `git clone --depth 1 mbaozi/SearXNGforWindows`
#     .venv/     ← `python -m venv` + `pip install -r repo/config/requirements.txt`
#     settings.yml  ← generated from `repo/config/settings.yml` overlaid with
#                     values from telecode's settings.json (engines, port,
#                     bind address, secret key, language, safesearch). Kept
#                     outside `repo/` so `git pull` doesn't clobber it.
#
# Run command: `.venv/Scripts/python.exe -m searx.webapp` with
#   PYTHONPATH=repo/python/Lib/site-packages   (so `import searx` resolves)
#   SEARXNG_SETTINGS_PATH=data/searxng/settings.yml
#   cwd=data/searxng/repo
#
# webapp.py:run() reads server.bind_address/server.port from settings.yml and
# calls Flask's app.run(host, port). Source-of-truth for those is telecode's
# `proxy.web_search.url` + `proxy.web_search.searxng.*` blocks.

_REPO_URL = "https://github.com/mbaozi/SearXNGforWindows.git"

# Module-level handle to the managed SearXNG child process. Tracked so
# `stop_searxng()` can terminate it cleanly on Telecode shutdown — same
# pattern as the proxy's AppRunner and the MCP server's daemon thread.
_searxng_proc: subprocess.Popen | None = None

# Module-level Windows Job Object handle. The child SearXNG process is
# bound to this job with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` so it dies
# automatically when Telecode dies — *regardless* of how Telecode dies.
# `Stop-ScheduledTask`, `taskkill /F`, BSOD, OOM all leave the job handle
# closed by the kernel, which kills every member. The handle MUST stay
# alive in module scope (closing it kills the job's members), so we hold
# it here for the lifetime of the Telecode process.
_searxng_job_handle: int | None = None


def _data_dir() -> Path:
    """Root for SearXNG state. Sibling of telecode's other data dirs."""
    return Path(__file__).resolve().parent.parent.parent / "data" / "searxng"


def _repo_dir() -> Path:
    return _data_dir() / "repo"


def _venv_dir() -> Path:
    return _data_dir() / ".venv"


def _venv_python() -> Path:
    if sys.platform == "win32":
        return _venv_dir() / "Scripts" / "python.exe"
    return _venv_dir() / "bin" / "python"


def _settings_yml_path() -> Path:
    """Settings file location. Lives at `<cwd>/config/settings.yml` so that
    `searx.settings_loader` finds it via its `os.path.join(os.getcwd(),
    "config", SETTINGS_YAML)` default — that loader ALWAYS reads the
    cwd-based path as the base and only merges `SEARXNG_SETTINGS_PATH` on
    top. Putting our generated file at the cwd-relative spot lets us bypass
    the merge and use it as the single source of truth."""
    return _data_dir() / "config" / "settings.yml"


def _venv_site_packages() -> Path:
    """Where the venv installs packages. Used as the destination for the
    patched-searx copy below — we drop searx alongside our pip-installed
    deps so it imports cleanly without any PYTHONPATH gymnastics."""
    if sys.platform == "win32":
        return _venv_dir() / "Lib" / "site-packages"
    # CPython on Linux/Mac uses lib/pythonX.Y/site-packages — glob it.
    candidates = list((_venv_dir() / "lib").glob("python*/site-packages"))
    if candidates:
        return candidates[0]
    return _venv_dir() / "lib" / "python3" / "site-packages"


def _upstream_searx_dir() -> Path:
    """Patched searx source inside the cloned upstream repo. The repo's
    `python/Lib/site-packages/` tree is the *embedded* Python 3.11.9 install
    that ships with the fork — we ignore everything in there EXCEPT the
    patched searx package itself, since the rest are deps already covered
    by our venv's `pip install -r requirements.txt`."""
    return _repo_dir() / "python" / "Lib" / "site-packages" / "searx"


def _parse_url_port(url: str) -> tuple[str, int]:
    """Extract (host, port) from telecode's `proxy.web_search.url`."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8888
    return host, port


async def _ping_searxng(timeout_s: float = 3.0) -> bool:
    """Cheap reachability check — any HTTP response under 500 means it's up."""
    base = proxy_config.web_search_url()
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base}/", allow_redirects=False) as resp:
                return resp.status < 500
    except Exception:
        return False


def _silent_subprocess_kwargs() -> dict[str, Any]:
    """Subprocess flags that suppress any console window on Windows.

    When Telecode is launched via `pythonw.exe` (no console), child
    processes like git/pip otherwise pop their own console window. This
    combines `CREATE_NO_WINDOW` with `STARTUPINFO.SW_HIDE` for full
    coverage across console + GUI subprocess types. No-op on Linux/Mac.
    """
    if sys.platform != "win32":
        return {}
    CREATE_NO_WINDOW = 0x08000000
    STARTF_USESHOWWINDOW = 0x00000001
    SW_HIDE = 0
    si = subprocess.STARTUPINFO()
    si.dwFlags |= STARTF_USESHOWWINDOW
    si.wShowWindow = SW_HIDE
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": si}


def _run_blocking(cmd: list[str], cwd: Path | None = None) -> None:
    """Run a setup command synchronously and stream output to logs.

    Silent on Windows — no console window pops for git/pip/venv subprocesses.
    """
    log.info("SearXNG setup: %s (cwd=%s)", " ".join(cmd), cwd or "")
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        **_silent_subprocess_kwargs(),
    )
    if result.stdout.strip():
        log.info("SearXNG setup stdout: %s", result.stdout.strip()[:2000])
    if result.stderr.strip():
        log.info("SearXNG setup stderr: %s", result.stderr.strip()[:2000])
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed (exit {result.returncode}): {' '.join(cmd)}"
        )


async def _clone_repo() -> None:
    """git clone the upstream fork into data/searxng/repo (shallow)."""
    if _repo_dir().exists():
        return
    if shutil.which("git") is None:
        raise RuntimeError(
            "git not found on PATH — install git for Windows from "
            "https://git-scm.com/download/win"
        )
    _data_dir().mkdir(parents=True, exist_ok=True)
    log.info("SearXNG: cloning %s into %s", _REPO_URL, _repo_dir())
    await asyncio.to_thread(
        _run_blocking,
        ["git", "clone", "--depth", "1", _REPO_URL, str(_repo_dir())],
    )


async def _create_venv_and_install() -> None:
    """Create the .venv and `pip install -r config/requirements.txt`."""
    if _venv_python().exists():
        return
    log.info("SearXNG: creating venv at %s", _venv_dir())
    await asyncio.to_thread(
        _run_blocking, [sys.executable, "-m", "venv", str(_venv_dir())]
    )
    requirements = _repo_dir() / "config" / "requirements.txt"
    if not requirements.exists():
        raise RuntimeError(f"requirements file not found at {requirements}")
    log.info("SearXNG: installing requirements (this takes 1-2 minutes)…")
    await asyncio.to_thread(
        _run_blocking,
        [
            str(_venv_python()),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "-r",
            str(requirements),
        ],
    )


def _install_searx_into_venv() -> None:
    """Copy the patched searx source from the upstream fork into the venv's
    site-packages, so `import searx` resolves there without any PYTHONPATH
    overlay. Putting the embedded fork's whole site-packages tree on
    PYTHONPATH instead would shadow venv-installed compiled deps (msgspec,
    lxml, brotli...) with binaries built for the fork's bundled Python 3.11
    — they'd ImportError on any other Python version. Re-runs each startup
    so source patches from `git pull` propagate."""
    src = _upstream_searx_dir()
    if not src.exists():
        raise RuntimeError(f"upstream searx source not found at {src}")
    dst = _venv_site_packages() / "searx"
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    log.info("SearXNG: copied patched searx -> %s", dst)


def _generate_settings_yml() -> None:
    """Render `data/searxng/config/settings.yml` from the upstream template,
    then overlay values from telecode's `proxy.web_search.searxng.*` block.

    Also copies sibling config files (limiter.toml etc.) from `repo/config/`
    so searx finds them via cwd-relative lookups. Only the settings.yml keys
    we expose are touched — everything else stays at the upstream default.
    Re-run on every startup so changes to telecode's settings.json take
    effect on next launch.
    """
    import yaml  # PyYAML — declared in requirements.txt

    template = _repo_dir() / "config" / "settings.yml"
    with template.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Mirror sibling files (limiter.toml etc.) into data/searxng/config/
    # so cwd-relative lookups inside searx find them. Skip settings.yml —
    # we write the customized version below.
    out_config_dir = _settings_yml_path().parent
    out_config_dir.mkdir(parents=True, exist_ok=True)
    for src in template.parent.iterdir():
        if src.name == "settings.yml":
            continue
        dst = out_config_dir / src.name
        if not dst.exists() and src.is_file():
            shutil.copy2(src, dst)

    host, port = _parse_url_port(proxy_config.web_search_url())

    # server.* — bind address, port, secret key
    server = data.setdefault("server", {})
    server["bind_address"] = host
    server["port"] = port
    if not server.get("secret_key") or server.get("secret_key") == "ultrasecretkey":
        # Required by webapp.init() — exits with error if left at the default.
        # Persist a stable random key inside the rendered file so the model's
        # cookies don't churn between restarts.
        server["secret_key"] = secrets.token_hex(32)
    server.setdefault("limiter", False)
    server.setdefault("public_instance", False)

    # search.* — language and safesearch
    search = data.setdefault("search", {})
    search["safe_search"] = proxy_config.web_search_searxng_safesearch()
    search["default_lang"] = proxy_config.web_search_searxng_language()
    formats = search.setdefault("formats", ["html"])
    if "json" not in formats:
        formats.append("json")  # Required for our /search?format=json calls.

    # engines — enable only the ones telecode wants. The upstream fork ships
    # with most engines disabled (Sogou + Baidu only); we re-enable a sane
    # English-language set by name.
    wanted = {name.lower() for name in proxy_config.web_search_searxng_engines()}
    if wanted:
        for engine in data.get("engines", []) or []:
            if not isinstance(engine, dict):
                continue
            engine["disabled"] = engine.get("name", "").lower() not in wanted

    out = _settings_yml_path()
    with out.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    log.info("SearXNG: wrote settings.yml to %s (engines=%s)", out, sorted(wanted))


def _searxng_log_path() -> Path:
    """Where SearXNG's stdout+stderr are captured. Truncated on each spawn
    so the file always reflects the current run."""
    return _data_dir() / "searxng.log"


def _ensure_kill_on_close_job() -> int | None:
    """Create (once) a Windows Job Object configured to kill all member
    processes when its handle closes, and return the handle.

    Children are added later via `_assign_to_job(handle, pid)`. The handle
    is held in `_searxng_job_handle` for the lifetime of Telecode — when
    Python exits (any reason), the handle goes out of scope, the OS closes
    it, and Windows sends a kill signal to every process in the job.

    Returns None on non-Windows or if the ctypes calls fail (in which case
    the caller falls back to the pid-file orphan-recovery path).
    """
    global _searxng_job_handle
    if _searxng_job_handle is not None:
        return _searxng_job_handle
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # CreateJobObjectW(lpJobAttributes, lpName) -> HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE

        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL

        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION layout (we only need the flags
        # field; everything else can be zero).
        ULONG_PTR = ctypes.c_size_t

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ULONG_PTR),
                ("MaximumWorkingSetSize", ULONG_PTR),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ULONG_PTR),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ULONG_PTR),
                ("JobMemoryLimit", ULONG_PTR),
                ("PeakProcessMemoryUsed", ULONG_PTR),
                ("PeakJobMemoryUsed", ULONG_PTR),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation = 9

        h = kernel32.CreateJobObjectW(None, None)
        if not h:
            raise OSError(f"CreateJobObjectW failed: {ctypes.get_last_error()}")

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            h,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            raise OSError(f"SetInformationJobObject failed: {ctypes.get_last_error()}")

        _searxng_job_handle = int(h)
        log.info("SearXNG: created kill-on-close job object (handle=%d)", _searxng_job_handle)
        return _searxng_job_handle
    except Exception as exc:
        log.warning("SearXNG: failed to create job object (orphan recovery will be the only safety net): %s", exc)
        return None


def _assign_to_job(job_handle: int, pid: int) -> bool:
    """Add a running process to a Windows Job Object."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_TERMINATE = 0x0001
        PROCESS_SET_QUOTA = 0x0100

        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE

        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        h_proc = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, pid)
        if not h_proc:
            log.warning("SearXNG: OpenProcess(%d) failed: %s", pid, ctypes.get_last_error())
            return False
        try:
            ok = kernel32.AssignProcessToJobObject(job_handle, h_proc)
            if not ok:
                log.warning("SearXNG: AssignProcessToJobObject failed: %s", ctypes.get_last_error())
                return False
            log.info("SearXNG: bound pid=%d to kill-on-close job", pid)
            return True
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception as exc:
        log.warning("SearXNG: assign-to-job failed: %s", exc)
        return False


def _pid_file() -> Path:
    """PID of the currently-managed SearXNG child. Lets the next Telecode
    boot kill any orphan from a previous run that survived a hard kill
    (e.g. Stop-ScheduledTask, which doesn't trigger Python's atexit hooks
    and so doesn't run _post_shutdown -> stop_searxng())."""
    return _data_dir() / "searxng.pid"


def _kill_pid(pid: int, label: str) -> None:
    """Force-kill a process by PID. No-op on failure (treated as already-dead)."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                **_silent_subprocess_kwargs(),
            )
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
        log.info("SearXNG: killed %s pid=%d", label, pid)
    except Exception as exc:
        log.info("SearXNG: %s kill failed (likely already dead): %s", label, exc)


def _pids_bound_to_port(port: int) -> list[int]:
    """Return PIDs of any processes listening on the given TCP port (Windows).

    Used as a fallback when the pid file is missing but a previous run left
    something serving on our port — e.g. first boot after this code change,
    or pid file got deleted manually.
    """
    if sys.platform != "win32":
        return []
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"Get-NetTCPConnection -LocalPort {port} -State Listen "
                f"-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess",
            ],
            capture_output=True, text=True,
            **_silent_subprocess_kwargs(),
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _kill_orphan() -> None:
    """Kill any SearXNG left over from a previous Telecode run.

    Two strategies:
      1. Read the pid file written by `_spawn_managed` — handles the
         common case where Telecode was hard-killed (Stop-ScheduledTask,
         taskkill, BSOD) and `_post_shutdown` didn't run.
      2. Fallback: query Windows for whichever process is listening on
         `web_search.url`'s port and kill it. Catches the case where the
         pid file is missing (first boot of this code, manual delete) but
         a stale process is still bound to our port.
    """
    pf = _pid_file()
    if pf.exists():
        try:
            pid = int(pf.read_text().strip())
            log.info("SearXNG: found pid file from previous run (pid=%d)", pid)
            _kill_pid(pid, "orphan from pid file")
        except Exception:
            pass
        pf.unlink(missing_ok=True)

    # Fallback: nothing should be on our port now. If something is, it's an
    # orphan we don't have a record of — kill it.
    _, port = _parse_url_port(proxy_config.web_search_url())
    for pid in _pids_bound_to_port(port):
        log.info("SearXNG: found orphan listening on port %d (pid=%d)", port, pid)
        _kill_pid(pid, f"port-{port} squatter")


def _spawn_managed() -> subprocess.Popen:
    """Start `python -m searx.webapp` from the venv as a managed child.

    Uses `CREATE_NO_WINDOW` to suppress the console window but does NOT
    detach — the process becomes a child of Telecode so we can terminate it
    in `stop_searxng()` during shutdown. `CREATE_NEW_PROCESS_GROUP` lets us
    send Ctrl-Break later if a graceful terminate isn't enough.

    Stdout and stderr are captured to `data/searxng/searxng.log` (truncated
    each spawn). Read that file when SearXNG fails to start.
    """
    env = os.environ.copy()
    env["SEARXNG_SETTINGS_PATH"] = str(_settings_yml_path())
    env["PYTHONUNBUFFERED"] = "1"
    # No PYTHONPATH override — `searx` lives in the venv's own site-packages
    # (copied there by `_install_searx_into_venv`), so the venv resolves it
    # alongside its pip-installed deps with matching Python ABI.

    cmd = [str(_venv_python()), "-m", "searx.webapp"]
    # cwd MUST be an ancestor of `searx/data/__init__.py` because the fork
    # patched that file to compute `Path(__file__).parent.relative_to(Path.cwd())`
    # — runs from `data/searxng/` so both `.venv/Lib/site-packages/searx/data`
    # and `repo/...` are valid subpaths. SEARXNG_SETTINGS_PATH still drives
    # settings loading, so cwd doesn't have to be the repo root.
    cwd = _data_dir()

    # Hide the console window (CREATE_NO_WINDOW + STARTUPINFO.SW_HIDE).
    # NOTE: deliberately *not* using CREATE_NEW_PROCESS_GROUP — the child
    # needs to stay in Telecode's group so the Job Object below can kill
    # it on parent death.
    silent = _silent_subprocess_kwargs()

    log_path = _searxng_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Open in write mode (truncate) so the file always reflects the current run.
    log_fh = open(log_path, "w", encoding="utf-8", errors="replace", buffering=1)

    log.info("SearXNG: spawning %s (cwd=%s, log=%s)", " ".join(cmd), cwd, log_path)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        shell=False,
        **silent,
    )

    # Bind the new process to a kill-on-close Windows Job Object so it
    # dies whenever Telecode dies — graceful or not. The pid file below is
    # a fallback for the very rare case where job binding fails.
    job = _ensure_kill_on_close_job()
    if job is not None:
        _assign_to_job(job, proc.pid)

    try:
        _pid_file().write_text(str(proc.pid), encoding="utf-8")
    except Exception as exc:
        log.warning("SearXNG: failed to write pid file: %s", exc)
    return proc


def stop_searxng() -> None:
    """Terminate the managed SearXNG child if we started one.

    Called from main.py:_post_shutdown alongside proxy runner cleanup.
    Tries `terminate()` first, then `kill()` after a short grace period.
    Safe to call when nothing is running. Also clears the pid file so
    the next boot doesn't try to kill an already-dead pid.
    """
    global _searxng_proc
    proc = _searxng_proc
    _pid_file().unlink(missing_ok=True)
    if proc is None:
        return
    if proc.poll() is not None:
        _searxng_proc = None
        return
    log.info("Stopping managed SearXNG (pid=%s)", proc.pid)
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("SearXNG didn't exit on terminate, killing")
            proc.kill()
            proc.wait(timeout=5)
    except Exception as exc:
        log.warning("Failed to stop SearXNG cleanly: %s", exc)
    finally:
        _searxng_proc = None


async def ensure_searxng_running() -> None:
    """If web_search is enabled with the searxng provider, ensure a local
    SearXNG is reachable — cloning, installing, and launching as needed.
    Opting into the searxng provider opts you into setup.

    Idempotent and safe to call repeatedly. Logs everything; never raises
    (failures degrade to "search returns ERROR strings"; the proxy still runs).
    """
    if not proxy_config.web_search_enabled():
        return
    if proxy_config.web_search_provider() != "searxng":
        return

    base = proxy_config.web_search_url()

    # Always start fresh — kill any orphan from a previous Telecode boot
    # (Stop-ScheduledTask + CREATE_NEW_PROCESS_GROUP = orphan that survives
    # parent termination), then regenerate settings.yml so changes to
    # `proxy.web_search.searxng.*` take effect on every restart.
    _kill_orphan()

    try:
        await _clone_repo()
        await _create_venv_and_install()
        _install_searx_into_venv()
        _generate_settings_yml()
    except Exception as exc:
        log.warning("SearXNG auto-setup failed during prepare: %s", exc)
        return

    global _searxng_proc
    try:
        _searxng_proc = _spawn_managed()
    except Exception as exc:
        log.warning("SearXNG auto-setup: failed to spawn webapp: %s", exc)
        return

    # Poll for readiness — first run does locale/plugin/network init which
    # can take 10-20s on cold start.
    for i in range(60):
        await asyncio.sleep(1)
        if _searxng_proc.poll() is not None:
            tail = ""
            try:
                tail = _searxng_log_path().read_text(encoding="utf-8", errors="replace")[-2000:]
            except Exception:
                pass
            log.warning(
                "SearXNG webapp exited prematurely (rc=%s); last 2000 chars of %s:\n%s",
                _searxng_proc.returncode, _searxng_log_path(), tail,
            )
            return
        if await _ping_searxng():
            log.info("SearXNG up at %s after %ds", base, i + 1)
            return
    log.warning(
        "SearXNG launched but not reachable at %s after 60s; check %s",
        base, _data_dir(),
    )
