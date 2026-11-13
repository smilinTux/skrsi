#!/usr/bin/env python3
"""Shared helpers for the baseline_*.py scripts (card 3611ea28, P0.1).

Each baseline script takes its source paths and a window end date as arguments, reads its
source read-only, and prints one JSON aggregate document: counts, rates and durations only.
Nothing here runs in a request, claim, dispatch, completion, search or session start.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

UTC = dt.timezone.utc


def window(end: str, days: int | None) -> tuple[dt.datetime, dt.datetime]:
    """[start, stop) in UTC. `end` is the last day INCLUDED (YYYY-MM-DD); days=None means no lower bound."""
    stop = dt.datetime.fromisoformat(end).replace(tzinfo=UTC) + dt.timedelta(days=1)
    return (stop - dt.timedelta(days=days) if days else dt.datetime.min.replace(tzinfo=UTC)), stop


def parse_time(value) -> dt.datetime | None:
    """ISO 8601 with Z or an offset, or epoch seconds, as aware UTC. None when it cannot be read."""
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value, UTC)
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def json_lines(path: Path):
    """Yield every JSON object in a JSONL file; malformed lines are skipped, not fatal."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                yield item


def percentile(values, p: float):
    """Nearest rank: the smallest value with at least p percent of the sample at or below it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)] if ordered else None


def median(values):
    if not values:
        return None
    m = statistics.median(values)
    return int(m) if m == int(m) else m


def pct(part: int, whole: int, places: int = 1):
    return round(100 * part / whole, places) if whole else None


def emit(schema: str, end: str, days: int | None, body: dict) -> dict:
    return {"schema": f"skrsi.baseline.{schema}.v1", "window_days": days, "window_end": end, **body}


def cli(description: str, extra) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--end", required=True, help="last day of the window, included (YYYY-MM-DD, UTC)")
    extra(parser)
    return parser.parse_args()


def run(args_doc: dict) -> int:
    json.dump(args_doc, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0
