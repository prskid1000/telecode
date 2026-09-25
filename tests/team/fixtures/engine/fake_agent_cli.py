"""Fake agent CLI for the P2 tests: acts like one real step.

Reads the prompt from stdin, optionally writes files into its cwd, then prints
a Claude-style (default) or Antigravity-style stream ending in a result. The
Claude result carries ``structured_output`` (the handoff) when --handoff is
given; --agy writes the handoff to .telecode/handoff.json instead, as agy is
told to. Options:

  --write REL=TEXT      write TEXT to cwd/REL (repeatable)
  --handoff JSON        structured handoff (Claude: result.structured_output;
                        --agy: .telecode/handoff.json)
  --text TEXT           the final reply text
  --session ID          session id to report (default: fresh uuid)
  --agy                 emit agy stream-json
  --argv-out FILE       append this process's argv (one JSON line per run)
  --prompt-out FILE     append the prompt (one JSON line per run)
  --fail-times N --counter FILE
                        the first N runs (counted in FILE) fail with an
                        "overloaded_error" result and exit 1
  --tokens N            report N output tokens in a message_delta, then
                        sleep --hang-sec (for the token-cap test)
  --hang-sec S          sleep S seconds before the result
  --budget-hit          end with subtype error_max_budget_usd
"""

import argparse
import json
import os
import sys
import time
import uuid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="append", default=[])
    ap.add_argument("--handoff")
    ap.add_argument("--text", default="step finished")
    ap.add_argument("--session")
    ap.add_argument("--agy", action="store_true")
    ap.add_argument("--argv-out")
    ap.add_argument("--prompt-out")
    ap.add_argument("--fail-times", type=int, default=0)
    ap.add_argument("--counter")
    ap.add_argument("--tokens", type=int, default=0)
    ap.add_argument("--hang-sec", type=float, default=0.0)
    ap.add_argument("--budget-hit", action="store_true")
    a, rest = ap.parse_known_args()

    prompt = sys.stdin.buffer.read().decode("utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    if a.argv_out:
        with open(a.argv_out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rest) + "\n")
    if a.prompt_out:
        with open(a.prompt_out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(prompt) + "\n")
    sid = a.session or str(uuid.uuid4())

    def out(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    if a.agy:
        out({"event": "init", "conversation_id": sid})
    else:
        out({"type": "system", "subtype": "init", "session_id": sid})

    if a.fail_times and a.counter:
        n = int(open(a.counter).read()) if os.path.exists(a.counter) else 0
        with open(a.counter, "w") as fh:
            fh.write(str(n + 1))
        if n < a.fail_times:
            sys.stderr.write("API Error: 529 overloaded_error: Overloaded\n")
            out({"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 1,
                 "error": "overloaded_error", "session_id": sid})
            return 1

    for spec in a.write:
        rel, _, text = spec.partition("=")
        path = os.path.join(os.getcwd(), rel)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace("\\n", "\n"))

    if a.tokens:
        out({"type": "stream_event", "session_id": sid, "event": {
            "type": "message_delta", "usage": {"input_tokens": 10, "output_tokens": a.tokens,
                                              "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 0}}})
    if a.hang_sec:
        for _ in range(int(a.hang_sec * 10)):
            time.sleep(0.1)

    if a.agy:
        if a.handoff:
            os.makedirs(".telecode", exist_ok=True)
            with open(os.path.join(".telecode", "handoff.json"), "w", encoding="utf-8") as fh:
                fh.write(a.handoff)
        out({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": a.text}})
        out({"event": "result", "result": {"status": "SUCCESS", "response": a.text, "num_turns": 1,
                                           "conversation_id": sid, "usage": {"input_tokens": 100, "output_tokens": 20}}})
        return 0

    usage = {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 1000,
             "cache_creation_input_tokens": 50}
    res = {"type": "result", "subtype": "error_max_budget_usd" if a.budget_hit else "success",
           "is_error": bool(a.budget_hit), "result": a.text, "session_id": sid, "num_turns": 1,
           "total_cost_usd": 0.0123, "duration_ms": 10, "usage": usage}
    if a.handoff:
        res["structured_output"] = json.loads(a.handoff)
    out({"type": "assistant", "session_id": sid,
         "message": {"id": "m1", "content": [{"type": "text", "text": a.text}]}})
    out(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
