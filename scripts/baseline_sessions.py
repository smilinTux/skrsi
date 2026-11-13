#!/usr/bin/env python3
"""Agent session shape from Claude Code transcripts: usage fields only, never message content.

    baseline_sessions.py --projects DIR --end YYYY-MM-DD [--days 7] [--share-model opus]

Reads DIR/<project>/<session>.jsonl (one level, so sub-agent transcripts are not double counted).
Two bases are reported: `records` counts every assistant record that carries a usage block (one
record per content block, how SPEC section 2.1 counted), `requests` counts one per API request id.
Context of a turn is input + cache-read + cache-write tokens. Percentiles are nearest rank.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_common as bc  # noqa: E402

FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _turns(projects: Path, start, stop):
    for path in sorted(projects.glob("*/*.jsonl")):
        for item in bc.json_lines(path):
            message = item.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            when = bc.parse_time(item.get("timestamp"))
            if item.get("type") != "assistant" or not isinstance(usage, dict) or when is None or not start <= when < stop:
                continue
            counts = [usage.get(f) if isinstance(usage.get(f), int) else 0 for f in FIELDS]
            yield {"uuid": item.get("uuid"), "request": item.get("requestId") or message.get("id"),
                   "session": item.get("sessionId") or path.stem, "when": when, "model": str(message.get("model") or ""),
                   "in": counts[0], "cw": counts[1], "cr": counts[2], "out": counts[3]}


def _summary(turns: list[dict], share_model: str) -> dict:
    ctx = [t["in"] + t["cw"] + t["cr"] for t in turns]
    by_session: dict[str, list[dict]] = {}
    for t in sorted(turns, key=lambda t: t["when"]):
        by_session.setdefault(t["session"], []).append(t)
    starts = [s[0]["in"] + s[0]["cw"] + s[0]["cr"] for s in by_session.values()]
    counts = [len(s) for s in by_session.values()]
    out = sum(t["out"] for t in turns)
    share = sum(t["out"] for t in turns if share_model in t["model"].lower())
    return {"turns": len(turns), "sessions": len(by_session),
            "cache_read_tokens": sum(t["cr"] for t in turns), "cache_write_tokens": sum(t["cw"] for t in turns),
            "output_tokens": out, "output_share_pct": bc.pct(share, out), "output_share_model": share_model,
            "context_start_median": bc.median(starts), "context_start_p90": bc.percentile(starts, 90),
            "context_turn_median": bc.median(ctx), "context_turn_p90": bc.percentile(ctx, 90),
            "input_share_above_200k_pct": bc.pct(sum(c for c in ctx if c > 200_000), sum(ctx)),
            "turns_per_session_median": bc.median(counts), "turns_per_session_p90": bc.percentile(counts, 90),
            "turns_per_session_max": max(counts, default=None)}


def compute(projects, end: str, days: int = 7, share_model: str = "opus") -> dict:
    start, stop = bc.window(end, days)
    records = {t["uuid"] or id(t): t for t in _turns(Path(projects), start, stop)}
    requests = {t["request"] or id(t): t for t in records.values()}
    return bc.emit("sessions", end, days, {"records": _summary(list(records.values()), share_model),
                                           "requests": _summary(list(requests.values()), share_model)})


def main() -> int:
    def extra(p):
        p.add_argument("--projects", required=True)
        p.add_argument("--days", type=int, default=7)
        p.add_argument("--share-model", default="opus", help="substring of the model name for the output share")
    a = bc.cli(__doc__.splitlines()[0], extra)
    return bc.run(compute(a.projects, a.end, a.days, a.share_model))


if __name__ == "__main__":
    sys.exit(main())
