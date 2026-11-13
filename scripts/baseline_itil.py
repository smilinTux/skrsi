#!/usr/bin/env python3
"""Incidents, problems and known-error entries from the ITIL store.

    baseline_itil.py --itil DIR --end YYYY-MM-DD

Reads DIR/incidents/*/core.json, DIR/problems/*/core.json and DIR/kedb/*.json. An incident is
detected up to the end of the end day. `incidents_linked` counts incidents whose own related_problem_id
is set; `incidents_listed_by_problem` counts incidents some problem lists in related_incident_ids.
Redirect stubs have no core.json and are not counted.
Only ids, dates and the link fields are read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_common as bc  # noqa: E402


def _cores(directory: Path):
    for path in sorted(directory.glob("*/core.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            yield doc


def compute(itil, end: str) -> dict:
    root = Path(itil)
    _, stop = bc.window(end, None)
    problems = list(_cores(root / "problems"))
    listed = {str(i) for p in problems for i in (p.get("related_incident_ids") or [])}
    incidents = [i for i in _cores(root / "incidents")
                 if (bc.parse_time(i.get("detected_at")) or stop) < stop]
    own = sum(1 for i in incidents if i.get("related_problem_id"))
    by_problem = sum(1 for i in incidents if str(i.get("id")) in listed)
    return bc.emit("itil", end, None, {"incidents": len(incidents), "incidents_linked": own,
                                       "incidents_listed_by_problem": by_problem,
                                       "problems": len(problems), "kedb_entries": len(list((root / "kedb").glob("*.json")))})


def main() -> int:
    a = bc.cli(__doc__.splitlines()[0], lambda p: p.add_argument("--itil", required=True))
    return bc.run(compute(a.itil, a.end))


if __name__ == "__main__":
    sys.exit(main())
