"""SSE listener for roots_changed events — persists group changes to settings.json.

When the docgraph UI modifies groups (add, remove, update watch flags),
the host broadcasts a roots_changed event via SSE. This module subscribes
to that event and atomically updates settings.json with the new group config."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import aiohttp

log = logging.getLogger("telecode.docgraph.groups_sync")


async def listen_for_roots_changed(host: str, port: int) -> None:
    """Subscribe to /api/events and persist roots_changed events to settings.json.

    Runs indefinitely, reconnecting on network errors. Call this in a background task.
    """
    base = f"http://{host}:{port}"
    while True:
        try:
            timeout = aiohttp.ClientTimeout(total=None)  # no timeout, keep connection open
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{base}/api/events") as resp:
                    if resp.status != 200:
                        log.warning(
                            "roots_changed listener: GET /api/events returned HTTP %s, "
                            "will retry in 5s", resp.status
                        )
                        await asyncio.sleep(5.0)
                        continue
                    log.info("roots_changed listener: connected to %s/api/events", base)
                    await _tee_events(resp)
        except asyncio.CancelledError:
            log.info("roots_changed listener: cancelled")
            raise
        except aiohttp.ClientError as exc:
            log.warning("roots_changed listener: %s, will retry in 5s", exc)
            await asyncio.sleep(5.0)
        except Exception as exc:
            log.error("roots_changed listener: unexpected error: %s, will retry in 5s", exc)
            await asyncio.sleep(5.0)


async def _tee_events(resp) -> None:
    """Process SSE stream line-by-line, looking for roots_changed events."""
    current_event = "message"
    try:
        async for raw in resp.content:
            try:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            except Exception:
                continue
            if not line:
                continue
            if line.startswith("event:"):
                current_event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            if current_event != "roots_changed":
                continue
            payload_text = line[5:].strip()
            try:
                payload = json.loads(payload_text)
            except Exception as exc:
                log.warning("roots_changed listener: malformed JSON: %s", exc)
                continue
            try:
                await _handle_roots_changed(payload)
            except Exception as exc:
                log.error("roots_changed listener: failed to persist: %s", exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("roots_changed listener: stream error: %s", exc)


async def _handle_roots_changed(payload: dict) -> None:
    """Extract groups from roots_changed event and persist to settings.json."""
    log.debug("roots_changed event: %s", payload)
    # The host sends groups in the payload; map slug -> name for settings.json format
    groups_raw = payload.get("groups")
    if groups_raw is None:
        log.debug("no groups in roots_changed event")
        return

    # Transform from host format (slug, db_path, paths) to settings.json format (name, db_path, paths)
    groups = []
    for g in groups_raw:
        groups.append({
            "name": g.get("slug", ""),
            "db_path": g.get("db_path", ""),
            "paths": g.get("paths", []),
        })

    # Atomically update settings.json with the new groups.
    await _persist_groups(groups)
    log.info("persisted %d group(s) to settings.json", len(groups))


async def _persist_groups(groups: list[dict]) -> None:
    """Atomically update docgraph.groups in settings.json."""
    # Read current settings
    settings_path = _get_settings_path()
    settings = await asyncio.to_thread(_read_json_file, settings_path)

    # Update groups
    if "docgraph" not in settings:
        settings["docgraph"] = {}
    settings["docgraph"]["groups"] = groups

    # Write atomically: write to temp file, then rename
    await asyncio.to_thread(_write_json_atomic, settings_path, settings)


def _get_settings_path() -> Path:
    """Return the path to settings.json."""
    return Path(os.environ.get("TELECODE_SETTINGS", "settings.json")).resolve()


def _read_json_file(path: Path) -> dict:
    """Read JSON file; return {} if not found."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("could not read %s: %s, using empty dict", path, exc)
        return {}


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON to path atomically using a temp file + rename."""
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in the same directory (for atomic rename)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
            suffix=".tmp"
        ) as tmp:
            tmp_path = tmp.name
            json.dump(data, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())  # ensure written to disk

        # Rename temp file to target. os.replace is atomic on both POSIX and Windows
        # (on Windows 3.8+, it replaces even if target exists).
        os.replace(tmp_path, path)
    except Exception as exc:
        # Clean up temp file if rename failed
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise exc
