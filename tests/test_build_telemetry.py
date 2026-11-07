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


def runs(*items):
    return [{"headSha": sha, "conclusion": c, "event": e, "createdAt": "2026-11-01T01:00:00Z"} for sha, c, e in items]


class Telemetry(unittest.TestCase):
    def test_first_pass_two_rounds_ci_failures_revert_and_defects(self):
        prs = [pr(1, "aaaaaaaa"), pr(2, "bbbbbbbb", title="feat: second", labels=["defect:privacy", "wontfix"]),
               pr(3, "cccccccc", commits=["2026-11-01T01:30:00Z"])]
        events = (reviewed("aaaaaaaa", "r1") + reviewed("bbbbbbbb", "r2", heads=("a", "b"))
                  + reviewed("cccccccc", "r3"))
        events.append(ev("r3", "x", "y", action="add_label") | {"label": "defect:dead-arm"})
        run_map = {1: [], 2: runs(("s1", "failure", "pull_request"), ("s1", "failure", "pull_request"),
                                   ("s2", "failure", "pull_request"), ("s3", "success", "pull_request"),
                                   ("s4", "failure", "push")), 3: []}
        res = bt.compute(prs, run_map, [{"number": 9, "title": 'Revert "feat: second"'}], events)
        rows = {r["pr"]: r for r in res["cards"]}
        self.assertEqual(rows[1]["review_rounds_to_pass"], 1)
        self.assertTrue(rows[1]["first_pass"])
        self.assertEqual((rows[2]["review_rounds_to_pass"], rows[2]["first_pass"]), (2, False))
        self.assertEqual(rows[2]["ci_failures_before_merge"], 2)  # distinct failing heads, push events ignored
        self.assertTrue(rows[2]["reverted"] and not rows[1]["reverted"])
        self.assertEqual(rows[2]["defect_labels"], ["defect:privacy"])  # off-allowlist labels never counted
        self.assertEqual(rows[3]["post_review_commits"], 1)  # a commit after the first review event is a failed round
        self.assertFalse(rows[3]["first_pass"])
        agg = res["repo"]
        self.assertEqual((agg["merged_prs"], agg["reviewed_prs"], agg["reverted_merges"]), (3, 3, 1))
        self.assertEqual(agg["first_pass_review_rate"], 0.333)
        self.assertEqual(agg["defect_class_counts"], {"defect:privacy": 1, "defect:dead-arm": 1})

    def test_unrecorded_review_is_missing_not_first_pass(self):
        res = bt.compute([pr(1, "aaaaaaaa"), pr(2, None)], {}, [], [])
        self.assertTrue(all(r["first_pass"] is None and r["review_rounds_to_pass"] is None for r in res["cards"]))
        agg = res["repo"]
        self.assertIsNone(agg["first_pass_review_rate"])
        self.assertEqual((agg["reviewed_prs"], agg["review_evidence_coverage_ratio"]), (0, 0.0))
        self.assertIsNone(agg["defect_labels_total"])  # no labels anywhere: missing, not zero
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
        res = bt.compute([pr(1, "aaaaaaaa")], {1: []}, [], reviewed("aaaaaaaa", "r1"))
        mf = bt.metrics_file(res, "w1")
        target = json.loads((ROOT / "targets/sk-build-quality.json").read_text())
        rec, _ = build(target, mf, mf, producer="authors", evaluator="skrsi-evaluate",
                       evidence_class="live_labelled", now="2026-11-07T00:00:00Z")
        validate_v4(rec)
        self.assertEqual(rec["outcome"], "inconclusive")  # identical windows: nothing improved, defects missing
        self.assertIn("defect_labels_total: baseline null (dead or unmeasured arm)", rec["missingness"])

    def test_range_parser(self):
        self.assertEqual(bt.parse_range("92-94,97"), [92, 93, 94, 97])

    def test_target_matches_metric_names_and_schema(self):
        from jsonschema import Draft202012Validator, FormatChecker
        target = json.loads((ROOT / "targets/sk-build-quality.json").read_text())
        schema = json.loads((ROOT / "schemas/target-v2.schema.json").read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(target)
        mf = bt.metrics_file(bt.compute([], {}, [], []), "w")
        self.assertEqual(set(target["metrics"]), set(mf["metrics"]))
        self.assertEqual(set(mf["invariants"]), set(target["invariants"]))


if __name__ == "__main__":
    unittest.main()
