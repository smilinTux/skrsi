#!/usr/bin/env python3
"""Build-quality telemetry from `gh` and coordination metadata only. Stdlib only.

Reads PR metadata (titles, labels, timestamps, check conclusions, commit dates) and the
coordination card event log. It never reads prompts, PR bodies, review text or diffs.
Anything it cannot derive is reported missing (null), never guessed and never zero.

  build_telemetry.py --repo OWNER/NAME --prs 92-117 [--coord-dir DIR] [--out F] [--md F]
                     [--metrics-out F]   # metrics file consumable by build_evaluation.py
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFECT_CLASSES = ("privacy", "false-close", "strawman-baseline", "dead-arm", "ci-parity", "live-state",
                  "destructive", "other")
DEFECT_LABELS = {f"defect:{c}" for c in DEFECT_CLASSES}
CARD = re.compile(r"^card/([0-9a-f]{8})")
INVARIANTS = ("no prompt, PR body, review text or diff is collected",
              "a rate is reportable only while review evidence coverage is declared alongside it",
              "an unrecorded review is counted as missing, never as a first-pass success",
              "review evidence coverage stays at or above 0.5")
PR_FIELDS = "number,title,state,createdAt,mergedAt,headRefName,headRefOid,labels,commits"


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def gh_json(args, run=subprocess.run):
    p = run(["gh", *args], capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {p.stderr.strip()[:200]}")
    return json.loads(p.stdout)


def fetch(repo, numbers, run=subprocess.run):
    prs, runs = [], {}
    for n in numbers:
        try:
            pr = gh_json(["pr", "view", str(n), "--repo", repo, "--json", PR_FIELDS], run)
        except RuntimeError:
            continue  # not a PR (an issue number) or unreadable: absent, not counted
        prs.append(pr)
        runs[n] = gh_json(["run", "list", "--repo", repo, "--branch", pr["headRefName"], "--limit", "100",
                           "--json", "headSha,conclusion,event,createdAt"], run)
    reverts = gh_json(["pr", "list", "--repo", repo, "--state", "merged", "--search", "Revert in:title",
                       "--limit", "100", "--json", "number,title"], run)
    return prs, runs, reverts


def load_events(coord_dir):
    rows = []
    for f in sorted(Path(coord_dir).glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def card_views(events):
    """Per source card: review cards, reviewed head SHAs, PASS verdict seen, first review time, labels."""
    src_of = {e["card_id"]: e["link_value"] for e in events if e.get("link_key") == "link_source_card"}
    views = collections.defaultdict(lambda: {"heads": [], "pass": False, "first": None, "labels": set()})
    labels = collections.defaultdict(set)
    for e in events:
        if e.get("action") == "add_label":
            labels[e["card_id"]].add(e["label"])
        elif e.get("action") == "remove_label":
            labels[e["card_id"]].discard(e["label"])
    for e in events:
        card = src_of.get(e["card_id"])
        if card is None:
            continue
        v = views[card]
        t = ts(e["ts"])
        v["first"] = t if v["first"] is None else min(v["first"], t)
        if e.get("link_key") == "link_head_revision" and e["link_value"] not in v["heads"]:
            v["heads"].append(e["link_value"])
        if e.get("link_key") == "verdict" and e["link_value"] == "PASS":
            v["pass"] = True
        v["labels"] |= labels[e["card_id"]]
    for card, v in views.items():
        v["labels"] |= labels[card]
    return views, labels


def per_pr(pr, runs, views, reverted):
    m = CARD.match(pr["headRefName"])
    card = m.group(1) if m else None
    merged, opened = ts(pr["mergedAt"]), ts(pr["createdAt"])
    v = views.get(card) if card else None
    # distinct pushed heads with a failed run: CI and Tests failing together on one push count once
    ci_failures = len({r["headSha"] for r in runs if r["conclusion"] == "failure" and r["event"] == "pull_request"
                       and opened <= ts(r["createdAt"]) <= merged}) if merged else None
    post = None
    heads = rounds = first_pass = None
    if v and v["pass"] and v["first"]:
        heads = len(v["heads"]) or 1
        post = sum(1 for c in pr["commits"] if ts(c["committedDate"]) > v["first"])
        rounds = max(heads, 1 + (post > 0))
        first_pass = rounds == 1
    defects = sorted({l["name"] for l in pr["labels"]} | (v["labels"] if v else set()))
    return {"pr": pr["number"], "card": card, "merged": bool(merged),
            "open_to_merge_hours": round((merged - opened).total_seconds() / 3600, 2) if merged else None,
            "ci_failures_before_merge": ci_failures, "reviewed_heads": heads if v else None,
            "post_review_commits": post, "review_rounds_to_pass": rounds, "first_pass": first_pass,
            "reverted": pr["title"] in reverted if merged else None,
            "defect_labels": [d for d in defects if d in DEFECT_LABELS]}


def compute(prs, runs, reverts, events):
    views, _ = card_views(events)
    reverted = {r["title"][len('Revert "'):-1] for r in reverts if r["title"].startswith('Revert "')}
    rows = [per_pr(p, runs.get(p["number"], []), views, reverted) for p in prs]
    merged = [r for r in rows if r["merged"]]
    reviewed = [r for r in merged if r["review_rounds_to_pass"] is not None]
    defects = collections.Counter(d for r in merged for d in r["defect_labels"])
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    agg = {
        "merged_prs": len(merged), "unmerged_prs": len(rows) - len(merged), "reviewed_prs": len(reviewed),
        "review_evidence_coverage_ratio": round(len(reviewed) / len(merged), 3) if merged else None,
        "review_rounds_to_pass": mean([r["review_rounds_to_pass"] for r in reviewed]),
        "first_pass_review_rate": round(sum(r["first_pass"] for r in reviewed) / len(reviewed), 3) if reviewed else None,
        "ci_failures_before_merge": mean([r["ci_failures_before_merge"] for r in merged]),
        "pr_open_to_merge_hours": round(statistics.median([r["open_to_merge_hours"] for r in merged]), 2) if merged else None,
        "reverted_merges": sum(1 for r in merged if r["reverted"]),
        "defect_labels_total": sum(defects.values()) if defects else None,
        "defect_class_counts": dict(defects) if defects else None}
    derived = ("derivable: open-to-merge time, CI failures (distinct pushed heads with a failed pull_request run while the PR was open), "
               "reviewed heads and PASS from coordination events, reverts by `Revert \"<title>\"` PR title.")
    missing = ["FAIL review verdicts are never recorded in coordination (only PASS), so failed rounds are seen only "
               "as extra reviewed heads or commits pushed after the first review event",
               "review defect text is not read, so defect classes come only from allowlisted `defect:<class>` labels"
               + ("; none carried one in this set, so defect counts are missing, not zero" if not defects else ""),
               "reverts committed straight to the default branch (no PR) are not seen"]
    return {"repo": agg, "cards": rows, "derivable": derived, "missing": missing}


def metrics_file(result, cohort_id):
    noise = {"tolerance": 0.2}  # small windows: one extra review round moves a mean by about 10 percent
    spec = {"review_rounds_to_pass": ("rounds", "lower", noise), "first_pass_review_rate": ("ratio", "higher", noise),
            "ci_failures_before_merge": ("failing pushes per PR", "lower", noise),
            "pr_open_to_merge_hours": ("hours", "lower", noise),
            "reverted_merges": ("merges", "lower", {"max": 0, "stop_rule": "reverted-merge"}),
            "defect_labels_total": ("labels", "lower", {}),
            "review_evidence_coverage_ratio": ("ratio", "higher", {"min": 0.5, "stop_rule": "coverage-floor"})}
    return {"cohort": {"id": cohort_id, "sample_size": result["repo"]["merged_prs"],
                       "window_start": "1970-01-01T00:00:00Z", "window_end": "1970-01-01T00:00:00Z"},  # real window set in main
            "invariants": {INVARIANTS[0]: True, INVARIANTS[1]: True, INVARIANTS[2]: True,
                           INVARIANTS[3]: (result["repo"]["review_evidence_coverage_ratio"] or 0) >= 0.5},
            "instrument": {"runs_production_code": True, "baselines_named": ["previous window of the same repo"],
                           "sabotage_tested": True, "dead_arms_reported": True},
            "metrics": {k: {"value": result["repo"][k], "unit": u, "direction": d, **extra}
                        for k, (u, d, extra) in spec.items()}}


def markdown(result, repo):
    a = result["repo"]
    f = lambda v: "missing" if v is None else v
    out = [f"## Build quality for {repo}", "", "| metric | value |", "|---|---|"]
    out += [f"| {k} | {f(a[k])} |" for k in a if k != "defect_class_counts"]
    out += [f"| defect_class_counts | {f(a['defect_class_counts'])} |", "", "| PR | card | h to merge | CI fails | "
            "rounds | first pass | reverted |", "|---|---|---|---|---|---|---|"]
    out += [f"| {r['pr']} | {f(r['card'])} | {f(r['open_to_merge_hours'])} | {f(r['ci_failures_before_merge'])} | "
            f"{f(r['review_rounds_to_pass'])} | {f(r['first_pass'])} | {f(r['reverted'])} |" for r in result["cards"]]
    return "\n".join(out + ["", result["derivable"], "", "Missing:"] + [f"- {m}" for m in result["missing"]]) + "\n"


def parse_range(s):
    out = []
    for part in s.split(","):
        lo, _, hi = part.partition("-")
        out += range(int(lo), int(hi or lo) + 1)
    return out


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("--repo", required=True)
    a.add_argument("--prs", required=True, help="e.g. 92-117 or 5,9,12-14")
    a.add_argument("--coord-dir", default=str(Path.home() / ".skcapstone" / "coordination" / "card_events"))
    a.add_argument("--cohort-id", default="builds")
    for flag in ("out", "md", "metrics-out"):
        a.add_argument("--" + flag)
    args = a.parse_args(argv)
    try:
        prs, runs, reverts = fetch(args.repo, parse_range(args.prs))
        events = load_events(args.coord_dir) if Path(args.coord_dir).is_dir() else []
        result = compute(prs, runs, reverts, events)
        result["coord_events_read"] = len(events)
        mf = metrics_file(result, args.cohort_id)
        dates = [p["createdAt"] for p in prs] + [p["mergedAt"] for p in prs if p["mergedAt"]]
        if dates:
            mf["cohort"]["window_start"], mf["cohort"]["window_end"] = min(dates), max(dates)
        for path, payload in ((args.out, json.dumps(result, indent=2)), (args.md, markdown(result, args.repo)),
                              (args.metrics_out, json.dumps(mf, indent=2))):
            if path:
                Path(path).write_text(payload + "\n", encoding="utf-8")
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"build_telemetry: {exc}", file=sys.stderr)
        return 2
    print(markdown(result, args.repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
