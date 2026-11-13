#!/usr/bin/env python3
"""skmemory search latency and arm timeouts from retrieval.jsonl.

    baseline_skmemory.py --log FILE --end YYYY-MM-DD [--days 7]

Each line carries ts (epoch seconds), elapsed_ms and per-arm status. The log holds a query hash
and arm status, never query text, and only elapsed_ms and the arm statuses are read.
A search times out when any arm reports status "timeout".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_common as bc  # noqa: E402


def compute(log, end: str, days: int = 7) -> dict:
    start, stop = bc.window(end, days)
    elapsed, timed_out = [], 0
    for row in bc.json_lines(Path(log)):
        when = bc.parse_time(row.get("ts"))
        if when is None or not start <= when < stop or not isinstance(row.get("elapsed_ms"), (int, float)):
            continue
        elapsed.append(row["elapsed_ms"])
        arms = row.get("arms") if isinstance(row.get("arms"), dict) else {}
        timed_out += any(isinstance(a, dict) and a.get("status") == "timeout" for a in arms.values())
    return bc.emit("skmemory", end, days, {"searches": len(elapsed), "median_ms": bc.median(elapsed),
                                           "p95_ms": bc.percentile(elapsed, 95),
                                           "searches_with_arm_timeout": timed_out,
                                           "arm_timeout_pct": bc.pct(timed_out, len(elapsed), 2)})


def main() -> int:
    def extra(p):
        p.add_argument("--log", required=True)
        p.add_argument("--days", type=int, default=7)
    a = bc.cli(__doc__.splitlines()[0], extra)
    return bc.run(compute(a.log, a.end, a.days))


if __name__ == "__main__":
    sys.exit(main())
