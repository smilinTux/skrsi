#!/usr/bin/env python3
"""Gateway request statistics from the live metrics.db, opened read-only.

    baseline_gateway.py --db FILE --end YYYY-MM-DD [--days 7] [--route NAME]
                        [--deploy-repo DIR --main-ref REF]

Reads request_log only. `route_*` count requests whose requested model is the route in the window.
`no_model_404` counts a 404 whose requested model is empty or "unknown". `rows_*` count the whole
table up to the window end. With --deploy-repo, reports the build commit of that checkout and how
far REF has moved on from it (git, read-only).
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_common as bc  # noqa: E402


def open_ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)


def _git(repo, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)


def build_fact(repo, main_ref: str) -> dict:
    sha = _git(repo, "rev-parse", "--short=7", "HEAD").stdout.strip()
    behind = _git(repo, "rev-list", "--count", f"HEAD..{main_ref}").stdout.strip()
    ancestor = _git(repo, "merge-base", "--is-ancestor", "HEAD", main_ref).returncode == 0
    return {"build_sha": sha, "commits_behind_main": int(behind) if behind.isdigit() else None,
            "is_ancestor_of_main": ancestor}


def compute(db, end: str, days: int = 7, route: str = "sk-default") -> dict:
    start, stop = bc.window(end, days)
    lo, hi = int(start.timestamp() * 1000), int(stop.timestamp() * 1000)
    con = open_ro(db)
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0] or 0  # noqa: E731
    inside = "started_at >= ? AND started_at < ?"
    requests = one(f"SELECT COUNT(*) FROM request_log WHERE model = ? AND {inside}", route, lo, hi)
    bad = one(f"SELECT COUNT(*) FROM request_log WHERE model = ? AND status_code = 502 AND {inside}", route, lo, hi)
    none404 = one("SELECT COUNT(*) FROM request_log WHERE status_code = 404 AND "
                  f"(model IS NULL OR model IN ('', 'unknown')) AND {inside}", lo, hi)
    before = "started_at < ?"
    body = {"route": route, "route_requests": requests, "route_502": bad, "route_502_pct": bc.pct(bad, requests),
            "no_model_404": none404,
            "rows_total": one(f"SELECT COUNT(*) FROM request_log WHERE {before}", hi),
            "rows_cost_unknown": one(f"SELECT COUNT(*) FROM request_log WHERE cost_truth = 'unknown' AND {before}", hi),
            "rows_agent_id_set": one(f"SELECT COUNT(*) FROM request_log WHERE agent_id IS NOT NULL AND {before}", hi),
            "rows_session_id_set": one(f"SELECT COUNT(*) FROM request_log WHERE session_id IS NOT NULL AND {before}", hi),
            "rows_client_set": one(f"SELECT COUNT(*) FROM request_log WHERE client IS NOT NULL AND {before}", hi)}
    con.close()
    return bc.emit("gateway", end, days, body)


def main() -> int:
    def extra(p):
        p.add_argument("--db", required=True)
        p.add_argument("--days", type=int, default=7)
        p.add_argument("--route", default="sk-default")
        p.add_argument("--deploy-repo")
        p.add_argument("--main-ref", default="origin/main")
    a = bc.cli(__doc__.splitlines()[0], extra)
    doc = compute(a.db, a.end, a.days, a.route)
    if a.deploy_repo:
        doc["build"] = build_fact(a.deploy_repo, a.main_ref)
    return bc.run(doc)


if __name__ == "__main__":
    sys.exit(main())
