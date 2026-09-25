"""Parse HEARTBEAT.md → list of ScheduleEntry.

HEARTBEAT.md is markdown with one or more ```yaml fenced blocks. Anything outside
the fences is human notes the agent reads but the scheduler ignores. Inside each
fence we expect a YAML list whose items have at least:

  name   (str, unique within file)
  cron   (str, 5-field cron, parsed by croniter)
  prompt (str, free-form)

Optional:
  workspace      "ephemeral" (default) | "persistent"
  workspace_id   required when workspace == "persistent"
  engine         "claude_code" (default) | "codex" | "antigravity"
  model          str, optional — engine model id (cloud) / llama model (local);
                 blank = the agent's default model, else the CLI default
  is_local       bool, default False — run against the local llama.cpp proxy
  enabled        bool, default True

Per-entry errors are collected; bad entries are skipped, valid ones still parsed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml
from croniter import croniter

logger = logging.getLogger("telecode.services.heartbeat.parser")

VALID_ENGINES = ("claude_code", "codex", "antigravity")
VALID_WORKSPACE_MODES = ("ephemeral", "persistent")


@dataclass
class ScheduleEntry:
    name: str
    cron: str
    prompt: str
    workspace: str = "ephemeral"
    workspace_id: Optional[str] = None
    engine: str = "claude_code"
    enabled: bool = True
    model: Optional[str] = None
    is_local: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "cron": self.cron,
            "prompt": self.prompt,
            "workspace": self.workspace,
            "workspace_id": self.workspace_id,
            "engine": self.engine,
            "model": self.model,
            "is_local": self.is_local,
            "enabled": self.enabled,
        }


@dataclass
class ParseResult:
    entries: List[ScheduleEntry] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)  # [{block, index, msg}]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "entries": [e.to_dict() for e in self.entries],
            "errors": self.errors,
        }


_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def _extract_yaml_blocks(text: str) -> List[str]:
    return [m.group(1) for m in _FENCE_RE.finditer(text or "")]


def _coerce_entry(raw: Any, block_idx: int, item_idx: int, errors: List[Dict[str, Any]]) -> Optional[ScheduleEntry]:
    if not isinstance(raw, dict):
        errors.append({"block": block_idx, "index": item_idx, "msg": "entry must be a mapping"})
        return None

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append({"block": block_idx, "index": item_idx, "msg": "missing or empty 'name'"})
        return None
    name = name.strip()

    cron = raw.get("cron")
    if not isinstance(cron, str) or not cron.strip():
        errors.append({"block": block_idx, "index": item_idx, "name": name, "msg": "missing 'cron'"})
        return None
    cron = cron.strip()
    try:
        croniter(cron)
    except Exception as exc:
        errors.append({"block": block_idx, "index": item_idx, "name": name, "msg": f"invalid cron: {exc}"})
        return None

    prompt = raw.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        errors.append({"block": block_idx, "index": item_idx, "name": name, "msg": "missing 'prompt'"})
        return None

    workspace = (raw.get("workspace") or "ephemeral").strip()
    if workspace not in VALID_WORKSPACE_MODES:
        errors.append({"block": block_idx, "index": item_idx, "name": name,
                       "msg": f"workspace must be one of {VALID_WORKSPACE_MODES}, got '{workspace}'"})
        return None

    workspace_id = raw.get("workspace_id")
    if workspace == "persistent":
        if not workspace_id or not isinstance(workspace_id, str):
            errors.append({"block": block_idx, "index": item_idx, "name": name,
                           "msg": "workspace_id required when workspace == 'persistent'"})
            return None
    else:
        workspace_id = None  # ignore for ephemeral

    engine = str(raw.get("engine") or "claude_code").strip().lower()
    if engine not in VALID_ENGINES:
        errors.append({"block": block_idx, "index": item_idx, "name": name,
                       "msg": f"engine must be one of {VALID_ENGINES}, got '{engine}'"})
        return None

    model = raw.get("model")
    if model is not None and not isinstance(model, str):
        errors.append({"block": block_idx, "index": item_idx, "name": name,
                       "msg": "model must be a string"})
        return None
    model = (model or "").strip() or None

    is_local = raw.get("is_local", False)
    if not isinstance(is_local, bool):
        errors.append({"block": block_idx, "index": item_idx, "name": name,
                       "msg": "is_local must be true or false"})
        return None

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = bool(enabled)

    return ScheduleEntry(
        name=name, cron=cron, prompt=prompt.rstrip(),
        workspace=workspace, workspace_id=workspace_id,
        engine=engine, enabled=enabled, model=model, is_local=is_local,
    )


def parse(text: str) -> ParseResult:
    result = ParseResult()
    blocks = _extract_yaml_blocks(text or "")
    seen_names: Dict[str, int] = {}  # name -> first block index seen

    for bidx, block in enumerate(blocks):
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as exc:
            result.errors.append({"block": bidx, "msg": f"yaml parse error: {exc}"})
            continue

        if data is None:
            continue
        if not isinstance(data, list):
            result.errors.append({"block": bidx, "msg": "yaml block must be a list of entries"})
            continue

        for iidx, raw in enumerate(data):
            entry = _coerce_entry(raw, bidx, iidx, result.errors)
            if entry is None:
                continue
            if entry.name in seen_names:
                result.errors.append({"block": bidx, "index": iidx, "name": entry.name,
                                      "msg": f"duplicate name (already defined in block {seen_names[entry.name]})"})
                continue
            seen_names[entry.name] = bidx
            result.entries.append(entry)

    return result


def next_fires(entry: ScheduleEntry, after_iso: Optional[str] = None, count: int = 3) -> List[str]:
    """Return the next `count` fire times as ISO-Z strings, anchored at after_iso (or now)."""
    from datetime import datetime, timezone
    if after_iso:
        try:
            base = datetime.fromisoformat(after_iso.replace("Z", "+00:00"))
        except Exception:
            base = datetime.now(timezone.utc)
    else:
        base = datetime.now(timezone.utc)
    it = croniter(entry.cron, base)
    out = []
    for _ in range(count):
        nxt = it.get_next(datetime)
        out.append(nxt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return out
