"""Schedule math for triggers: cron (IANA-tz aware), interval, one-off, active hours.

All datetimes returned are timezone-aware UTC. A cron expression is evaluated
in the schedule's own ``tz`` (default UTC), so ``0 9 * * 1-5`` with
``tz: Asia/Kolkata`` means 09:00 IST on weekdays, DST-correct for zones that
have it. Windows has no system tz database: the ``tzdata`` package provides it.
"""

from __future__ import annotations

import re
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional

from croniter import croniter

MIN_INTERVAL_SECONDS = 60
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_EVERY = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.I)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def zone(name: Optional[str]):
    """ZoneInfo for an IANA name ('' / None / 'UTC' → UTC). ValueError if unknown."""
    n = (name or "UTC").strip() or "UTC"
    if n.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(n)
    except Exception:
        raise ValueError(f"unknown time zone {n!r} (use an IANA name like Europe/London)") from None


def parse_every(value: Any) -> int:
    """Seconds from an int or '90', '15m', '2h', '1d'. ValueError when invalid."""
    if isinstance(value, bool):
        raise ValueError("every must be a number of seconds or like 15m / 2h / 1d")
    if isinstance(value, (int, float)):
        n = int(value)
    else:
        m = _EVERY.match(str(value or ""))
        if not m:
            raise ValueError(f"every must be seconds or like 15m / 2h / 1d, got {value!r}")
        n = int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2).lower()]
    if n < MIN_INTERVAL_SECONDS:
        raise ValueError(f"interval must be at least {MIN_INTERVAL_SECONDS} seconds")
    return n


def normalize_schedule(s: Any) -> Optional[Dict[str, Any]]:
    """{cron, tz} | {every_seconds} | {at, tz?} | None (event-only trigger)."""
    if not s:
        return None
    if not isinstance(s, dict):
        raise ValueError("schedule must be an object {cron, tz} | {every_seconds} | {at}")
    cron = (s.get("cron") or "").strip() if isinstance(s.get("cron"), str) else ""
    every = s.get("every_seconds") if s.get("every_seconds") not in (None, "", 0) else s.get("every")
    at = s.get("at")
    given = [x for x in (cron, every, at) if x not in (None, "", 0)]
    if not given:
        return None
    if len(given) > 1:
        raise ValueError("schedule takes exactly one of cron, every_seconds, at")
    tzname = (s.get("tz") or "UTC").strip() or "UTC"
    zone(tzname)
    if cron:
        try:
            croniter(cron)
        except Exception as exc:
            raise ValueError(f"invalid cron {cron!r}: {exc}") from None
        return {"cron": cron, "tz": tzname}
    if every not in (None, "", 0):
        return {"every_seconds": parse_every(every)}
    dt = parse_iso(at) if isinstance(at, str) else None
    if dt is None:
        raise ValueError(f"schedule.at must be an ISO date-time, got {at!r}")
    if isinstance(at, str) and not re.search(r"(Z|[+-]\d\d:?\d\d)$", at.strip()):
        # A naive local time is read in the schedule's tz.
        naive = datetime.fromisoformat(at.strip())
        dt = naive.replace(tzinfo=zone(tzname)).astimezone(timezone.utc)
    return {"at": to_iso(dt), "tz": tzname}


def next_fire(schedule: Optional[Dict[str, Any]], after: datetime,
              last_fire: Optional[datetime] = None) -> Optional[datetime]:
    """The first fire time strictly after ``after`` (interval: ``last_fire +
    every``, never earlier than ``after``). None = never (event-only / a
    one-off already past)."""
    if not schedule:
        return None
    if schedule.get("cron"):
        tz = zone(schedule.get("tz"))
        it = croniter(schedule["cron"], after.astimezone(tz))
        return it.get_next(datetime).astimezone(timezone.utc)
    if schedule.get("every_seconds"):
        step = timedelta(seconds=int(schedule["every_seconds"]))
        if last_fire is None:
            return after + step
        return _roll(last_fire + step, step, after)
    if schedule.get("at"):
        at = parse_iso(schedule["at"])
        return at if at and at > after else None
    return None


def _roll(base: datetime, step: timedelta, after: datetime) -> datetime:
    """First ``base + k*step`` strictly after ``after``."""
    if base > after:
        return base
    k = int((after - base) / step) + 1
    return base + k * step


def upcoming(schedule: Optional[Dict[str, Any]], count: int = 3, after: Optional[datetime] = None,
             active_hours: Optional[Dict[str, Any]] = None) -> List[str]:
    out: List[str] = []
    t = after or utcnow()
    last: Optional[datetime] = None
    guard = 0
    while schedule and len(out) < count and guard < 2000:
        guard += 1
        nxt = next_fire(schedule, t, last_fire=last)
        if nxt is None:
            break
        if in_active_hours(active_hours, nxt):
            out.append(to_iso(nxt))
        t = last = nxt
        if schedule.get("at"):
            break
    return out


# ── active hours ────────────────────────────────────────────────────────────

def normalize_active_hours(ah: Any) -> Optional[Dict[str, Any]]:
    """{start: "HH:MM", end: "HH:MM", tz, days: [mon..sun]} or None.
    Also accepts "09:00-18:00". end < start = a window across midnight."""
    if not ah:
        return None
    if isinstance(ah, str):
        m = re.match(r"^\s*(\S+)\s*-\s*(\S+)\s*$", ah)
        if not m:
            raise ValueError("active_hours must look like 09:00-18:00")
        ah = {"start": m.group(1), "end": m.group(2)}
    if not isinstance(ah, dict):
        raise ValueError("active_hours must be an object {start, end, tz, days}")
    start, end = str(ah.get("start") or "").strip(), str(ah.get("end") or "").strip()
    if not start and not end:
        return None
    for v in (start, end):
        if not _HHMM.match(v):
            raise ValueError(f"active_hours times must be HH:MM, got {v!r}")
    if start == end:
        raise ValueError("active_hours start and end must differ")
    days_in = ah.get("days") or list(DAYS)
    if isinstance(days_in, str):
        days_in = [d.strip() for d in days_in.split(",") if d.strip()]
    days = []
    for d in days_in:
        d3 = str(d).strip().lower()[:3]
        if d3 not in DAYS:
            raise ValueError(f"active_hours.days must be from {DAYS}, got {d!r}")
        if d3 not in days:
            days.append(d3)
    if not days:
        raise ValueError("active_hours.days is empty")
    tzname = (ah.get("tz") or "UTC").strip() or "UTC"
    zone(tzname)
    return {"start": start, "end": end, "tz": tzname, "days": [d for d in DAYS if d in days]}


def _hm(v: str) -> dtime:
    h, m = v.split(":")
    return dtime(int(h), int(m))


def in_active_hours(ah: Optional[Dict[str, Any]], when: Optional[datetime] = None) -> bool:
    if not ah:
        return True
    local = (when or utcnow()).astimezone(zone(ah.get("tz")))
    start, end, t = _hm(ah["start"]), _hm(ah["end"]), local.time()
    day = DAYS[local.weekday()]
    if start < end:
        return day in ah["days"] and start <= t < end
    # across midnight: the part after midnight belongs to the previous day's window
    if t >= start:
        return day in ah["days"]
    if t < end:
        return DAYS[(local.weekday() - 1) % 7] in ah["days"]
    return False
