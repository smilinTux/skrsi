"""Framework and instance model (card 7aa8e01f, P1.7).

Every rejection has its own failing fixture. Private-looking strings are built
by concatenation so this file itself passes the public scrub.
"""

import copy
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.cluster_config import YamlSubsetError, cluster_errors, load_cluster_yaml
from scripts.validate_instance import instance_errors, scrub_errors


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "instance-repo"
SCHEMA_PATH = ROOT / "schemas" / "cluster-v1.schema.json"
VALIDATOR = ROOT / "scripts" / "validate_instance.py"
GENERATOR = ROOT / "scripts" / "generate-config-template.sh"

AGGREGATE = {
    "node": "node-a",
    "window_start": "2026-11-10T00:00:00Z",
    "window_end": "2026-11-11T00:00:00Z",
    "cron": {"runs": 412, "failed": 2, "failure_rate": 0.0049},
    "gateway": {"requests": 2949, "errors_502": 12, "input_tokens": 1200000, "p95_ms": 1672},
}


def example_cluster() -> dict:
    return yaml.safe_load((TEMPLATE / "cluster.yaml.example").read_text(encoding="utf-8"))


def schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


class InstanceFixture:
    """A passing instance built from the template, in a temp dir."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "skrsi-config-example"
        shutil.copytree(TEMPLATE, self.root)
        (self.root / "cluster.yaml.example").rename(self.root / "cluster.yaml")
        (self.root / ".gitmodules.example").unlink()  # step 3 of creating an instance
        self.write_json("evidence/node-a/2026-11-11.json", AGGREGATE)
        node_b = dict(AGGREGATE, node="node-b")
        self.write_json("evidence/node-b/2026-11-11.json", node_b)
        self.write_json("baselines/2026-11-11-example.json", {
            "cluster": "example",
            "measured_on": "2026-11-11",
            "session_start_tokens_median": 77562,
            "fix_share_14d": 0.32,
        })

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, relative: str, data) -> Path:
        return self.write(relative, json.dumps(data, indent=2) + "\n")

    def set_cluster(self, cluster: dict) -> None:
        self.write("cluster.yaml", yaml.safe_dump(cluster, sort_keys=False))

    def errors(self) -> list[str]:
        return instance_errors(self.root)

    def cleanup(self) -> None:
        self._tmp.cleanup()


class ClusterSchemaTests(unittest.TestCase):
    def test_schema_is_valid_and_versioned(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.assertEqual(schema["properties"]["schema"]["const"], "skrsi.cluster.v1")
        self.assertFalse(schema["additionalProperties"])
        for name in ("cluster", "config_schema", "nodes", "canon"):
            self.assertIn(name, schema["required"])
        self.assertIn("overrides", schema["properties"])

    def test_example_validates(self):
        schema_validator().validate(example_cluster())

    def test_example_uses_synthetic_names_only(self):
        cluster = example_cluster()
        self.assertEqual(cluster["cluster"], "example")
        for node in cluster["nodes"]:
            self.assertRegex(node["name"], r"^node-[a-z]$")

    def test_example_covers_every_field(self):
        cluster = example_cluster()
        node = cluster["nodes"][0]
        self.assertTrue({"name", "role", "readers"} <= set(node))
        self.assertEqual(set(cluster["canon"]), {"repo", "namespace"})
        self.assertTrue(cluster["overrides"])
        self.assertTrue(cluster["services"])

    def assert_rejected(self, cluster: dict) -> None:
        with self.assertRaises(ValidationError):
            schema_validator().validate(cluster)

    def test_rejects_unknown_top_level_field(self):
        cluster = example_cluster()
        cluster["hosts"] = ["node-a"]
        self.assert_rejected(cluster)

    def test_rejects_unknown_node_field(self):
        cluster = example_cluster()
        cluster["nodes"][0]["address"] = "node-a"
        self.assert_rejected(cluster)

    def test_rejects_fqdn_or_uppercase_node_name(self):
        for name in ("node-a.example.com", "Node-A", "node_a", "-node", "a"):
            with self.subTest(name=name):
                cluster = example_cluster()
                cluster["nodes"][0]["name"] = name
                self.assert_rejected(cluster)

    def test_rejects_duplicate_or_missing_nodes(self):
        cluster = example_cluster()
        cluster["nodes"] = []
        self.assert_rejected(cluster)

    def test_rejects_missing_canon_namespace(self):
        cluster = example_cluster()
        del cluster["canon"]["namespace"]
        self.assert_rejected(cluster)

    def test_rejects_bad_override(self):
        cluster = example_cluster()
        cluster["overrides"][0]["threshold"] = "fast"
        self.assert_rejected(cluster)

    def test_rejects_wrong_config_schema_version(self):
        cluster = example_cluster()
        cluster["config_schema"] = 2
        self.assert_rejected(cluster)


class ClusterConfigStdlibTests(unittest.TestCase):
    """The stdlib loader and mirror must agree with PyYAML and the JSON Schema."""

    VARIANTS = (
        "schema: skrsi.cluster.v1\nconfig_schema: 1\ncluster: lab\nnodes:\n- name: node-a\n  role: primary\n"
        "  readers:\n  - cron\n  - github\ncanon: {repo: example-org/canon, namespace: ops}\n",
        "schema: 'skrsi.cluster.v1'\nconfig_schema: 1\ncluster: \"lab\"   # trailing comment\n"
        "nodes:\n    -   name: node-a\n        role: primary\n        readers: []\n"
        "canon:\n  repo: canon\n  namespace: ops\noverrides:\n  - target_id: abc\n    metric: p95_ms\n"
        "    threshold: 1.5e+3\n    paused: false\n",
        "a: 1.5e3\nb: -0.25\nc: .5\nd: '#not a comment'\ne: x#y\nf: [a, b c, 3]\ng: {}\nh: []\n",
        "a:\n  b:\n    - 1\n    - -2\n    - x # not a comment#here\n  c: ~\n  d: null\n  e: True\n",
    )

    def test_loader_matches_pyyaml(self):
        texts = [(TEMPLATE / "cluster.yaml.example").read_text(encoding="utf-8"), *self.VARIANTS]
        for text in texts:
            with self.subTest(text=text[:40]):
                self.assertEqual(load_cluster_yaml(text), yaml.safe_load(text))

    def test_loader_rejects_yaml_outside_the_subset(self):
        for text in ("a: &anchor 1\n", "a: *ref\n", "a: !!str 1\n", "a: |\n  text\n", "---\na: 1\n",
                     "a:\n\tb: 1\n", "a: 1\na: 2\n", "a: [1, 2\n", "a: 1\n   b: 2\n", "just text\n",
                     "a: \"x\\ny\"\n"):
            with self.subTest(text=text):
                with self.assertRaises(YamlSubsetError):
                    load_cluster_yaml(text)

    def test_mirror_agrees_with_schema(self):
        mutations = [
            lambda c: c,
            lambda c: c.update(hosts=["node-a"]),
            lambda c: c.pop("canon"),
            lambda c: c.pop("services"),
            lambda c: c.pop("overrides"),
            lambda c: c.update(schema="skrsi.cluster.v2"),
            lambda c: c.update(config_schema=2),
            lambda c: c.update(config_schema=True),
            lambda c: c.update(cluster="Lab"),
            lambda c: c.update(cluster="x" * 33),
            lambda c: c.update(nodes=[]),
            lambda c: c.update(nodes="node-a"),
            lambda c: c["nodes"][0].update(name="node-a.example.com"),
            lambda c: c["nodes"][0].update(name="a"),
            lambda c: c["nodes"][0].update(name="node_a"),
            lambda c: c["nodes"][0].update(address="x"),
            lambda c: c["nodes"][0].pop("readers"),
            lambda c: c["nodes"][0].update(readers=["cron", "cron"]),
            lambda c: c["nodes"][0].update(readers=["Cron"]),
            lambda c: c["nodes"][0].update(role=""),
            lambda c: c.update(services=[{"name": "svc", "repo": "a/b/c"}]),
            lambda c: c.update(services=[{"name": "svc", "repo": "o/r"}, {"repo": "o/r", "name": "svc"}]),
            lambda c: c["canon"].update(namespace="Ops"),
            lambda c: c["canon"].update(extra="x"),
            lambda c: c["overrides"][0].update(threshold="fast"),
            lambda c: c["overrides"][0].update(threshold=True),
            lambda c: c["overrides"][0].update(paused="no"),
            lambda c: c["overrides"][0].update(metric="P95"),
            lambda c: c["overrides"][0].pop("metric"),
            lambda c: c.update(overrides=[]),
        ]
        validator = schema_validator()
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                cluster = example_cluster()
                mutate(cluster)
                self.assertEqual(cluster_errors(cluster) == [], validator.is_valid(cluster), cluster_errors(cluster))


class ValidateInstanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = InstanceFixture()

    def tearDown(self):
        self.fixture.cleanup()

    def assert_one_error(self, needle: str) -> list[str]:
        errors = self.fixture.errors()
        self.assertTrue(errors, "expected a violation, got none")
        self.assertTrue(any(needle in error for error in errors), errors)
        return errors

    def test_passing_instance(self):
        self.assertEqual(self.fixture.errors(), [])

    def test_template_itself_passes_with_example_cluster(self):
        self.assertEqual(instance_errors(TEMPLATE, TEMPLATE / "cluster.yaml.example"), [])

    def test_framework_submodule_is_not_walked(self):
        self.fixture.write_json("framework/tests/fixture.json", {"addr": "10.1." + "2.3"})
        self.assertEqual(self.fixture.errors(), [])

    def test_rejects_missing_cluster_file(self):
        (self.fixture.root / "cluster.yaml").unlink()
        self.assert_one_error("cluster.yaml")

    def test_rejects_cluster_that_fails_schema(self):
        cluster = example_cluster()
        cluster["nodes"][0]["name"] = "Node.A"
        self.fixture.set_cluster(cluster)
        self.assert_one_error("cluster.yaml")

    def test_rejects_config_schema_mismatch(self):
        self.fixture.write("CONFIG_SCHEMA", "2\n")
        self.assert_one_error("CONFIG_SCHEMA")

    def test_rejects_ipv4_address(self):
        self.fixture.write_json("baselines/b.json", {"gateway": "10.0." + "4.17:18780"})
        errors = self.assert_one_error("IPv4")
        self.assertTrue(any(error.startswith("baselines/b.json") for error in errors), errors)

    def test_rejects_ipv6_address(self):
        self.fixture.write_json("baselines/b.json", {"peer": "fd7a:115c:a1e0:" + ":1"})
        self.assert_one_error("IPv6")

    def test_rejects_domain(self):
        self.fixture.write_json("baselines/b.json", {"endpoint": "skhub." + "corp-internal.net"})
        self.assert_one_error("domain")

    def test_rejects_domain_in_markdown(self):
        self.fixture.write("evaluations/notes.md", "Collected from build." + "acme.io today.\n")
        self.assert_one_error("domain")

    def test_rejects_home_path(self):
        for path in ("/" + "home/someone/state", "/" + "Users/someone/x", "~" + "/state/skrsi", "/" + "root/.cache"):
            with self.subTest(path=path):
                self.fixture.write_json("baselines/b.json", {"state_dir": path})
                self.assert_one_error("home or user path")

    def test_rejects_unknown_node_name_in_record(self):
        self.fixture.write_json("evidence/node-a/2026-11-11.json", dict(AGGREGATE, node="node-z"))
        self.assert_one_error("node-z")

    def test_rejects_unknown_node_name_in_list(self):
        self.fixture.write_json("baselines/b.json", {"nodes": ["node-a", "build-host"]})
        self.assert_one_error("build-host")

    def test_rejects_evidence_for_unknown_node(self):
        self.fixture.write_json("evidence/node-z/2026-11-11.json", dict(AGGREGATE, node="node-a"))
        self.assert_one_error("evidence/node-z")

    def test_rejects_evidence_outside_node_dir(self):
        self.fixture.write_json("evidence/2026-11-11.json", AGGREGATE)
        self.assert_one_error("evidence/<node>/")

    def test_rejects_evidence_with_bad_file_name(self):
        for name in ("latest.json", "2026-13-40.json", "2026-11-11.yaml", "deep/2026-11-11.json"):
            with self.subTest(name=name):
                fixture = InstanceFixture()
                try:
                    fixture.write_json(f"evidence/node-a/{name}", AGGREGATE)
                    errors = fixture.errors()
                    self.assertTrue(any("YYYY-MM-DD" in error for error in errors), errors)
                finally:
                    fixture.cleanup()

    def test_rejects_evidence_node_field_that_disagrees_with_path(self):
        self.fixture.write_json("evidence/node-a/2026-11-11.json", dict(AGGREGATE, node="node-b"))
        self.assert_one_error("does not match its path")

    def test_rejects_content_like_fields(self):
        cases = {
            "prompt": {"prompt": "summarize the outage"},
            "response": {"turns": [{"response": "done"}]},
            "memory content": {"memory_content": "a remembered fact"},
            "card text": {"card": {"description": "fix the router"}},
            "pr text": {"pr_body": "This PR fixes"},
            "credential": {"auth": {"token": "x"}},
            "dsn": {"dsn": "postgres"},
        }
        for label, record in cases.items():
            with self.subTest(label=label):
                fixture = InstanceFixture()
                try:
                    fixture.write_json("evaluations/e.json", record)
                    errors = fixture.errors()
                    self.assertTrue(any("content-like field" in error for error in errors), errors)
                finally:
                    fixture.cleanup()

    def test_aggregate_token_counts_are_not_credentials(self):
        self.fixture.write_json("baselines/b.json", {"input_tokens": 10, "token_count": 3, "tokens_p90": 5})
        self.assertEqual(self.fixture.errors(), [])

    def test_rejects_binary_file(self):
        (self.fixture.root / "baselines" / "metrics.db").write_bytes(b"\x00\xff\x00SQLite")
        self.assert_one_error("not UTF-8 text")

    def test_rejects_unknown_skrsi_schema_and_invalid_evaluation(self):
        self.fixture.write_json("evaluations/e.json", {"schema": "skrsi.evaluation.v9"})
        self.assert_one_error("unknown schema")
        fixture = InstanceFixture()
        try:
            fixture.write_json("evaluations/e.json", {"schema": "skrsi.evaluation.v4"})
            errors = fixture.errors()
            self.assertTrue(any("skrsi.evaluation.v4" in error for error in errors), errors)
        finally:
            fixture.cleanup()

    def test_example_evaluation_v4_is_accepted(self):
        record = json.loads((ROOT / "examples" / "evaluation-v4.json").read_text(encoding="utf-8"))
        record.pop("$schema_file")
        self.fixture.write_json("evaluations/2026-11-11-example.json", record)
        self.assertEqual(self.fixture.errors(), [])

    def test_every_violation_is_reported(self):
        self.fixture.write_json("baselines/b.json", {"a": "10.0." + "4.17", "prompt": "x"})
        self.fixture.write_json("evidence/node-z/2026-11-11.json", AGGREGATE)
        errors = self.fixture.errors()
        self.assertGreaterEqual(len(errors), 3, errors)


class ValidateInstanceCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(VALIDATOR), *args], capture_output=True, text=True, check=False)

    def test_cli_passes_and_fails(self):
        fixture = InstanceFixture()
        try:
            ok = self.run_cli(str(fixture.root))
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("instance validation passed", ok.stdout)
            fixture.write_json("baselines/b.json", {"a": "10.0." + "4.17", "prompt": "x"})
            bad = self.run_cli(str(fixture.root))
            self.assertEqual(bad.returncode, 1)
            lines = [line for line in bad.stderr.splitlines() if line.startswith("baselines/b.json")]
            self.assertEqual(len(lines), 2, bad.stderr)
        finally:
            fixture.cleanup()

    def test_cli_validates_template_with_example_cluster(self):
        result = self.run_cli("--cluster", str(TEMPLATE / "cluster.yaml.example"), str(TEMPLATE))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_usage_error(self):
        self.assertEqual(self.run_cli().returncode, 2)


class ScrubTests(unittest.TestCase):
    def test_framework_and_template_are_clean(self):
        self.assertEqual(scrub_errors([ROOT]), [])
        self.assertEqual(scrub_errors([TEMPLATE], hostnames=True), [])

    def test_scrub_flags_private_ip_but_not_documentation_ip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("see 192.0." + "2.10 and 203.0." + "113.5\n", encoding="utf-8")
            self.assertEqual(scrub_errors([root]), [])
            (root / "a.md").write_text("gateway at 192.168." + "7.41\n", encoding="utf-8")
            self.assertEqual(len(scrub_errors([root])), 1)

    def test_scrub_flags_host_shaped_names_in_template_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cluster.yaml").write_text("nodes: [" + "buildbox" + "2031]\n", encoding="utf-8")
            self.assertEqual(scrub_errors([root]), [])
            self.assertEqual(len(scrub_errors([root], hostnames=True)), 1)

    def test_scrub_uses_private_denylist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("runs on secret" + "name\n", encoding="utf-8")
            self.assertEqual(scrub_errors([root]), [])
            self.assertEqual(len(scrub_errors([root], denylist=["secret" + "name"])), 1)


class GeneratorTests(unittest.TestCase):
    def test_generator_copies_fixture_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            subprocess.run(["bash", str(GENERATOR), str(out)], check=True, capture_output=True)
            self.assertEqual(_differences(filecmp.dircmp(TEMPLATE, out)), [])
            self.assertTrue((out / ".github" / "workflows" / "validate-instance.yml").is_file())
            self.assertTrue((out / "CONFIG_SCHEMA").is_file())

    def test_generator_requires_output_dir(self):
        result = subprocess.run(["bash", str(GENERATOR)], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage", result.stderr)

    def test_template_has_required_layout(self):
        for relative in ("CONFIG_SCHEMA", "cluster.yaml.example", "README.md", ".gitmodules.example",
                         "baselines/README.md", "evidence/README.md", "evaluations/README.md",
                         ".github/workflows/validate-instance.yml"):
            with self.subTest(path=relative):
                self.assertTrue((TEMPLATE / relative).is_file())
        self.assertEqual((TEMPLATE / "CONFIG_SCHEMA").read_text(encoding="utf-8").strip(), "1")
        self.assertEqual(example_cluster()["config_schema"], 1)

    def test_instance_workflow_runs_validator_and_pins_actions(self):
        workflow = (TEMPLATE / ".github" / "workflows" / "validate-instance.yml").read_text(encoding="utf-8")
        self.assertIn("framework/scripts/validate_instance.py", workflow)
        for line in workflow.splitlines():
            if "uses:" in line:
                self.assertRegex(line, r"uses: [\w./-]+@[0-9a-f]{40}")


class ReviewRoundOneTests(unittest.TestCase):
    """Findings of the first independent review of PR 12. Synthetic names only."""

    HOST = "buildbox" + "77"

    def setUp(self):
        self.fixture = InstanceFixture()

    def tearDown(self):
        self.fixture.cleanup()

    def errors_for(self, relative: str) -> list[str]:
        return [e for e in self.fixture.errors() if e.startswith(relative + ":")]

    # 1
    def test_nested_framework_and_git_dirs_are_walked(self):
        leak = {"addr": "10.9." + "8.7"}
        for relative in ("baselines/framework/notes.json", "evidence/node-a/framework/2026-11-12.json",
                         "evaluations/.git/leak.json"):
            with self.subTest(path=relative):
                self.fixture.write_json(relative, leak)
                self.assertTrue(any("IPv4" in e for e in self.errors_for(relative)), self.fixture.errors())

    # 2
    def test_undeclared_host_shaped_names_anywhere_are_rejected(self):
        cases = {
            "baselines/a.json": json.dumps({"machine": self.HOST}),
            "baselines/b.json": json.dumps({"per_node": {self.HOST: {"n": 1}}}),
            "baselines/c.json": json.dumps({"note": "ran on " + self.HOST}),
            "baselines/d.yaml": self.HOST + ": 3\n",
            "baselines/README.md": "measured on " + self.HOST + "\n",
        }
        for relative, text in cases.items():
            with self.subTest(path=relative):
                self.fixture.write(relative, text)
                self.assertTrue(any(self.HOST in e for e in self.errors_for(relative)), self.fixture.errors())

    def test_declared_host_shaped_node_and_percentiles_are_allowed(self):
        cluster = example_cluster()
        cluster["nodes"][0]["name"] = self.HOST
        self.fixture.set_cluster(cluster)
        shutil.rmtree(self.fixture.root / "evidence" / "node-a")
        self.fixture.write_json(f"evidence/{self.HOST}/2026-11-11.json", dict(AGGREGATE, node=self.HOST))
        self.fixture.write_json("baselines/b.json", {"per_node": {self.HOST: {"p95": 3, "p50": 1}}})
        self.fixture.write("baselines/README.md", "measured on " + self.HOST + ", sha256 digests\n")
        self.assertEqual(self.fixture.errors(), [])

    # 3
    def test_drift_job_clones_template_repo_or_says_it_is_absent(self):
        workflow = (ROOT / ".github" / "workflows" / "instance-template.yml").read_text(encoding="utf-8")
        self.assertIn("smilinTux/skrsi-config-template", workflow)
        self.assertIn("git ls-remote", workflow)
        self.assertIn("::notice::", workflow)
        self.assertNotIn("diff -r templates/instance-repo", workflow)
        doc = (ROOT / "docs" / "INSTANCE-MODEL.md").read_text(encoding="utf-8")
        self.assertIn("no-op until", doc)

    # 4
    def test_content_keys_match_on_tokens(self):
        for key in ("user_prompt", "response_text", "memory_snippet", "card_name", "prTitle", "systemPrompt",
                    "db-password", "apiKey", "session-transcript", "commitMessage", "secret", "tokens"):
            with self.subTest(key=key):
                fixture = InstanceFixture()
                try:
                    fixture.write_json("baselines/b.json", {"x": {key: "value"}})
                    self.assertTrue(any("content-like" in e for e in fixture.errors()), (key, fixture.errors()))
                finally:
                    fixture.cleanup()

    def test_numeric_aggregate_names_are_allowed(self):
        self.fixture.write_json("baselines/b.json", {
            "input_tokens": 5, "output_tokens": 6, "total_tokens": 11, "token_count": 3, "message_count": 2,
            "prompt_tokens_p95": 4.5, "tokens": {"input": 1, "output": 2}})
        self.assertEqual(self.fixture.errors(), [])

    # 5
    def test_file_types_are_restricted(self):
        for relative in ("baselines/x.csv", "evaluations/notes.md", "extra.txt", "scripts/x.json",
                         "evidence/node-a/README.txt"):
            with self.subTest(path=relative):
                self.fixture.write(relative, "{}\n")
                self.assertTrue(any("file type" in e for e in self.errors_for(relative)), self.fixture.errors())

    def test_allowed_file_types_pass(self):
        self.fixture.write(".github/workflows/extra.yaml", "name: extra\n")
        self.fixture.write_json("evaluations/recurrence/2026-11.json", {"unverified-done": 2})
        self.fixture.write(".gitmodules", "[submodule \"framework\"]\n")
        self.assertEqual(self.fixture.errors(), [])

    # 7
    def test_escaped_json_strings_are_checked_decoded(self):
        dot = "\\u002e"
        self.fixture.write("baselines/b.json", '{"x": "10' + dot + '1' + dot + '2' + dot + '3"}\n')
        self.assertTrue(any("IPv4" in e for e in self.errors_for("baselines/b.json")), self.fixture.errors())
        self.fixture.write("baselines/b.json", '{"\\u0062uildbox77": 1}\n')
        self.assertTrue(any(self.HOST in e for e in self.errors_for("baselines/b.json")), self.fixture.errors())

    # 8
    def test_leading_zero_ipv4_and_srv_paths_are_rejected(self):
        self.fixture.write_json("baselines/b.json", {"x": "192.168" + ".000.041"})
        self.assertTrue(any("IPv4" in e for e in self.errors_for("baselines/b.json")), self.fixture.errors())
        self.fixture.write_json("baselines/b.json", {"x": "/" + "srv/work/someone"})
        self.assertTrue(any("path" in e for e in self.errors_for("baselines/b.json")), self.fixture.errors())

    # 9
    def test_yaml_reader_fails_closed(self):
        for text in ("a: [x, 'b c']\n", "a: {k: 'v'}\n", "a: 'abc\n", "a: abc'\n", "a: it's\n",
                     "a: b: c\n", "a: 012\n", "a: [01]\n", "- a: b: c\n"):
            with self.subTest(text=text):
                with self.assertRaises(YamlSubsetError):
                    load_cluster_yaml(text)


class ReviewRoundTwoTests(unittest.TestCase):
    """Findings of the second independent review of PR 12. Synthetic names only."""

    HOST = "buildbox" + "77"

    def setUp(self):
        self.fixture = InstanceFixture()

    def tearDown(self):
        self.fixture.cleanup()

    def assert_flagged(self, relative: str, record, needle: str) -> None:
        fixture = InstanceFixture()
        try:
            if isinstance(record, str):
                fixture.write(relative, record)
            else:
                fixture.write_json(relative, record)
            errors = [e for e in fixture.errors() if e.startswith(relative + ":")]
            self.assertTrue(any(needle in e for e in errors), fixture.errors())
        finally:
            fixture.cleanup()

    # A
    def test_host_shaped_names_inside_identifiers_and_any_case(self):
        host = self.HOST
        cases = {
            "key with suffix": ({host + "_p95_ms": 12}, host),
            "nested key with prefix": ({"by_node": {"node_" + host: 1}}, host),
            "underscore value": ({"x": host + "_gpu"}, host),
            "hyphen value": ({"x": host + "-worker"}, host),
            "upper case": ({"x": host.upper()}, host),
            "capitalized": ({"x": host.capitalize()}, host),
            "trailing letter": ({"x": "labwk" + "01" + "x"}, "labwk01"),
            "leading digit": ({"x": "2" + host}, host),
        }
        for label, (record, needle) in cases.items():
            with self.subTest(case=label):
                self.assert_flagged("baselines/b.json", record, needle)

    def test_declared_hyphenated_node_passes(self):
        cluster = example_cluster()
        cluster["nodes"][0]["name"] = "gpu-box" + "12"
        self.fixture.set_cluster(cluster)
        shutil.rmtree(self.fixture.root / "evidence" / "node-a")
        self.fixture.write_json("baselines/b.json", {"x": "ran on gpu-box" + "12", "by_node": {"gpu-box" + "12": 1}})
        self.assertEqual(self.fixture.errors(), [])

    def test_no_false_positive_names(self):
        from scripts.instance_text import host_shaped
        words = ["qwen3-8b", "ornith-tiny", "llama3-70b", "gemma2-27b", "gpt-oss-120b", "claude-opus-5-5", "bge-m3",
                 "phi3", "p50", "p95", "p99", "p999_ms", "rfc3339", "skrsi-v1.0.0", "qwen2", "gpt4o", "amd64",
                 "arm64", "aarch64", "win32", "win64", "py312", "cp311", "ubuntu2404", "node20", "top10", "ndcg10",
                 "es2022", "sha256", "evidence_sha256", "7aa8e01f", "3611ea28-0c1d-4e2b-9f00-1234abcd5678",
                 "9cadaa769ca26306e56abf566b13792a24e7dd47", "skrsi.evaluation.v4", "2026-11-12T00:00:00Z"]
        for word in words:
            with self.subTest(word=word):
                self.assertEqual(host_shaped(word), [])

    # B
    def test_content_tokens_cover_queries_and_outputs(self):
        for key in ("query", "search_query", "question", "answer", "input", "output", "notes", "summary",
                    "snippet", "excerpt", "stderr", "stdout", "log", "payload", "sample", "pwd", "auth", "authHeader",
                    "header"):
            with self.subTest(key=key):
                self.assert_flagged("baselines/b.json", {"x": {key: "value"}}, "content-like")
        self.assert_flagged("baselines/b.json", {"query": ["a", "b"]}, "content-like")
        self.assert_flagged("baselines/b.json", {"output": {"model": "x"}}, "content-like")

    def test_numbers_never_carry_content(self):
        self.fixture.write_json("baselines/b.json", {
            "token_budget": 1000, "message_id": 5, "input_tokens": 10, "output_tokens": 10, "secret": 1234,
            "query_count": 3, "output": {"p95_ms": 12}, "auth_failures": [1, 2], "sample_size": 40})
        self.assertEqual(self.fixture.errors(), [])

    # D
    def test_github_dir_allows_only_workflow_yaml(self):
        for relative in (".github/CODEOWNERS", ".github/workflows/run.sh", ".github/workflows/nested/x.yml",
                         ".github/notes.md"):
            with self.subTest(path=relative):
                self.assert_flagged(relative, "name: x\n", "file type")

    def test_readme_only_at_root_and_directly_in_record_dirs(self):
        for relative in ("evaluations/recurrence/README.md", "baselines/2026/README.md"):
            with self.subTest(path=relative):
                self.assert_flagged(relative, "notes\n", "file type")
        self.fixture.write("baselines/README.md", "notes\n")
        self.assertEqual(self.fixture.errors(), [])

    def test_symlinks_are_rejected(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        (outside / "data.json").write_text("{}\n", encoding="utf-8")
        (self.fixture.root / "baselines" / "link.json").symlink_to(outside / "data.json")
        (self.fixture.root / "evaluations" / "linkdir").symlink_to(outside, target_is_directory=True)
        errors = self.fixture.errors()
        for relative in ("baselines/link.json", "evaluations/linkdir"):
            with self.subTest(path=relative):
                self.assertTrue(any(e.startswith(relative + ":") and "symlink" in e for e in errors), errors)

    # E
    def test_credential_shaped_values_are_rejected(self):
        values = {
            "openai style": "s" + "k-" + "proj" + "A1b2C3d4E5f6G7h8I9j0K",
            "github token": "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0Kl",
            "github pat": "github" + "_pat_" + "11ABCDEFG",
            "slack": "xo" + "xb-" + "1234-abcd",
            "aws key id": "AK" + "IA" + "ABCDEFGHIJ234567",
            "pem": "-----" + "BEGIN OPENSSH PRIVATE KEY-----",
            "userinfo in url": "postgres" + chr(58) + "//" + "user" + chr(58) + "q" * 6 + chr(64) + "db/x",
        }
        for label, value in values.items():
            with self.subTest(case=label):
                self.assert_flagged("baselines/b.json", {"x": value}, "credential")

    def test_service_names_are_not_credentials(self):
        self.fixture.write_json("baselines/b.json", {"x": "sk-build-quality-evaluation-target", "y": "sk-default"})
        self.assertEqual(self.fixture.errors(), [])


class GeneratorSafetyTests(unittest.TestCase):
    """Finding 6. Destructive commands are stubbed where a bug could reach real paths."""

    def run_generator(self, out, env=None):
        return subprocess.run(["bash", str(GENERATOR), str(out)], capture_output=True, text=True, check=False,
                              env=env)

    def test_keeps_git_dir_and_clears_the_rest(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / ".git").mkdir()
            (out / ".git" / "HEAD").write_text("ref", encoding="utf-8")
            (out / "CONFIG_SCHEMA").write_text("1\n", encoding="utf-8")
            (out / "stale.txt").write_text("left over", encoding="utf-8")
            result = self.run_generator(out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((out / ".git" / "HEAD").is_file())
            self.assertFalse((out / "stale.txt").exists())
            self.assertTrue((out / "cluster.yaml.example").is_file())

    def test_refuses_non_empty_dir_that_is_not_a_git_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "keep.txt").write_text("mine", encoding="utf-8")
            result = self.run_generator(directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((Path(directory) / "keep.txt").is_file())

    def test_refuses_unrelated_git_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            (repo / "precious.py").write_text("print('mine')\n", encoding="utf-8")
            result = self.run_generator(repo)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((repo / "precious.py").is_file())
            self.assertIn("CONFIG_SCHEMA", result.stderr)

    def test_refuses_home_even_when_it_is_a_git_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".git").mkdir()
            (home / "keep.txt").write_text("mine", encoding="utf-8")
            result = self.run_generator(home, env={**os.environ, "HOME": str(home)})
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((home / "keep.txt").is_file())

    def test_refuses_the_framework_repo_itself(self):
        before = sorted(p.name for p in ROOT.iterdir())
        result = self.run_generator(ROOT, env=self.stubbed_env())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sorted(p.name for p in ROOT.iterdir()), before)

    def test_refuses_filesystem_root(self):
        result = self.run_generator("/", env=self.stubbed_env())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refus", result.stderr)

    def stubbed_env(self) -> dict:
        stub = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, stub)
        for name in ("find", "rm", "cp"):
            (stub / name).write_text("#!/bin/sh\necho stubbed >&2\nexit 0\n", encoding="utf-8")
            (stub / name).chmod(0o755)
        return {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"}


def _differences(comparison: filecmp.dircmp) -> list[str]:
    found = [*comparison.left_only, *comparison.right_only, *comparison.diff_files, *comparison.funny_files]
    for sub in comparison.subdirs.values():
        found.extend(_differences(sub))
    return found


class DocsTests(unittest.TestCase):
    def test_instance_model_doc_states_the_split(self):
        doc = (ROOT / "docs" / "INSTANCE-MODEL.md").read_text(encoding="utf-8")
        for phrase in ("service repo", "framework", "instance", "submodule", "release tag", "CONFIG_SCHEMA",
                       "evidence/<node>/", "cluster.yaml", "canon", "deploy key", "Allowed", "Not allowed"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, doc)

    def test_instance_model_is_linked(self):
        self.assertIn("INSTANCE-MODEL.md", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("INSTANCE-MODEL.md", (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8"))
        self.assertIn("cluster-v1.schema.json", (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8"))

    def test_changelog_records_card(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = changelog.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
        self.assertIn("7aa8e01f", unreleased)

    def test_no_em_or_en_dashes_in_new_files(self):
        paths = [ROOT / "docs" / "INSTANCE-MODEL.md", SCHEMA_PATH, VALIDATOR, GENERATOR,
                 *[p for p in TEMPLATE.rglob("*") if p.is_file()]]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, path)
            self.assertNotIn("\u2013", text, path)


if __name__ == "__main__":
    unittest.main()
