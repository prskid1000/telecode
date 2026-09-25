"""Triggers — the one scheduler (P3). Replaces Routines and the Heartbeat loop.

A trigger starts work on a target (a Task-mode prompt, an agent prompt, or a
Team job run) from a schedule (cron with an IANA tz / interval / one-off at), an
event (bearer-token webhook, GitHub webhook, file watch) or by hand, with shared
guarantees: skip-if-running under a per-trigger lock, catch-up policy, active
hours, skip-if-empty, OK-reply suppression, session shared|fresh, model
override, goal / stop conditions, auto-pause after K consecutive failures,
pinned constraints at the tail, untrusted payload wrapping, and Claude's
``--permission-mode`` (default ``auto``) instead of skipping permissions.

  model      record validation            store      triggers + trigger_fires tables
  schedule   cron / interval / at / hours fire       fire + completion reconcile
  scheduler  the daemon thread (proxy)    heartbeat  HEARTBEAT.md → triggers
  webhook    token / GitHub HMAC + filters  migrate  routines + heartbeat → triggers (once)
"""
