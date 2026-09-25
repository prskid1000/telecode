"""Per-run cost from a CLI's cumulative figure.

Claude Code's ``total_cost_usd`` on a resumed (or forked) conversation includes
every earlier invocation of that conversation (verified on 2.1.282:
``modelUsage`` sums across resumes while ``usage`` is per invocation). The
runner therefore keeps the last total each conversation reported
(``sessions_repo.cost_total`` / ``set_cost_total``, table
``engine_cost_totals``) and records ``total - base`` as the run's cost, so Task
submits, run steps, trigger fires, spans / telemetry summaries, the session
lineage and TeleDesign turns all see per-run cost.

The CLI restores a conversation's cost only in some cases (it keys the saved
cost on the last session run in that project directory), so a resumed run can
also report a per-invocation figure. The guard — subtract only when
``total >= base`` — keeps that case right: a total below the base can only be
the run's own cost. Codex and agy report no cost (None), untouched.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("telecode.services.engine.cost")


def per_run_cost(total: Any, base: Optional[float]) -> Any:
    """``total - base`` when the CLI's figure includes the earlier runs; the
    figure itself otherwise (no base, a non-number, or a total below it)."""
    if not isinstance(total, (int, float)) or not base:
        return total
    return round(float(total) - float(base), 6) if total >= base else total


def lookup_base(engine: str, resume_id: Optional[str], hint: Optional[float] = None) -> Optional[float]:
    """The conversation's last reported cumulative total (the shared record
    first, then the caller's ``hint``). No resume id = a fresh conversation."""
    if not resume_id:
        return None
    try:
        from services.db import sessions_repo
        base = sessions_repo.cost_total(engine, resume_id)
    except Exception:
        logger.exception("cost base lookup failed")
        base = None
    return base if base is not None else hint


def record_total(engine: str, engine_session_id: Optional[str], total: Any) -> None:
    try:
        from services.db import sessions_repo
        sessions_repo.set_cost_total(engine, engine_session_id, total)
    except Exception:
        logger.exception("cost total write failed")
