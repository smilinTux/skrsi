#!/usr/bin/env python3
"""Render a built skrsi.evaluation.v4 record as the PR comment. Stdlib only, no decisions made here."""
from __future__ import annotations


def render(rec, table, triggered, unchecked, notes=()) -> str:
    """Signal first. The record keeps every missingness entry; the comment folds what repeats on every PR."""
    f = lambda v: "missing" if v is None else f"{v:.6g}"
    names = lambda items: ", ".join(f"`{n}`" for n in items)
    i = rec["instrument"]
    moved = [r for r in table if r[4] != "flat" and not (r[4] == "missing" and r[1] is None and r[2] is None)]
    flat = [r[0] for r in table if r[4] == "flat"]
    unmeasured = [r[0] for r in table if r[4] == "missing" and r[1] is None and r[2] is None]
    out = [f"### SKRSI evaluation: **{rec['outcome']}** (`{rec['target_ref']}`)", ""]
    out += [f"**Warning:** {n}" for n in notes] + ([""] if notes else [])
    out += [f"`{rec['baseline']['id']}` (n={rec['baseline']['sample_size']}) against "
            f"`{rec['treatment']['id']}` (n={rec['treatment']['sample_size']}).",
            f"Evidence class `{rec['evidence_class']}`; " + (
                f"production code: {'yes' if i['runs_production_code'] else 'no'}; "
                f"sabotage tested: {'yes' if i['sabotage_tested'] else 'no'}; dead arms reported: "
                f"{'yes' if i['dead_arms_reported'] else 'no'}; baselines: {', '.join(i['baselines_named']) or 'none named'}."
                if any(i.values()) else "Instrument not described by the base side."),
            ""]
    if moved:
        out += ["| metric | baseline | treatment | delta | verdict |", "|---|---|---|---|---|"]
        out += [f"| {n} | {f(b)} | {f(t)} | {f(d)} | {s} |" for n, b, t, d, s in moved]
    else:
        out.append("No measured metric moved.")
    if flat:
        out.append(f"\nFlat: {names(flat)}.")
    if unmeasured:
        out.append(f"\nNot measured by this instrument: {names(unmeasured)}.")
    for sid, rule, metric in triggered:
        out.append(f"\nStop rule `{sid}` triggered by `{metric}`: proposed action `{rule['action']}`, owner {rule['owner']}.")
    if unchecked:
        out.append("\nStop rules not machine-checked: " + names(unchecked) + ".")
    if rec["missingness"]:
        out += ["", f"<details><summary>Missing or capped ({len(rec['missingness'])})</summary>", ""]
        out += [f"- {m}" for m in rec["missingness"]] + ["", "</details>"]
    out.append("\nAdvisory evidence only. It does not gate, approve or merge anything.")
    return "\n".join(out) + "\n"
