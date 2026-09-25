"""Per-turn launch options that keep Claude Code design turns cheap.

A design turn is a Claude Code conversation whose every API call carries the
CLI's own fixed context before a word of ours: tool schemas, the user's
CLAUDE.md chain, skills listing, MCP servers and claude.ai connectors. Measured
on a real machine (2.1.282, first call of a trivial turn): ~99k tokens, of which
TeleDesign's brief was ~17k. The rest came from the user's environment:

- a design project lives in ``data/design/projects/<id>`` *inside* telecode's
  checkout, so the CLI walked up and loaded telecode's 93 KB developer CLAUDE.md
  plus the user's global CLAUDE.md and auto-memory (~95k chars);
- 33 built-in tool schemas (~85k chars: Workflow, Monitor, Cron*, worktrees…);
- a 155-skill listing (~30k chars);
- every user-scope MCP server and claude.ai connector (hundreds of tools).

Each lever below is a setting (read on every call — hot-reloadable):

| setting | default | effect |
|---|---|---|
| ``design.claude.strict_mcp`` | true | ``--strict-mcp-config`` + ``--mcp-config`` naming only telecode's server |
| ``design.claude.tools`` | ``Read,Write,Edit,Glob,Grep,Bash`` | ``--tools``; ``"default"`` = the CLI's full set |
| ``design.claude.disallowed_tools`` | telecode MCP tools a design turn never needs | ``--disallowedTools`` (removes them from the request) |
| ``design.claude.setting_sources`` | ``user`` | ``--setting-sources``; skips project/local settings **and** the project CLAUDE.md chain; ``"all"`` = CLI default |
| ``design.claude.claude_md`` | false | false → ``CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`` (the user's global CLAUDE.md too) |
| ``design.claude.auto_memory`` | false | false → ``CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`` |
| ``design.claude.skills`` | false | false → ``--disable-slash-commands`` (no skills listing) |
| ``design.brief_mode`` | ``system`` | ``system``: the brief rides in ``--append-system-prompt-file`` (Claude only); ``message``: in the first user message |
| ``design.brief_select`` | true | edit turns (comments, mentions, auto-fix, short change requests) carry a lean brief |
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger("telecode.services.design.engine_opts")

DEFAULT_TOOLS = "Read,Write,Edit,Glob,Grep,Bash"

# telecode MCP tools that are for *other* clients driving TeleDesign from outside
# (create/list projects, send a chat message, file CRUD by project id — a design
# turn already runs in the project folder with native Read/Write) or unrelated to
# design. Removing them trims ~9k chars of schema from every API call.
DEFAULT_DISALLOWED_MCP = (
    "approve_tool", "code_execution", "speak", "transcribe", "web_search",
    "design_create_project", "design_list_projects", "design_get_project", "design_send_message",
    "design_spawn_agents", "design_extract_system", "design_get_app_state", "design_list_systems",
    "design_list_files", "design_read_file", "design_write_file", "design_delete_file",
    "design_copy_file", "design_move_file",
)

BRIEF_MODES = ("system", "message")


def _get(key: str, default: Any) -> Any:
    return config.get_nested(key, default)


def strict_mcp() -> bool:
    return bool(_get("design.claude.strict_mcp", True))


def tools() -> Optional[str]:
    """Comma list for ``--tools``; None = don't pass the flag (CLI default set)."""
    v = _get("design.claude.tools", DEFAULT_TOOLS)
    if isinstance(v, (list, tuple)):
        v = ",".join(str(x).strip() for x in v if str(x).strip())
    v = str(v if v is not None else "").strip()
    if not v or v.lower() == "default":
        return None
    return v


def disallowed_tools() -> List[str]:
    """Full tool names for ``--disallowedTools``. Bare telecode MCP names are
    qualified with the configured server name."""
    raw = _get("design.claude.disallowed_tools", None)
    if raw is None:
        raw = list(DEFAULT_DISALLOWED_MCP)
    if isinstance(raw, str):
        raw = [x for x in raw.split(",")]
    from services.design.mcp_registration import server_name
    name = server_name()
    out = []
    for t in raw or []:
        t = str(t).strip()
        if not t:
            continue
        # Built-in tools are capitalised (Read, WebFetch); telecode's are snake_case.
        out.append(t if t.startswith("mcp__") or t[:1].isupper() else f"mcp__{name}__{t}")
    return out


def setting_sources() -> Optional[str]:
    v = _get("design.claude.setting_sources", "user")
    if isinstance(v, (list, tuple)):
        v = ",".join(str(x) for x in v)
    v = str(v if v is not None else "").strip()
    if v.lower() in ("all", "default"):
        return None
    return v  # "" = no settings files at all (valid)


def claude_md() -> bool:
    return bool(_get("design.claude.claude_md", False))


def auto_memory() -> bool:
    return bool(_get("design.claude.auto_memory", False))


def skills() -> bool:
    return bool(_get("design.claude.skills", False))


def brief_mode(engine: str) -> str:
    """``system`` needs ``--append-system-prompt-file``, which only Claude Code has."""
    v = str(_get("design.brief_mode", "system") or "system").lower()
    if v not in BRIEF_MODES:
        v = "system"
    return v if engine == "claude_code" else "message"


def brief_select() -> bool:
    return bool(_get("design.brief_select", True))


def mcp_config() -> Dict[str, Any]:
    """The only MCP server a design turn sees: telecode's own (when it runs)."""
    if not _get("mcp_server.enabled", False):
        return {"mcpServers": {}}
    from services.design.mcp_registration import server_name, server_url
    return {"mcpServers": {server_name(): {"type": "http", "url": server_url()}}}


def _mcp_config_path() -> Path:
    d = Path(config._settings_dir()) / "data" / "runtime" / "design"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "claude-mcp.json"
    text = json.dumps(mcp_config(), indent=1)
    try:
        if not p.is_file() or p.read_text(encoding="utf-8") != text:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(p)
    except OSError as exc:
        logger.warning("design: could not write %s: %s", p, exc)
    return p


def claude_launch() -> Tuple[List[str], Dict[str, str]]:
    """(extra argv, extra env) for a Claude Code design turn."""
    args: List[str] = []
    env: Dict[str, str] = {}
    if strict_mcp():
        args += ["--strict-mcp-config", "--mcp-config", str(_mcp_config_path())]
    t = tools()
    if t is not None:
        args += ["--tools", t]
    dis = disallowed_tools()
    if dis:
        args += ["--disallowedTools", ",".join(dis)]
    ss = setting_sources()
    if ss is not None:
        args += ["--setting-sources", ss]
    if not skills():
        args.append("--disable-slash-commands")
    if not claude_md():
        env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
    if not auto_memory():
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return args, env
