"""resources_metrics.py: the flattener for sk-node-resource-efficiency.

Every number below is hand-computed from the fixture. Names are synthetic.
"""

import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from scripts import resources_metrics as rm
from scripts.build_evaluation import build, validate_v4
from scripts.instance_text import text_findings

ROOT = Path(__file__).resolve().parents[1]
TARGET = json.loads((ROOT / "targets/sk-node-resource-efficiency.json").read_text())

CLUSTER = """schema: skrsi.cluster.v1
config_schema: 1
cluster: example
nodes:
  - name: node-a
    role: primary
    readers: [cron, resources]
  - name: node-b
    role: laptop
    readers: [resources]
  - name: node-c
    role: gpu-worker
    readers: []
services: []
canon:
  repo: example-org/example-canon
  namespace: ops
"""


def unit(cpu, rss, kills=0, restarts=0, basis="delta"):
    return {"status": "ok", "cpu_core_pct": cpu, "cpu_basis": basis, "cpu_window_s": 86400, "rss_mb": rss,
            "mem_peak_mb": rss * 2, "kills": kills, "restarts": restarts}


def evidence(units, disk=50.0, sync=None, status="ok"):
    res = {"units_declared": len(units), "units": units, "disk": {"mounts": 1, "free_pct_min": disk},
           "sync": sync or {}}
    return {"kind": "skrsi.evidence.daily", "readers": {"resources": {"status": status, "result": res}}}


class ResourcesMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "cluster.yaml").write_text(CLUSTER)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, node, day, doc):
        path = self.root / "evidence" / node / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))

    def fixture(self, b_kills=0):
        # node-a: two units on two days; svc-y missing on day 2.
        self.put("node-a", "2026-01-01", evidence({"svc-x": unit(60, 50), "svc-y": unit(10, 1000)}, disk=4.0,
                                                  sync={"f1": {"status": "ok", "files": 1000, "need_files": 5}}))
        self.put("node-a", "2026-01-02", evidence({"svc-x": unit(40, 70), "svc-y": {"status": "missing"}}, disk=3.0,
                                                  sync={"f1": {"status": "ok", "files": 1200, "need_files": 0}}))
        # node-b: one unit, one day only (day 2 has no evidence file).
        self.put("node-b", "2026-01-01", evidence({"syncd": unit(90, 900, kills=b_kills, restarts=1)}, disk=20.0,
                                                  sync={"f1": {"status": "ok", "files": 1100, "need_files": 300}}))

    def test_hand_computed_window(self):
        self.fixture(b_kills=3)
        doc = rm.compute(self.root, "2026-01-01", "2026-01-02")
        m = {k: v["value"] for k, v in doc["metrics"].items()}
        self.assertEqual(m["service_cpu_core_pct_total"], 50 + 10 + 90)   # x mean 50, y 10, syncd 90
        self.assertEqual(m["service_cpu_core_pct_max"], 90)
        self.assertEqual(m["service_rss_mb_total"], 60 + 1000 + 900)
        self.assertEqual(m["service_rss_mb_max"], 1000)
        self.assertEqual(m["service_kill_count"], 1.5)                    # 3 kills over 2 days
        self.assertEqual(m["service_restart_count"], 0.5)
        self.assertEqual(m["node_disk_free_pct_min"], 3.0)
        self.assertEqual(m["sync_folder_file_count_max"], 1200)
        self.assertEqual(m["sync_folder_need_files_max"], 300)
        # declared unit-days: node-a 2 days x 2 + node-b 2 days x 1 = 6; measured 2 + 1 + 1 = 4
        self.assertEqual(m["resource_coverage_ratio"], round(4 / 6, 3))
        self.assertEqual(doc["cohort"]["sample_size"], 3)
        self.assertTrue(doc["cohort"]["id"].startswith("resources-2026-01-01-to-2026-01-02-units-"))
        self.assertEqual(text_findings(json.dumps(doc)), [])

    def test_unmeasured_is_null_and_missing_node_lowers_coverage(self):
        self.put("node-a", "2026-01-01", evidence({"svc-x": unit(10, 10)}))
        self.put("node-b", "2026-01-01", {"readers": {"resources": {"status": "error", "reason": "x"}}})
        doc = rm.compute(self.root, "2026-01-01", "2026-01-01")
        m = {k: v["value"] for k, v in doc["metrics"].items()}
        self.assertEqual(m["resource_coverage_ratio"], 0.5)  # node-b assumed to declare 1 unit, all unmeasured
        self.assertIsNone(m["sync_folder_file_count_max"])
        empty = rm.compute(self.root, "2026-02-01", "2026-02-01")
        self.assertTrue(all(v["value"] is None for v in empty["metrics"].values()))
        self.assertEqual(empty["cohort"]["sample_size"], 0)

    def test_unreadable_kill_counts_are_null_not_zero(self):
        quiet = unit(10, 10)
        quiet["kills"] = quiet["restarts"] = None
        self.put("node-a", "2026-01-01", evidence({"svc-x": quiet}))
        doc = rm.compute(self.root, "2026-01-01", "2026-01-01", nodes=["node-a"])
        self.assertIsNone(doc["metrics"]["service_kill_count"]["value"])
        self.assertIsNone(doc["metrics"]["service_restart_count"]["value"])

    def test_instantaneous_cpu_fails_the_cpu_invariant(self):
        self.put("node-a", "2026-01-01", evidence({"svc-x": unit(10, 10, basis="instant")}))
        doc = rm.compute(self.root, "2026-01-01", "2026-01-01", nodes=["node-a"])
        self.assertFalse(doc["invariants"][rm.INV_CPU])

    def test_metric_names_rules_and_invariants_match_the_target(self):
        Draft202012Validator(json.loads((ROOT / "schemas/target-v2.schema.json").read_text()),
                             format_checker=FormatChecker()).validate(TARGET)
        self.assertEqual(list(rm.RULES), TARGET["metrics"])
        rule_ids = {r["id"] for r in TARGET["stop_rules"]}
        self.assertTrue({r["stop_rule"] for r in rm.RULES.values() if "stop_rule" in r} <= rule_ids)
        named = {rm.INV_NAMES, rm.INV_MISSING, rm.INV_CPU, rm.INV_ALLOWLIST}
        self.assertEqual(named, {i for i in TARGET["invariants"] if not i.startswith("min_sample_size")})

    def test_feeds_the_evaluator_and_a_kill_rise_is_a_regression(self):
        self.fixture(b_kills=0)
        self.put("node-b", "2026-01-02", evidence({"syncd": unit(90, 900)}))  # 4 node-days: the target floor
        base = rm.compute(self.root, "2026-01-01", "2026-01-02")
        self.assertEqual(base["cohort"]["sample_size"], 4)
        self.put("node-b", "2026-01-02", evidence({"syncd": unit(30, 900, kills=4)}))  # cheaper, but killed
        treat = rm.compute(self.root, "2026-01-01", "2026-01-02", baseline=base)
        self.assertTrue(treat["invariants"][rm.INV_ALLOWLIST])
        rec, _ = build(TARGET, base, treat, producer="collector", evaluator="skrsi-local",
                       evidence_class="live_self_retrieval", now="2026-01-03T00:00:00Z")
        validate_v4(rec)
        self.assertEqual(rec["outcome"], "regression")
        guards = {g["name"]: g["passed"] for g in rec["guardrails"]}
        self.assertFalse(guards["stop_rule:kills-rise"])
        small = rm.compute(self.root, "2026-01-01", "2026-01-01")  # 2 node-days: capped, never a verdict
        rec, _ = build(TARGET, small, small, producer="collector", evaluator="skrsi-local",
                       evidence_class="live_self_retrieval", now="2026-01-03T00:00:00Z")
        self.assertEqual(rec["outcome"], "inconclusive")

    def test_allowlist_change_is_caught(self):
        self.fixture()
        base = rm.compute(self.root, "2026-01-01", "2026-01-02")
        self.put("node-a", "2026-01-03", evidence({"svc-x": unit(1, 1)}))  # svc-y dropped from the allowlist
        treat = rm.compute(self.root, "2026-01-03", "2026-01-03", nodes=["node-a"], baseline=base)
        self.assertFalse(treat["invariants"][rm.INV_ALLOWLIST])


if __name__ == "__main__":
    unittest.main()
