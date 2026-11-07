#!/usr/bin/env python3
"""Build a validated skrsi.evaluation.v4 record from a target, a baseline and metrics.

Stdlib only. Exit 0 for every outcome (a regression is a normal result), exit 2 only
when its own input is malformed. It records evidence and proposes; it never approves.

Baseline and metrics files share one shape. Rules and the instrument description are read from the
BASE side only, so a PR cannot relax its own bar; the metrics side supplies values and invariants:
  {"cohort": {...}?, "instrument": {...}?, "invariants": {"<target invariant>": bool}?,
   "metrics": {"<name>": {"value": number|null, "unit": str, "direction": "higher"|"lower",
                          "tolerance": rel>=0?, "min": n?, "max": n?, "stop_rule": id?}}}
A null value is a dead or unmeasured arm: it becomes missingness, never zero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone

SCHEMA = "skrsi.evaluation.v4"
CLASSES = ("synthetic", "live_self_retrieval", "live_labelled")
OUTCOMES = ("pass", "fail", "blocked", "inconclusive", "regression")
INSTRUMENT_KEYS = ("runs_production_code", "baselines_named", "sabotage_tested", "dead_arms_reported")
COHORT_KEYS = ("id", "sample_size", "window_start", "window_end")
MIN_SAMPLE = re.compile(r"^min_sample_size: (\d+)\b")  # a target invariant of this exact form states the floor
RULE_KEYS = ("unit", "direction", "tolerance", "min", "max", "stop_rule")
# fullmatch everywhere: `$` would accept a trailing newline
ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,127}")
REF = re.compile(r"[a-z0-9._-]+@[1-9][0-9]*")
TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)")
SHA = re.compile(r"[0-9a-f]{64}")


class Malformed(ValueError):
    """The input or the produced record is not valid."""


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise Malformed(msg)

def _str(v, lo=1, hi=128) -> bool:
    return isinstance(v, str) and lo <= len(v) <= hi


def _int(v) -> bool:  # JSON Schema counts 1.0 as an integer, so the mirror must too
    return not isinstance(v, bool) and (isinstance(v, int) or (isinstance(v, float) and v.is_integer()))


def _when(v):
    _need(isinstance(v, str) and TS.fullmatch(v), "bad timestamp")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        raise Malformed(f"bad timestamp {v!r}") from None


def _cohort_ok(c, where: str) -> None:
    _need(isinstance(c, dict) and set(c) == set(COHORT_KEYS), f"{where}: cohort keys must be {COHORT_KEYS}")
    _need(isinstance(c["id"], str) and _int(c["sample_size"]) and c["sample_size"] >= 0, f"{where}: bad id or sample_size")
    _need(_when(c["window_start"]) <= _when(c["window_end"]), f"{where}: window_end is before window_start")


def _verdict(b, t, spec) -> tuple[str, float | None]:
    """improved, flat, regressed or missing, from the numbers, direction, tolerance and bounds."""
    if t is not None and (t < spec.get("min", t) or t > spec.get("max", t)):
        return "regressed", None if b is None else t - b
    if b is None or t is None:
        return "missing", None
    delta = t - b
    worse = -delta if spec["direction"] == "higher" else delta
    margin = spec.get("tolerance", 0) * abs(b)
    return ("regressed" if worse > margin else "improved" if -worse > margin else "flat"), delta


def _instrument_ok(i) -> None:
    _need(isinstance(i, dict) and set(i) == set(INSTRUMENT_KEYS), f"instrument keys must be {INSTRUMENT_KEYS}")
    _need(all(isinstance(i[k], bool) for k in ("runs_production_code", "sabotage_tested", "dead_arms_reported")),
          "instrument flags must be booleans")
    b = i["baselines_named"]
    _need(isinstance(b, list) and len(set(b)) == len(b) and all(_str(x) for x in b), "baselines_named: unique strings")


def validate_v4(r: dict) -> dict:
    """Stdlib mirror of schemas/evaluation-v4.schema.json, including the pass cap. Returns r."""
    _need(isinstance(r, dict), "record must be an object")
    keys = {"schema", "evaluation_id", "target_ref", "participants", "baseline", "treatment", "metrics",
            "guardrails", "missingness", "evidence_class", "instrument", "outcome", "evidence_sha256"}
    _need(set(r) == keys, f"record keys must be exactly {sorted(keys)}")
    _need(r["schema"] == SCHEMA, "schema const")
    _need(isinstance(r["evaluation_id"], str) and ID.fullmatch(r["evaluation_id"]), "evaluation_id pattern")
    _need(isinstance(r["target_ref"], str) and REF.fullmatch(r["target_ref"]), "target_ref pattern")
    p = r["participants"]
    _need(isinstance(p, list) and len(p) == 2 and all(_str(x) for x in p) and p[0] != p[1],
          "participants must be [producer, evaluator], distinct")
    _cohort_ok(r["baseline"], "baseline")
    _cohort_ok(r["treatment"], "treatment")
    m = r["metrics"]
    _need(isinstance(m, list) and m, "metrics must be non-empty")
    for x in m:
        req = {"name", "unit", "baseline", "treatment", "direction", "verdict"}
        _need(isinstance(x, dict) and req <= set(x) <= req | {"tolerance", "min", "max"}, "metric keys")
        _need(isinstance(x["name"], str) and isinstance(x["unit"], str), "metric name/unit")
        _need(all(v is None or _num(v) for v in (x["baseline"], x["treatment"])), "metric values: number or null")
        _need(all(_num(x[k]) for k in ("tolerance", "min", "max") if k in x) and x.get("tolerance", 0) >= 0,
              "metric rule fields")
        _need(x["direction"] in ("higher", "lower", "unknown") and x["verdict"] in
              ("improved", "flat", "regressed", "missing"), "metric direction or verdict")
        want = "missing" if x["direction"] == "unknown" else _verdict(x["baseline"], x["treatment"], x)[0]
        _need(x["verdict"] == want, f"metric {x['name']}: verdict {x['verdict']!r} does not match the numbers ({want!r})")
    g = r["guardrails"]
    _need(isinstance(g, list) and g, "guardrails must be non-empty")
    _need(all(isinstance(x, dict) and set(x) == {"name", "passed"} and isinstance(x["name"], str)
              and isinstance(x["passed"], bool) for x in g), "guardrail shape")
    _need(isinstance(r["missingness"], list) and all(_str(x, 1, 10**6) for x in r["missingness"]), "missingness")
    _need(r["evidence_class"] in CLASSES, "evidence_class")
    _instrument_ok(r["instrument"])
    _need(r["outcome"] in OUTCOMES, "outcome")
    _need(isinstance(r["evidence_sha256"], str) and SHA.fullmatch(r["evidence_sha256"]), "evidence_sha256")
    if r["outcome"] == "pass":
        _need(r["evidence_class"] != "synthetic", "pass is impossible with evidence_class synthetic")
        _need(r["instrument"]["runs_production_code"], "pass is impossible when runs_production_code is false")
        _need(all(x["passed"] for x in g) and not r["missingness"], "pass needs passed guardrails, no missingness")
        _need(all(x["baseline"] is not None and x["treatment"] is not None for x in m), "pass needs numeric metrics")
        i = r["instrument"]
        _need(i["sabotage_tested"] and i["dead_arms_reported"] and i["baselines_named"],
              "pass needs sabotage_tested, dead_arms_reported and at least one named baseline")
        _need(all(x["verdict"] in ("improved", "flat") for x in m) and any(x["verdict"] == "improved" for x in m),
              "pass needs no regressed metric and at least one improved")
    return r


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _side(doc: dict, where: str) -> dict:
    _need(isinstance(doc, dict) and isinstance(doc.get("metrics"), dict), f"{where}: needs a metrics object")
    extra = set(doc) - {"cohort", "instrument", "invariants", "metrics"}
    _need(not extra, f"{where}: unknown keys {sorted(extra)}")
    allowed = {"value", "unit", "direction", "tolerance", "min", "max", "stop_rule"}
    for name, s in doc["metrics"].items():
        _need(isinstance(s, dict) and set(s) <= allowed and {"value", "unit", "direction"} <= set(s),
              f"{where}.{name}: needs value, unit, direction; allowed {sorted(allowed)}")
        _need(s["value"] is None or _num(s["value"]), f"{where}.{name}: value must be a number or null")
        _need(isinstance(s["unit"], str) and s["direction"] in ("higher", "lower"), f"{where}.{name}: unit/direction")
        _need(all(_num(s[k]) for k in ("tolerance", "min", "max") if k in s), f"{where}.{name}: numeric rule fields")
        _need(s.get("tolerance", 0) >= 0, f"{where}.{name}: tolerance must be >= 0")
    inv = doc.get("invariants", {})
    _need(isinstance(inv, dict) and all(isinstance(v, bool) for v in inv.values()), f"{where}: invariants map to bools")
    return doc


def build(target, baseline, metrics, *, producer, evaluator, evidence_class, now,
          baseline_id="baseline", treatment_id="treatment") -> tuple[dict, str]:
    _need(isinstance(target, dict) and target.get("schema") == "skrsi.target.v2", "target must be skrsi.target.v2")
    _need(ID.fullmatch(str(target.get("target_id", ""))) and isinstance(target.get("revision"), int), "target id/revision")
    _need(_str(producer) and _str(evaluator) and producer != evaluator, "participants must be two distinct non-empty names")
    _need(evidence_class in CLASSES, f"evidence_class must be one of {CLASSES}")
    base, treat = _side(baseline, "baseline"), _side(metrics, "metrics")
    rules = {r["id"]: r for r in target["stop_rules"]}
    inst = baseline.get("instrument", {k: ([] if k == "baselines_named" else False) for k in INSTRUMENT_KEYS})
    _instrument_ok(inst)
    default = {"id": "", "sample_size": 0, "window_start": now, "window_end": now}
    cohorts = [{**default, "id": cid, **side.get("cohort", {})} for cid, side in
               ((baseline_id, base), (treatment_id, treat))]
    rows, table, missing, triggered, bound_rules, improved = [], [], [], {}, set(), False
    for name in target["metrics"]:
        b, t = base["metrics"].get(name), treat["metrics"].get(name)
        if b is None:  # rules come from the BASE side only: a PR cannot relax its own bar
            rows.append({"name": name, "unit": "unknown", "baseline": None, "treatment": None,
                         "direction": "unknown", "verdict": "missing"})
            table.append((name, None, None, None, "missing"))
            missing.append(f"{name}: absent from baseline, so no base rules apply")
            continue
        spec = b
        if t is not None and any(t.get(k) != b.get(k) for k in RULE_KEYS):
            missing.append(f"{name}: the PR's metrics file changes the rules; base rules applied")
        bv, tv = b["value"], (t or {}).get("value")
        verdict, delta = _verdict(bv, tv, spec)
        out_of_bounds = tv is not None and (tv < spec.get("min", tv) or tv > spec.get("max", tv))
        status = "bound violated" if out_of_bounds else verdict
        bounded = "min" in spec or "max" in spec
        sid = spec.get("stop_rule")
        if sid:
            _need(sid in rules, f"{name}: stop_rule {sid!r} is not in the target")
            bound_rules.add(sid)
            if out_of_bounds if bounded else verdict == "regressed":  # a declared bound is the rule's condition
                triggered[sid] = name
        for side, v in (("baseline", bv), ("treatment", tv)):
            if v is None:
                missing.append(f"{name}: {side} " + ("absent" if (b if side == "baseline" else t) is None else "null (dead or unmeasured arm)"))
        improved |= status == "improved"
        rows.append({"name": name, "unit": spec["unit"], "baseline": bv, "treatment": tv, "direction": spec["direction"],
                     "verdict": verdict, **{k: spec[k] for k in ("tolerance", "min", "max") if k in spec}})
        table.append((name, bv, tv, delta, status))
    regressed = any(s in ("regressed", "bound violated") for *_, s in table)
    asserted = treat.get("invariants", {})
    unknown = set(asserted) - set(target["invariants"])
    _need(not unknown, f"invariants not in target: {sorted(unknown)}")
    guards = [{"name": "independent_evaluator", "passed": True}]
    min_n = max([int(m.group(1)) for inv in target["invariants"] if (m := MIN_SAMPLE.match(inv))] or [0])
    for inv in target["invariants"]:
        if MIN_SAMPLE.match(inv):
            continue
        if inv in asserted:
            guards.append({"name": f"invariant:{inv}", "passed": asserted[inv]})
        else:
            missing.append(f"invariant not asserted by the bench: {inv}")
    for sid in sorted(bound_rules):
        guards.append({"name": f"stop_rule:{sid}", "passed": sid not in triggered})
    caps = [(evidence_class == "synthetic", "cap: evidence_class synthetic cannot support pass"),
            (not inst["runs_production_code"], "cap: instrument does not run production code"),
            (not inst["sabotage_tested"], "cap: instrument has no demonstrated sabotage failure"),
            (not inst["dead_arms_reported"], "cap: instrument does not report dead arms as dead"),
            (not inst["baselines_named"], "cap: no named baselines")]
    missing += [msg for hit, msg in caps if hit]
    small = [c["id"] for c in cohorts if c["sample_size"] < min_n]
    if small:
        missing.append(f"cap: sample size below the target minimum of {min_n} in {', '.join(small)}")
    if small:  # a noisy window never supports regression, fail or pass
        outcome = "inconclusive"
    elif regressed:
        outcome = "regression"
    elif not all(g["passed"] for g in guards):
        outcome = "fail"
    elif missing:
        outcome = "inconclusive"
    elif not improved:
        outcome, missing = "inconclusive", ["no metric improved beyond its tolerance"]
    else:
        outcome = "pass"
    digest = hashlib.sha256(json.dumps([target, baseline, metrics, evidence_class, [producer, evaluator]],
                                       sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rec = {"schema": SCHEMA, "evaluation_id": f"eval-{target['target_id']}-{digest[:12]}"[:128],
           "target_ref": f"{target['target_id']}@{target['revision']}", "participants": [producer, evaluator],
           "baseline": cohorts[0], "treatment": cohorts[1], "metrics": rows, "guardrails": guards,
           "missingness": missing, "evidence_class": evidence_class, "instrument": inst, "outcome": outcome,
           "evidence_sha256": digest}
    validate_v4(rec)
    return rec, _markdown(rec, table, [(s, rules[s], triggered[s]) for s in sorted(triggered)],
                          [s for s in rules if s not in bound_rules])


def _markdown(rec, table, triggered, unchecked) -> str:
    f = lambda v: "missing" if v is None else f"{v:.6g}"
    i = rec["instrument"]
    out = [f"### SKRSI evaluation: **{rec['outcome']}** (`{rec['target_ref']}`)", "",
           f"Evidence class `{rec['evidence_class']}`; production code: {'yes' if i['runs_production_code'] else 'no'}; "
           f"sabotage tested: {'yes' if i['sabotage_tested'] else 'no'}; dead arms reported: "
           f"{'yes' if i['dead_arms_reported'] else 'no'}; baselines: {', '.join(i['baselines_named']) or 'none named'}.",
           "", "| metric | baseline | treatment | delta | verdict |", "|---|---|---|---|---|"]
    out += [f"| {n} | {f(b)} | {f(t)} | {f(d)} | {s} |" for n, b, t, d, s in table]
    for sid, rule, metric in triggered:
        out.append(f"\nStop rule `{sid}` triggered by `{metric}`: proposed action `{rule['action']}`, owner {rule['owner']}.")
    if unchecked:
        out.append("\nStop rules not machine-checked: " + ", ".join(f"`{s}`" for s in unchecked) + ".")
    if rec["missingness"]:
        out += ["", "Missing or capped:"] + [f"- {m}" for m in rec["missingness"]]
    out.append("\nAdvisory evidence only. It does not gate, approve or merge anything.")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for flag in ("target", "baseline", "metrics", "producer", "evaluator", "evidence-class", "out", "md"):
        a.add_argument("--" + flag, required=flag != "md")
    a.add_argument("--now", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    a.add_argument("--baseline-id", default="baseline")
    a.add_argument("--treatment-id", default="treatment")
    args = a.parse_args(argv)
    try:
        rec, md = build(_load(args.target), _load(args.baseline), _load(args.metrics), producer=args.producer,
                        evaluator=args.evaluator, evidence_class=args.evidence_class, now=args.now,
                        baseline_id=args.baseline_id, treatment_id=args.treatment_id)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2, sort_keys=True)
            fh.write("\n")
        if args.md:
            with open(args.md, "w", encoding="utf-8") as fh:
                fh.write(md)
    except (Malformed, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"build_evaluation: malformed input: {exc}", file=sys.stderr)
        return 2
    print(f"outcome={rec['outcome']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
