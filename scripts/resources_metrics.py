#!/usr/bin/env python3
"""Flatten a window of `resources` evidence into a metrics file for sk-node-resource-efficiency.

    resources_metrics.py --instance DIR --start YYYY-MM-DD --end YYYY-MM-DD
                         [--nodes a,b] [--baseline METRICS.json] [--out FILE]

Reads `evidence/<node>/<date>.json` from a local instance checkout for every day in [start, end] (both
included) and every node whose `cluster.yaml` readers include `resources` (or `--nodes`), and prints one
metrics file in the shape `build_evaluation.py` takes. Stdlib only, read-only, aggregates only.

The reader record it expects (written by the collector, `readers.resources.result`):

    {"units_declared": 3,
     "units": {"<unit>": {"status": "ok", "cpu_core_pct": 1.5, "cpu_basis": "delta", "cpu_window_s": 86400,
                          "rss_mb": 120.0, "mem_peak_mb": 300.0, "kills": 0, "restarts": 0},
               "<unit>": {"status": "missing"}},
     "disk": {"mounts": 1, "free_pct_min": 12.5, "free_gb_min": 30.1},
     "sync": {"<folder>": {"status": "ok", "files": 1000, "need_files": 0}}}

How each metric is aggregated over the window:

- `service_cpu_core_pct_*`, `service_rss_mb_*`: the mean of each (node, unit) pair over the days it was
  measured, then summed over pairs (`_total`) or the largest pair (`_max`). 100 is one full core.
- `service_kill_count`, `service_restart_count`: all kills or restarts in the window, per calendar day.
- `node_disk_free_pct_min`: the lowest free percentage seen on any node on any day.
- `sync_folder_*_max`: the largest file count and the largest out-of-sync file count on any node and day.
- `resource_coverage_ratio`: measured unit-days over declared unit-days. A node-day with no evidence, or
  whose reader is not ok, counts all of that node's declared units as unmeasured. A node never seen in the
  window is assumed to declare as many units as the largest declaration seen.

A value that cannot be computed is `null`, which the evaluator reports as missingness, never as zero.
The cohort id carries a digest of the tracked (node, unit) allowlist. With `--baseline`, the invariant that
both sides track the same allowlist is asserted by comparing that digest; without it the invariant is left
unasserted, so the evaluation names it as missing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cluster_config import load_cluster_yaml  # noqa: E402

INV_NAMES = "only declared unit names, node short names and numbers are collected; no command lines, paths, environment or log text"
INV_MISSING = "an unmeasured service or node is counted as missing, never as idle"
INV_CPU = "cpu is measured from cumulative cgroup counters over a stated window, never from one instantaneous sample"
INV_ALLOWLIST = "the tracked unit allowlist is the same on both sides of the comparison"
CUMULATIVE = {"delta", "since_start"}
DIGEST = re.compile(r"-units-([0-9a-f]{8})$")

RULES = {
    "service_cpu_core_pct_total": {"unit": "core-pct", "direction": "lower", "tolerance": 0.1},
    "service_cpu_core_pct_max": {"unit": "core-pct", "direction": "lower", "tolerance": 0.1},
    "service_rss_mb_total": {"unit": "MB", "direction": "lower", "tolerance": 0.1},
    "service_rss_mb_max": {"unit": "MB", "direction": "lower", "tolerance": 0.1},
    "service_kill_count": {"unit": "kills/day", "direction": "lower", "tolerance": 0.0, "noise": 1.0,
                           "stop_rule": "kills-rise"},
    "service_restart_count": {"unit": "restarts/day", "direction": "lower", "tolerance": 0.25, "noise": 1.0},
    "node_disk_free_pct_min": {"unit": "pct", "direction": "higher", "tolerance": 0.05, "min": 2,
                               "stop_rule": "disk-floor"},
    "sync_folder_file_count_max": {"unit": "files", "direction": "lower", "tolerance": 0.02},
    "sync_folder_need_files_max": {"unit": "files", "direction": "lower", "tolerance": 0.5, "noise": 100,
                                   "stop_rule": "sync-behind"},
    "resource_coverage_ratio": {"unit": "ratio", "direction": "higher", "tolerance": 0.0, "min": 0.8,
                                "stop_rule": "coverage-floor"},
}
INSTRUMENT = {"runs_production_code": True, "baselines_named": ["previous-window"], "sabotage_tested": False,
              "dead_arms_reported": True}


def _days(start: str, end: str) -> list[str]:
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if b < a:
        raise SystemExit("--end is before --start")
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _r(v, places=2):
    return None if v is None else round(v, places)


def compute(instance: Path, start: str, end: str, nodes: list[str] | None = None,
            baseline: dict | None = None) -> dict:
    days = _days(start, end)
    if nodes is None:
        cluster = load_cluster_yaml((instance / "cluster.yaml").read_text(encoding="utf-8"))
        nodes = [n["name"] for n in cluster.get("nodes", []) if "resources" in n.get("readers", [])]
    per_pair: dict[tuple[str, str], dict[str, list[float]]] = {}
    allowlist: set[tuple[str, str]] = set()
    declared_by_node: dict[str, int] = {}
    seen_days: dict[str, set[str]] = {n: set() for n in nodes}
    measured = kills = restarts = sample = 0
    disk_min = files_max = need_max = None
    cpu_cumulative = True
    for node in nodes:
        for day in days:
            path = instance / "evidence" / node / f"{day}.json"
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            reader = (rec.get("readers") or {}).get("resources") or {}
            res = reader.get("result")
            if reader.get("status") != "ok" or not isinstance(res, dict):
                continue
            units = res.get("units") or {}
            declared = res.get("units_declared")
            declared = declared if _num(declared) else len(units)
            declared_by_node[node] = max(declared_by_node.get(node, 0), int(declared))
            seen_days[node].add(day)
            sample += 1
            for name, u in units.items():
                allowlist.add((node, name))
                if not isinstance(u, dict) or u.get("status") != "ok":
                    continue
                measured += 1
                vals = per_pair.setdefault((node, name), {"cpu": [], "rss": []})
                if _num(u.get("cpu_core_pct")):
                    vals["cpu"].append(u["cpu_core_pct"])
                    cpu_cumulative &= u.get("cpu_basis") in CUMULATIVE
                if _num(u.get("rss_mb")):
                    vals["rss"].append(u["rss_mb"])
                kills += u.get("kills", 0) if _num(u.get("kills")) else 0
                restarts += u.get("restarts", 0) if _num(u.get("restarts")) else 0
            disk = res.get("disk") or {}
            if _num(disk.get("free_pct_min")):
                disk_min = disk["free_pct_min"] if disk_min is None else min(disk_min, disk["free_pct_min"])
            for folder in (res.get("sync") or {}).values():
                if not isinstance(folder, dict) or folder.get("status") != "ok":
                    continue
                if _num(folder.get("files")):
                    files_max = folder["files"] if files_max is None else max(files_max, folder["files"])
                if _num(folder.get("need_files")):
                    need_max = folder["need_files"] if need_max is None else max(need_max, folder["need_files"])
    fallback = max(declared_by_node.values(), default=0)
    expected = sum(len(days) * declared_by_node.get(n, fallback) for n in nodes)

    def means(key):
        return [sum(v[key]) / len(v[key]) for v in per_pair.values() if v[key]]

    cpu, rss = means("cpu"), means("rss")
    values = {
        "service_cpu_core_pct_total": _r(sum(cpu)) if cpu else None,
        "service_cpu_core_pct_max": _r(max(cpu)) if cpu else None,
        "service_rss_mb_total": _r(sum(rss), 1) if rss else None,
        "service_rss_mb_max": _r(max(rss), 1) if rss else None,
        "service_kill_count": _r(kills / len(days)) if sample else None,
        "service_restart_count": _r(restarts / len(days)) if sample else None,
        "node_disk_free_pct_min": _r(disk_min),
        "sync_folder_file_count_max": files_max,
        "sync_folder_need_files_max": need_max,
        "resource_coverage_ratio": _r(measured / expected, 3) if expected else None,
    }
    digest = hashlib.sha256("\n".join(f"{n}/{u}" for n, u in sorted(allowlist)).encode()).hexdigest()[:8]
    invariants = {INV_NAMES: True, INV_MISSING: True, INV_CPU: cpu_cumulative}
    if baseline is not None:
        m = DIGEST.search(str((baseline.get("cohort") or {}).get("id", "")))
        invariants[INV_ALLOWLIST] = bool(m) and m.group(1) == digest
    return {
        "cohort": {"id": f"resources-{start}-to-{end}-units-{digest}", "sample_size": sample,
                   "window_start": f"{start}T00:00:00Z", "window_end": f"{end}T23:59:59Z"},
        "instrument": dict(INSTRUMENT),
        "invariants": invariants,
        "metrics": {k: {"value": values[k], **RULES[k]} for k in RULES},
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--instance", required=True, type=Path, help="local checkout of the instance repo")
    p.add_argument("--start", required=True, help="first day, included (YYYY-MM-DD, UTC)")
    p.add_argument("--end", required=True, help="last day, included (YYYY-MM-DD, UTC)")
    p.add_argument("--nodes", help="comma-separated node names (default: nodes with the resources reader)")
    p.add_argument("--baseline", type=Path, help="the other side's metrics file, to check the allowlist digest")
    p.add_argument("--out", type=Path, help="write here instead of stdout")
    a = p.parse_args(argv)
    base = json.loads(a.baseline.read_text(encoding="utf-8")) if a.baseline else None
    doc = compute(a.instance, a.start, a.end, a.nodes.split(",") if a.nodes else None, base)
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
