#!/usr/bin/env python3
"""skwhisper curator writes and digests from the daemon log text.

    baseline_skwhisper.py --log FILE --end YYYY-MM-DD [--days 1]

FILE is the daemon log or its journal export. A line counts toward a day by the "YYYY-MM-DD HH:MM:SS
[skwhisper" stamp the daemon itself writes. Counts lines saying a whisper context file was written
and lines saying a session was digested. Line text is matched, never copied.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_common as bc  # noqa: E402

STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} \[skwhisper")


def compute(log, end: str, days: int = 1) -> dict:
    start, stop = bc.window(end, days)
    first, last = start.date().isoformat(), (stop.date()).isoformat()
    writes = digests = 0
    with open(log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            m = STAMP.search(line)
            if not m or not first <= m.group(1) < last:
                continue
            writes += "Wrote whisper context" in line
            digests += "Digested session" in line
    return bc.emit("skwhisper", end, days, {"whisper_writes": writes, "digests": digests,
                                            "writes_per_day": round(writes / days, 1)})


def main() -> int:
    def extra(p):
        p.add_argument("--log", required=True)
        p.add_argument("--days", type=int, default=1)
    a = bc.cli(__doc__.splitlines()[0], extra)
    return bc.run(compute(a.log, a.end, a.days))


if __name__ == "__main__":
    sys.exit(main())
