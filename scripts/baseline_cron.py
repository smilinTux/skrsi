#!/usr/bin/env python3
"""Scheduled-run reliability from the sk-cron-run ledger.

    baseline_cron.py --ledger FILE --end YYYY-MM-DD [--days 30] [--host NAME] [--exclude-host NAME ...] [--min-streak 5]

`--exclude-host` (repeatable) drops a host's runs, for hosts that share the merged ledger but are not
part of the baseline (test hosts, other machines); it applies after `--host`. A ledger line is JSON with job, start (ISO 8601), ok and optionally host. A streak is consecutive
failed runs of one job in start order. Output fields are counts and rates only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_common as bc  # noqa: E402


def compute(ledger, end: str, days: int = 30, host: str | None = None, min_streak: int = 5,
            exclude_hosts=()) -> dict:
    start, stop = bc.window(end, days)
    runs: dict[str, list[tuple]] = {}
    for item in bc.json_lines(Path(ledger)):
        when = bc.parse_time(item.get("start"))
        if when is None or not start <= when < stop or (host and item.get("host") != host) \
                or item.get("host") in exclude_hosts:
            continue
        runs.setdefault(str(item.get("job")), []).append((when, bool(item.get("ok"))))
    longest = {}
    for job, rows in runs.items():
        best = now = 0
        for _, ok in sorted(rows, key=lambda r: r[0]):
            now = 0 if ok else now + 1
            best = max(best, now)
        longest[job] = best
    total = sum(len(r) for r in runs.values())
    failed = sum(1 for r in runs.values() for _, ok in r if not ok)
    streaks = sorted((v for v in longest.values() if v >= min_streak), reverse=True)
    top = sorted(longest.values(), reverse=True)[:3]
    return bc.emit("cron", end, days, {"runs": total, "failed": failed, "failed_pct": bc.pct(failed, total, 2),
                                       "jobs_total": len(runs), "min_streak": min_streak,
                                       "jobs_with_streak": len(streaks), "longest_streaks": top})


def main() -> int:
    def extra(p):
        p.add_argument("--ledger", required=True)
        p.add_argument("--days", type=int, default=30)
        p.add_argument("--host", help="count only this host's runs")
        p.add_argument("--exclude-host", action="append", default=[], metavar="NAME")
        p.add_argument("--min-streak", type=int, default=5)
    a = bc.cli(__doc__.splitlines()[0], extra)
    return bc.run(compute(a.ledger, a.end, a.days, a.host, a.min_streak, a.exclude_host))


if __name__ == "__main__":
    sys.exit(main())
