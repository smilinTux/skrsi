import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts import build_telemetry as bt
from scripts.build_evaluation import build, validate_v4

ROOT = Path(__file__).resolve().parents[1]


def pr(n, card, opened="2026-11-01T00:00:00Z", merged="2026-11-01T02:00:00Z", title=None, labels=(), commits=()):
    return {"number": n, "title": title or f"feat: change {n}", "state": "MERGED" if merged else "OPEN",
            "createdAt": opened, "mergedAt": merged, "headRefName": f"card/{card}" if card else "fix/x",
            "headRefOid": "h" * 40, "labels": [{"name": x} for x in labels],
            "commits": [{"committedDate": c} for c in commits]}


def ev(card, key, value, t="2026-11-01T01:00:00Z", action="link"):
    return {"card_id": card, "action": action, "ts": t, "link_key": key, "link_value": value, "label": value}


def reviewed(card, review, heads=("a",), at="2026-11-01T01:00:00Z"):
    out = [ev(review, "link_source_card", card, at)]
    out += [ev(review, "link_head_revision", h, at) for h in heads]
    return out + [ev(review, "verdict", "PASS", at)]


def rnd(card, pr_no, head, verdict, classes="", t="2026-11-01T01:00:00Z"):
    return ev(card, "review_round", f"{pr_no} {head} {verdict} {classes}".strip(), t)


def runs(*items):
    return [{"headSha": sha, "conclusion": c, "event": e, "createdAt": "2026-11-01T01:00:00Z"} for sha, c, e in items]


class Telemetry(unittest.TestCase):
    def test_recorded_fail_then_pass_is_second_round_and_single_pass_is_first(self):
        prs = [pr(1, "aaaaaaaa"), pr(2, "bbbbbbbb", title="feat: second", labels=["defect:privacy", "wontfix"])]
        events = (reviewed("aaaaaaaa", "r1") + reviewed("bbbbbbbb", "r2", heads=("a", "b"))
                  + [rnd("aaaaaaaa", 1, "h" * 40, "PASS"),
                     rnd("bbbbbbbb", 2, "a" * 8, "FAIL", "privacy,bogus", "2026-11-01T00:30:00Z"),
                     rnd("bbbbbbbb", 2, "h" * 40, "PASS", "", "2026-11-01T01:30:00Z")])
        run_map = {1: [], 2: runs(("s1", "failure", "pull_request"), ("s1", "failure", "pull_request"),
                                   ("s2", "failure", "pull_request"), ("s3", "success", "pull_request"),
                                   ("s4", "failure", "push"))}
        res = bt.compute(prs, run_map, [{"number": 9, "title": 'Revert "feat: second"'}], events)
        rows = {r["pr"]: r for r in res["cards"]}
        self.assertEqual((rows[1]["review_rounds_to_pass"], rows[1]["first_pass"]), (1, True))
        self.assertEqual((rows[2]["review_rounds_to_pass"], rows[2]["first_pass"]), (2, False))
        self.assertEqual(rows[2]["ci_failures_before_merge"], 2)  # distinct failing heads, push events ignored
        self.assertTrue(rows[2]["reverted"] and not rows[1]["reverted"])
        self.assertEqual(rows[2]["defect_classes"], ["privacy"])  # off-allowlist classes never counted
        agg = res["repo"]
        self.assertEqual((agg["merged_prs"], agg["round_covered_prs"], agg["covered_rounds"]), (2, 2, 3))
        self.assertEqual((agg["first_pass_review_rate"], agg["review_rounds_to_pass"]), (0.5, 1.5))
        self.assertEqual(agg["defect_class_counts"], {"privacy": 1})

    def test_no_recorded_round_is_missing_even_when_one_head_looks_clean(self):
        prs = [pr(1, "aaaaaaaa"), pr(2, "bbbbbbbb", commits=["2026-11-01T01:30:00Z"])]
        events = reviewed("aaaaaaaa", "r1") + reviewed("bbbbbbbb", "r2")  # a PASS verdict, but no review_round
        res = bt.compute(prs, {}, [], events)
        for row in res["cards"]:
            self.assertIsNone(row["first_pass"])
            self.assertIsNone(row["review_rounds_to_pass"])
        agg = res["repo"]
        self.assertIsNone(agg["first_pass_review_rate"])
        self.assertIsNone(agg["review_rounds_to_pass"])
        self.assertEqual((agg["round_covered_prs"], agg["review_evidence_coverage_ratio"]), (0, 0.0))
        self.assertEqual(agg["proxy_rework_rate"], 0.5)  # labelled proxy: PR 2 has a commit after first review
        self.assertTrue(res["cards"][1]["proxy_reworked"] and not res["cards"][0]["proxy_reworked"])
        self.assertIsNone(agg["defect_labels_total"])

    def test_incomplete_records_do_not_count(self):
        prs = [pr(1, "aaaaaaaa"), pr(2, "bbbbbbbb"), pr(3, "cccccccc")]
        events = (reviewed("aaaaaaaa", "r1", heads=("a", "b"))
                  + [rnd("aaaaaaaa", 1, "h" * 40, "PASS"),            # two reviewed heads, one record
                     rnd("bbbbbbbb", 2, "h" * 40, "FAIL"),            # last record is FAIL
                     rnd("cccccccc", 3, "z" * 40, "PASS")])           # PASS on a different head than merged
        res = bt.compute(prs, {}, [], events)
        self.assertTrue(all(r["review_rounds_to_pass"] is None for r in res["cards"]))

    def test_unrecorded_review_is_missing_not_first_pass(self):
        res = bt.compute([pr(1, "aaaaaaaa"), pr(2, None)], {}, [], [])
        self.assertTrue(all(r["first_pass"] is None and r["review_rounds_to_pass"] is None for r in res["cards"]))
        agg = res["repo"]
        self.assertIsNone(agg["first_pass_review_rate"])
        self.assertEqual((agg["pass_verdict_prs"], agg["review_evidence_coverage_ratio"]), (0, 0.0))
        self.assertIsNone(agg["defect_labels_total"])  # nothing recorded: missing, not zero
        self.assertTrue(any("missing, not zero" in m for m in res["missing"]))

    def test_open_pr_has_no_merge_metrics(self):
        res = bt.compute([pr(1, "aaaaaaaa", merged=None)], {}, [], [])
        row = res["cards"][0]
        self.assertFalse(row["merged"])
        self.assertIsNone(row["open_to_merge_hours"])
        self.assertEqual(res["repo"]["merged_prs"], 0)
        self.assertIsNone(res["repo"]["pr_open_to_merge_hours"])

    def test_fetch_requests_no_bodies_and_skips_unreadable_numbers(self):
        calls = []

        def fake(cmd, **_):
            calls.append(cmd)
            if cmd[2:4] == ["view", "5"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="not a pull request")
            if cmd[2:4] == ["view", "6"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps(pr(6, "aaaaaaaa")), stderr="")
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")

        prs, run_map, reverts = bt.fetch("o/r", [5, 6], run=fake)
        self.assertEqual([p["number"] for p in prs], [6])
        flat = " ".join(" ".join(c) for c in calls)
        for forbidden in ("body", "reviews", "comments", "diff"):
            self.assertNotIn(forbidden, flat)

    def test_metrics_file_feeds_the_evaluator_and_stays_inconclusive_without_a_baseline_gap(self):
        res = bt.compute([pr(1, "aaaaaaaa")], {1: []}, [], reviewed("aaaaaaaa", "r1") + [rnd("aaaaaaaa", 1, "h" * 40, "PASS")])
        mf = bt.metrics_file(res, "w1")
        target = json.loads((ROOT / "targets/sk-build-quality.json").read_text())
        rec, _ = build(target, mf, mf, producer="authors", evaluator="skrsi-evaluate",
                       evidence_class="live_labelled", now="2026-11-07T00:00:00Z")
        validate_v4(rec)
        self.assertEqual(rec["outcome"], "inconclusive")
        self.assertTrue(any("below the target minimum" in m for m in rec["missingness"]))  # 1 merged PR < 20

    def test_range_parser(self):
        self.assertEqual(bt.parse_range("92-94,97"), [92, 93, 94, 97])

    def test_target_matches_metric_names_and_schema(self):
        from jsonschema import Draft202012Validator, FormatChecker
        target = json.loads((ROOT / "targets/sk-build-quality.json").read_text())
        schema = json.loads((ROOT / "schemas/target-v2.schema.json").read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(target)
        mf = bt.metrics_file(bt.compute([], {}, [], []), "w")
        self.assertEqual(set(target["metrics"]), set(mf["metrics"]))
        self.assertEqual(set(mf["invariants"]), {i for i in target["invariants"] if not i.startswith("min_sample_size")})


if __name__ == "__main__":
    unittest.main()
