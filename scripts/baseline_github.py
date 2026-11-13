#!/usr/bin/env python3
"""Share of merged PRs titled as fixes, from recorded GitHub JSON.

    baseline_github.py --pr NAME=FILE [--pr NAME=FILE ...] --end YYYY-MM-DD [--days 14]

FILE holds the output of `gh pr list -R OWNER/NAME --state merged --limit 500
--json number,title,mergedAt`. Only the title prefix and merge time are read.
A fix is a title that starts with the word "fix" (fix:, fix(scope):, Fix ...), not "fixture".
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_common as bc  # noqa: E402

FIX = re.compile(r"^\s*fix\b", re.IGNORECASE)


def compute(files: dict, end: str, days: int = 14) -> dict:
    start, stop = bc.window(end, days)
    repos = {}
    for name, path in sorted(files.items()):
        prs = json.loads(Path(path).read_text(encoding="utf-8"))
        merged = [p for p in prs if bc.parse_time(p.get("mergedAt")) and start <= bc.parse_time(p["mergedAt"]) < stop]
        fixes = sum(1 for p in merged if FIX.match(str(p.get("title") or "")))
        repos[name] = {"merged": len(merged), "fix": fixes, "fix_pct": bc.pct(fixes, len(merged))}
    return bc.emit("github", end, days, {"repos": repos})


def main() -> int:
    def extra(p):
        p.add_argument("--pr", action="append", required=True, metavar="NAME=FILE")
        p.add_argument("--days", type=int, default=14)
    a = bc.cli(__doc__.splitlines()[0], extra)
    return bc.run(compute(dict(x.split("=", 1) for x in a.pr), a.end, a.days))


if __name__ == "__main__":
    sys.exit(main())
