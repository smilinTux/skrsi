import ast
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from scripts.build_evaluation import Malformed, build, validate_v4

ROOT = Path(__file__).resolve().parents[1]
NOW = "2030-01-01T00:00:00Z"


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


SCHEMA = Draft202012Validator(load("schemas/evaluation-v4.schema.json"), format_checker=FormatChecker())


def accepted_by_schema(record):
    return not list(SCHEMA.iter_errors(record))


def accepted_by_stdlib(record):
    try:
        validate_v4(record)
        return True
    except Malformed:
        return False


class Inputs(unittest.TestCase):
    def setUp(self):
        self.target = load("examples/target-v2.json")
        self.base = load("examples/build-input/baseline.json")
        self.treat = load("examples/build-input/metrics.json")

    def run_build(self, evidence_class="live_labelled", **overrides):
        kw = dict(producer="collector-a", evaluator="reviewer-b", evidence_class=evidence_class, now=NOW)
        kw.update(overrides)
        return build(self.target, self.base, self.treat, **kw)


class ContractV4(unittest.TestCase):
    def record(self):
        r = load("examples/evaluation-v4.json")
        r.pop("$schema_file")
        return r

    def test_pass_is_impossible_for_synthetic_or_non_production_instrument(self):
        for mutate in (
            lambda r: r.update(evidence_class="synthetic"),
            lambda r: r["instrument"].update(runs_production_code=False),
        ):
            r = self.record()
            mutate(r)
            self.assertFalse(accepted_by_schema(r))
            self.assertFalse(accepted_by_stdlib(r))
            r["outcome"] = "inconclusive"
            r["missingness"] = ["cap"]
            self.assertTrue(accepted_by_schema(r))
            self.assertTrue(accepted_by_stdlib(r))

    def test_stdlib_validator_agrees_with_schema_on_a_mutation_corpus(self):
        base = self.record()
        mutations = {
            "drop evidence_class": lambda r: r.pop("evidence_class"),
            "drop instrument": lambda r: r.pop("instrument"),
            "bad class": lambda r: r.update(evidence_class="live"),
            "extra instrument key": lambda r: r["instrument"].update(extra=1),
            "flag not bool": lambda r: r["instrument"].update(sabotage_tested="yes"),
            "dup baselines": lambda r: r["instrument"].update(baselines_named=["a", "a"]),
            "empty baseline name": lambda r: r["instrument"].update(baselines_named=[""]),
            "v3 schema id": lambda r: r.update(schema="skrsi.evaluation.v3"),
            "same participants": lambda r: r.update(participants=["x", "x"]),
            "null metric on pass": lambda r: r["metrics"][0].update(treatment=None),
            "failed guardrail on pass": lambda r: r["guardrails"][0].update(passed=False),
            "missingness on pass": lambda r: r.update(missingness=["x"]),
            "unknown outcome": lambda r: r.update(outcome="great"),
            "short sha": lambda r: r.update(evidence_sha256="ab"),
            "extra key": lambda r: r.update(extra=1),
        }
        for name, mutate in mutations.items():
            r = copy.deepcopy(base)
            mutate(r)
            with self.subTest(name):
                self.assertEqual(accepted_by_schema(r), accepted_by_stdlib(r), name)
                self.assertFalse(accepted_by_schema(r))
        self.assertTrue(accepted_by_schema(base) and accepted_by_stdlib(base))

    def test_hand_crafted_pass_is_rejected_by_contract_and_validator_alike(self):
        def regress(r):
            r["metrics"][1].update(treatment=0.1, verdict="regressed")

        def lie(r):  # a verdict that does not match the numbers: only the validator can see it
            r["metrics"][1].update(treatment=0.1)

        both = {
            "sabotage not tested": lambda r: r["instrument"].update(sabotage_tested=False),
            "dead arms not reported": lambda r: r["instrument"].update(dead_arms_reported=False),
            "no baselines named": lambda r: r["instrument"].update(baselines_named=[]),
            "regressed verdict": regress,
            "nothing improved": lambda r: [m.update(verdict="flat") for m in r["metrics"]],
            "missing verdict on pass": lambda r: r["metrics"][0].update(verdict="missing"),
            "id with newline": lambda r: r.update(evaluation_id=r["evaluation_id"] + "\n"),
            "sha with newline": lambda r: r.update(evidence_sha256=r["evidence_sha256"][:63] + "\n"),
            "ref with newline": lambda r: r.update(target_ref="a@1\n"),
        }
        for name, mutate in both.items():
            r = self.record()
            mutate(r)
            with self.subTest(name):
                self.assertFalse(accepted_by_schema(r), "schema")
                self.assertFalse(accepted_by_stdlib(r), "validator")
        r = self.record()
        lie(r)  # schema cannot recompute arithmetic; the normative validator can
        self.assertFalse(accepted_by_stdlib(r))
        r = self.record()
        r["baseline"]["window_end"] = "2000-01-01T00:00:00Z"  # before window_start: ordering is validator-only
        self.assertFalse(accepted_by_stdlib(r))
        r = self.record()
        r["treatment"]["window_start"] = "2029-13-45T00:00:00Z"
        self.assertFalse(accepted_by_stdlib(r))

    def test_integral_float_sample_size_and_newlines_agree(self):
        for value, ok in ((1.0, True), (1.5, False), (True, False), (-1, False)):
            r = self.record()
            r["baseline"]["sample_size"] = value
            with self.subTest(sample_size=value):
                self.assertEqual((accepted_by_schema(r), accepted_by_stdlib(r)), (ok, ok))

    def test_v3_schema_is_untouched_and_v4_is_a_new_file(self):
        self.assertEqual(load("schemas/evaluation-v3.schema.json")["properties"]["schema"]["const"], "skrsi.evaluation.v3")
        self.assertNotIn("instrument", load("schemas/evaluation-v3.schema.json")["properties"])


class Builder(Inputs):
    def test_golden_examples_are_reproducible(self):
        for cls, name in (("live_labelled", "evaluation-v4.json"), ("synthetic", "evaluation-v4-synthetic-capped.json")):
            expected = load(f"examples/{name}")
            expected.pop("$schema_file")
            rec, _ = self.run_build(cls)
            self.assertEqual(rec, expected)
            self.assertTrue(accepted_by_schema(rec))

    def test_sabotage_a_real_regression_yields_regression(self):
        self.treat["metrics"]["first_pass_review_rate"]["value"] = 0.4
        rec, md = self.run_build()
        self.assertEqual(rec["outcome"], "regression")
        self.assertIn({"name": "stop_rule:quality-regression", "passed": False}, rec["guardrails"])
        self.assertIn("quality-regression", md)
        self.assertTrue(accepted_by_schema(rec))

    def test_plain_regression_without_a_stop_rule(self):
        self.treat["metrics"]["review_latency_ms"]["value"] = 150000
        rec, _ = self.run_build()
        self.assertEqual(rec["outcome"], "regression")

    def test_tolerance_absorbs_noise_but_not_more(self):
        self.treat["metrics"]["review_latency_ms"]["value"] = 120000 * 1.04
        self.treat["metrics"]["first_pass_review_rate"]["value"] = 0.9
        self.assertEqual(self.run_build()[0]["outcome"], "pass")
        self.treat["metrics"]["review_latency_ms"]["value"] = 120000 * 1.06
        self.assertEqual(self.run_build()[0]["outcome"], "regression")

    def test_bound_violation_is_a_regression_even_when_better_than_baseline(self):
        self.base["metrics"]["first_pass_review_rate"]["value"] = 0.1
        self.treat["metrics"]["first_pass_review_rate"]["value"] = 0.3
        self.assertEqual(self.run_build()[0]["outcome"], "regression")

    def test_dead_arm_is_missing_never_zero(self):
        self.treat["metrics"]["review_latency_ms"]["value"] = None
        rec, md = self.run_build()
        self.assertEqual(rec["outcome"], "inconclusive")
        self.assertIsNone(rec["metrics"][0]["treatment"])
        self.assertTrue(any("dead or unmeasured" in m for m in rec["missingness"]))
        self.assertIn("missing", md)

    def test_metric_absent_from_both_sides_is_explicit_missingness(self):
        del self.base["metrics"]["first_pass_review_rate"], self.treat["metrics"]["first_pass_review_rate"]
        rec, _ = self.run_build()
        self.assertEqual(rec["outcome"], "inconclusive")
        self.assertIn("first_pass_review_rate: absent from baseline, so no base rules apply", rec["missingness"])

    def test_caps_hold_the_outcome_below_pass(self):
        self.assertEqual(self.run_build("synthetic")[0]["outcome"], "inconclusive")
        for flag in ("runs_production_code", "sabotage_tested", "dead_arms_reported"):
            self.base["instrument"][flag] = False
            rec, _ = self.run_build()
            self.assertEqual(rec["outcome"], "inconclusive", flag)
            self.base["instrument"][flag] = True
        self.base["instrument"]["baselines_named"] = []
        self.assertEqual(self.run_build()[0]["outcome"], "inconclusive")

    def test_minimum_sample_size_in_the_target_caps_regression_and_pass(self):
        self.target["invariants"] = [*self.target["invariants"], "min_sample_size: 20 merged PRs per window"]
        self.treat["metrics"]["first_pass_review_rate"]["value"] = 0.4  # a real regression, but n=30 here
        self.assertEqual(self.run_build()[0]["outcome"], "regression")
        for side in (self.base, self.treat):
            side["cohort"]["sample_size"] = 13
        rec, md = self.run_build()
        self.assertEqual(rec["outcome"], "inconclusive")
        self.assertTrue(any("below the target minimum of 20" in m for m in rec["missingness"]))
        self.assertIn("bound violated", md)  # still shown, only not judged
        self.treat["metrics"]["first_pass_review_rate"]["value"] = 0.9
        self.assertEqual(self.run_build()[0]["outcome"], "inconclusive")  # pass is capped too
        self.assertFalse(any(g["name"].startswith("invariant:min_sample") for g in rec["guardrails"]))

    def test_a_pr_cannot_relax_its_own_bar(self):
        # the PR's metrics file widens tolerance, drops the bound and claims a better instrument
        self.treat["metrics"]["first_pass_review_rate"].update(value=0.4, tolerance=0.9)
        del self.treat["metrics"]["first_pass_review_rate"]["min"]
        del self.treat["metrics"]["first_pass_review_rate"]["stop_rule"]
        self.treat["instrument"] = {"runs_production_code": True, "baselines_named": ["x"], "sabotage_tested": True,
                                    "dead_arms_reported": True}
        self.base["instrument"]["sabotage_tested"] = False
        rec, _ = self.run_build()
        self.assertEqual(rec["outcome"], "regression")  # base rules apply: 0.4 is under the base floor of 0.5
        self.assertTrue(any("changes the rules" in m for m in rec["missingness"]))
        self.assertFalse(rec["instrument"]["sabotage_tested"])

    def test_flat_result_is_not_a_pass(self):
        for name in self.treat["metrics"]:
            self.treat["metrics"][name]["value"] = self.base["metrics"][name]["value"]
        self.assertEqual(self.run_build()[0]["outcome"], "inconclusive")

    def test_invariants(self):
        self.treat["invariants"]["no protected payload collection"] = False
        self.assertEqual(self.run_build()[0]["outcome"], "fail")
        del self.treat["invariants"]["no protected payload collection"]
        rec, _ = self.run_build()
        self.assertEqual(rec["outcome"], "inconclusive")
        self.assertTrue(any("not asserted" in m for m in rec["missingness"]))
        self.treat["invariants"]["made up"] = True
        with self.assertRaises(Malformed):
            self.run_build()

    def test_malformed_input_raises(self):
        with self.assertRaises(Malformed):
            self.run_build(producer="same", evaluator="same")
        with self.assertRaises(Malformed):
            self.run_build(evidence_class="vibes")
        self.treat["metrics"]["review_latency_ms"]["direction"] = "sideways"
        with self.assertRaises(Malformed):
            self.run_build()


class Cli(unittest.TestCase):
    def call(self, directory, *extra, metrics="examples/build-input/metrics.json", producer="a-producer"):
        out = Path(directory) / "eval.json"
        proc = subprocess.run(
            [sys.executable, "scripts/build_evaluation.py", "--target", "examples/target-v2.json",
             "--baseline", "examples/build-input/baseline.json", "--metrics", metrics, "--producer", producer,
             "--evaluator", "an-evaluator", "--evidence-class", "live_labelled", "--out", str(out),
             "--md", str(Path(directory) / "t.md"), *extra],
            cwd=ROOT, capture_output=True, text=True)
        return proc, out

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            proc, out = self.call(d)
            self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "outcome=pass"))
            self.assertTrue(accepted_by_schema(json.loads(out.read_text())))
            bad = Path(d) / "regressed.json"
            doc = load("examples/build-input/metrics.json")
            doc["metrics"]["first_pass_review_rate"]["value"] = 0.1
            bad.write_text(json.dumps(doc))
            proc, out = self.call(d, metrics=str(bad))
            self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "outcome=regression"))
            proc, _ = self.call(d, producer="an-evaluator")
            self.assertEqual(proc.returncode, 2)
            bad.write_text("{not json")
            proc, _ = self.call(d, metrics=str(bad))
            self.assertEqual(proc.returncode, 2)


class Hygiene(unittest.TestCase):
    def test_scripts_are_thin_and_stdlib_only(self):
        for path in (ROOT / "scripts").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertLessEqual(len(text.splitlines()), 300, path.name)
            for node in ast.walk(ast.parse(text)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
                for name in names:
                    siblings = {p.stem for p in path.parent.glob("*.py")}  # each is held to these same checks
                    self.assertIn(name.split(".")[0], {*sys.stdlib_module_names, *siblings}, f"{path.name}: {name}")


class AdoptionLearnings(Inputs):
    """Defects and blind spots found by the first real adopter, skmemory-retrieval."""

    def test_stop_rule_guardrail_is_not_passed_when_its_metric_is_unmeasured(self):
        self.treat["metrics"]["first_pass_review_rate"]["value"] = None  # a dead arm cannot clear a stop rule
        rec, md = self.run_build()
        self.assertNotIn("stop_rule:quality-regression", [g["name"] for g in rec["guardrails"]])
        self.assertTrue(any("quality-regression" in m and "not evaluated" in m for m in rec["missingness"]))
        self.assertIn("not machine-checked", md)
        self.assertIn("`quality-regression`", md)
        self.assertEqual(rec["outcome"], "inconclusive")

    def test_a_crashed_treatment_bench_does_not_clear_a_stop_rule(self):
        self.treat = {"metrics": {}}
        rec, _ = self.run_build()
        self.assertNotIn("stop_rule:quality-regression", [g["name"] for g in rec["guardrails"]])

    def test_stop_rule_is_not_passed_while_one_of_its_metrics_is_unmeasured(self):
        for side, value in ((self.base, 10.0), (self.treat, None)):
            side["metrics"]["review_latency_ms"].update(stop_rule="quality-regression", value=value)
        rec, _ = self.run_build()
        self.assertNotIn("stop_rule:quality-regression", [g["name"] for g in rec["guardrails"]])

    def test_a_triggered_stop_rule_still_fails_when_another_of_its_metrics_is_unmeasured(self):
        for side, value in ((self.base, 10.0), (self.treat, None)):
            side["metrics"]["review_latency_ms"].update(stop_rule="quality-regression", value=value)
        self.treat["metrics"]["first_pass_review_rate"]["value"] = 0.1
        rec, _ = self.run_build()
        self.assertIn({"name": "stop_rule:quality-regression", "passed": False}, rec["guardrails"])
        self.assertEqual(rec["outcome"], "regression")

    def test_same_numbers_at_different_commits_are_different_evidence(self):
        a, _ = self.run_build(baseline_id="base-aaaaaaa", treatment_id="head-bbbbbbb")
        b, _ = self.run_build(baseline_id="base-ccccccc", treatment_id="head-ddddddd")
        self.assertNotEqual(a["evidence_sha256"], b["evidence_sha256"])
        self.assertNotEqual(a["evaluation_id"], b["evaluation_id"])

    def test_commit_label_is_kept_alongside_the_cohort_id_of_the_file(self):
        rec, md = self.run_build(baseline_id="base-aaaaaaa", treatment_id="head-bbbbbbb")
        self.assertEqual(rec["baseline"]["id"], f"base-aaaaaaa/{self.base['cohort']['id']}")
        self.assertEqual(rec["treatment"]["id"], f"head-bbbbbbb/{self.treat['cohort']['id']}")
        self.assertIn("base-aaaaaaa", md)
        self.assertIn("head-bbbbbbb", md)

    def test_without_a_label_the_cohort_id_of_the_file_stands(self):
        rec, _ = self.run_build()
        self.assertEqual(rec["treatment"]["id"], self.treat["cohort"]["id"])
        del self.treat["cohort"]["id"]
        self.treat["cohort"] = {}
        self.assertEqual(self.run_build()[0]["treatment"]["id"], "treatment")

    def test_a_note_is_missingness_in_the_record_and_leads_the_comment(self):
        note = "bench failed on the PR head"
        rec, md = self.run_build(notes=[note])
        self.assertIn(note, rec["missingness"])
        self.assertNotEqual(rec["outcome"], "pass")
        self.assertLess(md.index(note), md.index("|---|"))

    def test_comment_leads_with_what_moved_and_folds_what_cannot_be_measured(self):
        self.target["metrics"] = [*self.target["metrics"], "live_only_metric"]
        rec, md = self.run_build()
        self.assertTrue(any("live_only_metric" in m for m in rec["missingness"]))  # the record keeps all of it
        self.assertIn("Not measured by this instrument: `live_only_metric`", md)
        self.assertNotIn("| live_only_metric |", md)
        self.assertLess(md.index("<details>"), md.index("live_only_metric: absent from baseline"))

    def test_flat_metrics_are_summarised_not_tabulated(self):
        for side in (self.base, self.treat):
            for m in side["metrics"].values():
                m["value"] = 1.0
        _, md = self.run_build()
        self.assertIn("No measured metric moved", md)
        self.assertNotIn("|---|", md)
        self.assertIn("`first_pass_review_rate`", md)

    def test_one_dead_side_stays_in_the_table(self):
        self.treat["metrics"]["first_pass_review_rate"]["value"] = None
        _, md = self.run_build()
        self.assertIn("| first_pass_review_rate |", md)

    def test_a_bound_already_broken_at_base_says_so_and_still_triggers(self):
        self.base["metrics"]["first_pass_review_rate"]["value"] = 0.2
        self.treat["metrics"]["first_pass_review_rate"]["value"] = 0.3
        rec, md = self.run_build()
        self.assertEqual(rec["outcome"], "regression")
        self.assertIn("bound violated (also at base)", md)
        self.assertIn("quality-regression", md)

    def test_margin_metric_makes_a_cross_arm_stop_rule_machine_checked(self):
        """`candidate - baseline arm` with min 0 is how a target checks one arm against another in the same run."""
        margin = {"unit": "ratio", "direction": "higher", "min": 0, "stop_rule": "quality-regression"}
        self.target["metrics"] = ["recall_margin_vs_bm25"]
        self.base["metrics"] = {"recall_margin_vs_bm25": {"value": 0.01, **margin}}
        self.treat["metrics"] = {"recall_margin_vs_bm25": {"value": -0.128, **margin}}
        rec, md = self.run_build()
        self.assertEqual(rec["outcome"], "regression")
        self.assertIn({"name": "stop_rule:quality-regression", "passed": False}, rec["guardrails"])
        self.assertIn("Stop rule `quality-regression` triggered by `recall_margin_vs_bm25`", md)

    def test_a_null_baseline_cannot_clear_an_unbounded_stop_rule(self):
        self.base["metrics"]["review_latency_ms"].update(stop_rule="quality-regression", value=None)
        self.treat["metrics"]["review_latency_ms"].update(stop_rule="quality-regression")
        del self.base["metrics"]["first_pass_review_rate"]["stop_rule"]
        del self.treat["metrics"]["first_pass_review_rate"]["stop_rule"]
        rec, _ = self.run_build()
        self.assertNotIn("stop_rule:quality-regression", [g["name"] for g in rec["guardrails"]])
        self.assertTrue(any("quality-regression" in m and "not evaluated" in m for m in rec["missingness"]))

    def test_a_null_baseline_can_still_break_a_bound(self):
        self.base["metrics"]["first_pass_review_rate"]["value"] = None
        self.treat["metrics"]["first_pass_review_rate"]["value"] = 0.1
        rec, _ = self.run_build()
        self.assertIn({"name": "stop_rule:quality-regression", "passed": False}, rec["guardrails"])

    def test_notes_change_the_digest(self):
        plain, _ = self.run_build()
        noted, _ = self.run_build(notes=["the bench crashed"])
        self.assertNotEqual(plain["evidence_sha256"], noted["evidence_sha256"])

    def test_a_metric_only_the_head_measured_is_shown_not_called_unmeasured(self):
        self.target["metrics"] = [*self.target["metrics"], "new_metric"]
        self.treat["metrics"]["new_metric"] = {"value": 0.9, "unit": "ratio", "direction": "higher"}
        rec, md = self.run_build()
        row = next(m for m in rec["metrics"] if m["name"] == "new_metric")
        self.assertEqual((row["treatment"], row["verdict"]), (0.9, "missing"))
        self.assertTrue(accepted_by_schema(rec))
        self.assertIn("| new_metric | missing | 0.9 |", md)
        self.assertNotIn("Not measured by this instrument: `new_metric`", md)

    def test_an_undescribed_instrument_is_not_reported_as_a_bad_one(self):
        self.base = {"metrics": {}}  # the base bench failed: nothing is known about the instrument
        _, md = self.run_build()
        self.assertIn("Instrument not described by the base side", md)
        self.assertNotIn("production code: no", md)

    def test_a_non_string_cohort_id_is_malformed(self):
        for bad in (0, False, 7):
            self.treat["cohort"]["id"] = bad
            with self.assertRaises(Malformed):
                self.run_build()

    def test_note_flag_reaches_the_record(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for name, doc in (("t", self.target), ("b", self.base), ("m", self.treat)):
                paths[name] = Path(directory) / f"{name}.json"
                paths[name].write_text(json.dumps(doc), encoding="utf-8")
            out = Path(directory) / "e.json"
            subprocess.run([sys.executable, str(ROOT / "scripts" / "build_evaluation.py"),
                            "--target", str(paths["t"]), "--baseline", str(paths["b"]), "--metrics", str(paths["m"]),
                            "--producer", "a-producer", "--evaluator", "an-evaluator",
                            "--evidence-class", "live_labelled", "--out", str(out),
                            "--note", "first note", "--note", "second note"], check=True, capture_output=True)
            self.assertLessEqual({"first note", "second note"},
                                 set(json.loads(out.read_text(encoding="utf-8"))["missingness"]))


if __name__ == "__main__":
    unittest.main()
