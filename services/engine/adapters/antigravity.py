"""Antigravity: ``agy --input-format stream-json --output-format stream-json … -p=``.

The prompt goes on stdin as one ``{"event":"user","message":{"content":…}}``
line (``-p=`` with an empty value; a bare ``-p`` swallows the next flag).
Stream: ``init.conversation_id`` (replayed with ``--conversation``),
``step_update`` (tool / agent_response ``text_delta``), ``result``.

Local mode (agy >= 1.1.13): the Gemini-API route against the proxy, in an
isolated home (``<settings_dir>/data/agy-local-home`` via USERPROFILE/HOME) so
the user's real ``~/.gemini`` is never touched; model in agy's custom-model
URL form. No cost field; no structured-output flag yet.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.engine.adapters.base import Adapter, Launch, ParseState, tool_event
from services.engine.types import EngineError, EngineRequest, EngineResult

logger = logging.getLogger("telecode.services.engine.antigravity")

LOCAL_ENV_STRIP = ("GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT",
                   "GOOGLE_CLOUD_LOCATION", "GOOGLE_APPLICATION_CREDENTIALS")


def local_home() -> Path:
    import config as app_config
    return Path(app_config._settings_dir()) / "data" / "agy-local-home"


def ensure_local_home(home: Optional[Path] = None) -> Path:
    """Create the isolated agy home with ``modelProvider: gemini``; keeps any
    other keys agy wrote there."""
    home = home or local_home()
    cfg_dir = home / ".gemini" / "antigravity-cli"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "settings.json"
    data: Dict[str, Any] = {}
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(loaded, dict):
                data = loaded
    except (OSError, json.JSONDecodeError):
        data = {}
    if data.get("modelProvider") != "gemini":
        data["modelProvider"] = "gemini"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return home


def local_env(proxy_port: int, home: Path, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k in LOCAL_ENV_STRIP:
        env.pop(k, None)
    env.update({
        "USERPROFILE": str(home),
        "HOME": str(home),
        "GEMINI_API_KEY": "local",
        # No /v1 — the genai SDK appends /v1beta/models/... itself.
        "GOOGLE_GEMINI_BASE_URL": f"http://localhost:{proxy_port}",
    })
    return env


def local_model_arg(model: str) -> str:
    return f"gemini-api://local/models/{model}"


def build_argv(*, work_dir: Path, resume_id: Optional[str], model: Optional[str] = None,
               add_dirs=()) -> List[str]:
    cmd: List[str] = [
        "agy",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
        "--add-dir", str(work_dir),
    ]
    for d in add_dirs or ():
        cmd += ["--add-dir", str(d)]
    if model:
        cmd += ["--model", model]
    if resume_id:
        cmd += ["--conversation", resume_id]
    cmd.append("-p=")
    return cmd


def stdin_message(prompt: str) -> str:
    return json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"


class AntigravityAdapter(Adapter):
    engine = "antigravity"
    label = "agy"
    resume_start_key = "resumed_antigravity_conversation_id"
    persist_deltas = True
    stdin_close_wait = 60.0

    def build(self, req: EngineRequest) -> Launch:
        env: Optional[Dict[str, str]] = None
        model_arg: Optional[str] = req.model if (req.model and not req.is_local) else None
        if req.is_local:
            import config as app_config
            import llamacpp.state as llama_state
            model = req.model or llama_state.last_active_model() or "local"
            home = ensure_local_home()
            env = local_env(app_config.proxy_port(), home)
            model_arg = local_model_arg(model)
            logger.info(f"Local mode: agy home={home} base_url={env['GOOGLE_GEMINI_BASE_URL']} model={model_arg}")
        if req.env_extra:
            env = {**(env or os.environ), **req.env_extra}
        if req.schema:
            logger.info("antigravity: no structured-output flag yet — schema ignored")
        argv = build_argv(work_dir=req.cwd, resume_id=req.resume_id, model=model_arg, add_dirs=req.add_dirs)
        return Launch(argv=argv, stdin=stdin_message(req.prompt), env=env)

    def parse(self, evt: Dict[str, Any], st: ParseState) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        kind = evt.get("event")
        if kind == "init":
            st.session_id = evt.get("conversation_id") or st.session_id
        elif kind == "step_update":
            step = evt.get("step_update") or {}
            st.session_id = step.get("conversation_id") or st.session_id
            if step.get("step_type") == "tool" and step.get("state") == "ACTIVE":
                info = step.get("tool_info") or {}
                name = step.get("tool_name") or info.get("name") or "tool"
                st.tool_calls.append(name)
                params = info.get("parameters")
                summary = json.dumps(params, ensure_ascii=False)[:300] if params is not None else name
                out.append(tool_event(name, summary, params))
            elif step.get("step_type") == "agent_response" and step.get("text_delta"):
                st.text_parts.append(step["text_delta"])
                out.append({"kind": "delta", "text": step["text_delta"]})
        elif kind == "result":
            st.final = evt.get("result") or {}
            st.saw_completion = True
            st.session_id = st.final.get("conversation_id") or st.session_id
        return out

    def _tokens(self, st: ParseState) -> Dict[str, int]:
        usage = (st.final or {}).get("usage") or {}
        return {
            "input": usage.get("input_tokens") or 0,
            "output": usage.get("output_tokens") or 0,
            "cache_read": usage.get("cache_read_tokens") or 0,
            "cache_write": 0,
            "total_input_incl_cache": usage.get("input_tokens") or 0,
        }

    def usage_event(self, st: ParseState) -> Optional[Dict[str, Any]]:
        if not st.final:
            return None
        return {"kind": "usage", "tokens": self._tokens(st), "cost_usd": None}

    def finish(self, req, st, returncode, stderr, wall_ms) -> EngineResult:
        fin = st.final or {}
        if fin.get("status") not in (None, "SUCCESS") and not fin.get("response"):
            raise EngineError(f"agy failed: {fin.get('error') or stderr.strip()[:500]}")
        if returncode not in (0, None) and st.final is None and not st.text_parts:
            raise EngineError(f"agy exited with code {returncode}: {stderr.strip()[:500]}")
        text = (fin.get("response") or "".join(st.text_parts) or "\n".join(st.raw_lines)).strip()
        return EngineResult(
            engine=self.engine, text=text, engine_session_id=st.session_id,
            cost_usd=None,  # agy reports no cost: None = unknown, not free
            duration_ms=int((fin.get("duration_seconds") or 0) * 1000), duration_api_ms=0,
            num_turns=fin.get("num_turns") or 1, tokens=self._tokens(st),
            tool_calls=list(st.tool_calls), exit_code=returncode)
