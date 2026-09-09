import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.validate_public import scan
from scripts.migrate_evaluation_v2_to_v3 import migrate


ROOT = Path(__file__).resolve().parents[1]


def load_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def validator_for_example(relative: str) -> tuple[Draft202012Validator, dict]:
    instance = load_json(relative)
    schema_path = instance.pop("$schema_file")
    schema = load_json(schema_path)
    return Draft202012Validator(schema, format_checker=FormatChecker()), instance


class PublicContractTests(unittest.TestCase):
    def test_schemas_and_examples_validate(self):
        schemas = {}
        for path in sorted((ROOT / "schemas").glob("*.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            schemas[path.relative_to(ROOT).as_posix()] = schema

        self.assertEqual(len(schemas), 5)
        for path in sorted((ROOT / "examples").glob("*.json")):
            instance = json.loads(path.read_text(encoding="utf-8"))
            schema_path = instance.pop("$schema_file")
            self.assertIn(schema_path, schemas)
            Draft202012Validator(
                schemas[schema_path], format_checker=FormatChecker()
            ).validate(instance)

    def test_evaluation_rejects_same_producer_and_evaluator(self):
        validator, instance = validator_for_example("examples/evaluation-v3.json")
        instance["participants"] = ["same-agent", "same-agent"]
        with self.assertRaises(ValidationError):
            validator.validate(instance)

    def test_pass_rejects_failed_guardrail(self):
        validator, instance = validator_for_example("examples/evaluation-v3.json")
        instance["guardrails"][0]["passed"] = False
        with self.assertRaises(ValidationError):
            validator.validate(instance)

    def test_pass_rejects_explicit_missingness(self):
        validator, instance = validator_for_example("examples/evaluation-v3.json")
        instance["missingness"] = ["treatment sample 12 unavailable"]
        with self.assertRaises(ValidationError):
            validator.validate(instance)

    def test_pass_rejects_null_metric_values_in_any_order(self):
        validator, original = validator_for_example("examples/evaluation-v3.json")
        second = {
            "name": "first_pass_review_rate",
            "unit": "ratio",
            "baseline": 0.8,
            "treatment": 0.9,
        }
        for position in (0, 1):
            for field in ("baseline", "treatment"):
                instance = copy.deepcopy(original)
                instance["metrics"].append(copy.deepcopy(second))
                instance["metrics"][position][field] = None
                with self.subTest(position=position, field=field):
                    with self.assertRaises(ValidationError):
                        validator.validate(instance)

        instance = copy.deepcopy(original)
        instance["metrics"] = [copy.deepcopy(second), *instance["metrics"]]
        instance["metrics"][0]["treatment"] = None
        instance["metrics"][1]["baseline"] = None
        with self.assertRaises(ValidationError):
            validator.validate(instance)

        for outcome in ("fail", "blocked", "inconclusive", "regression"):
            instance = copy.deepcopy(original)
            instance["outcome"] = outcome
            instance["metrics"][0]["baseline"] = None
            with self.subTest(outcome=outcome):
                validator.validate(instance)

    def test_target_requires_bounded_predeclared_stop_rules(self):
        validator, instance = validator_for_example("examples/target-v2.json")
        missing = copy.deepcopy(instance)
        missing.pop("stop_rules")
        with self.assertRaises(ValidationError):
            validator.validate(missing)

        instance["stop_rules"] *= 11
        with self.assertRaises(ValidationError):
            validator.validate(instance)

    def test_every_schema_id_and_shape_is_version_immutable(self):
        for path in sorted((ROOT / "schemas").glob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            version = re.search(r"-v([0-9]+)\.schema\.json$", path.name).group(1)
            self.assertTrue(schema["$id"].endswith(path.name))
            self.assertTrue(schema["properties"]["schema"]["const"].endswith(f".v{version}"))
            self.assertFalse(schema["additionalProperties"])

        policy = (ROOT / "docs/CONTRACTS.md").read_text(encoding="utf-8")
        self.assertIn("Every published schema identifier and file is immutable", policy)
        self.assertIn("Any field addition", policy)

    def test_public_artifact_scan_covers_root_and_dotfiles(self):
        self.assertEqual(scan(ROOT), [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("host=" + "chi" + "ap99", encoding="utf-8")
            findings = scan(root)
            self.assertEqual(len(findings), 1)
            self.assertTrue(findings[0].startswith("README.md:"))

            (root / "README.md").write_text("public", encoding="utf-8")
            (root / ".public-config").write_text("path=/" + "home/private", encoding="utf-8")
            findings = scan(root)
            self.assertEqual(len(findings), 1)
            self.assertTrue(findings[0].startswith(".public-config:"))

    def test_markdown_links_resolve(self):
        link = re.compile(r"\[[^]]+\]\((?!https?://)([^)#]+)(?:#[^)]+)?\)")
        for path in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
            for target in link.findall(path.read_text(encoding="utf-8")):
                self.assertTrue((path.parent / target).resolve().exists(), f"{path}: {target}")

    def test_v2_to_v3_migration_matches_executable_fixture(self):
        source = load_json("examples/migration/evaluation-v2-null-pass.input.json")
        expected = load_json("examples/migration/evaluation-v2-null-pass.output.json")
        self.assertEqual(migrate(source), expected)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.json"
            subprocess.run(
                [sys.executable, "scripts/migrate_evaluation_v2_to_v3.py", "examples/migration/evaluation-v2-null-pass.input.json", str(output)],
                cwd=ROOT,
                check=True,
            )
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), expected)

    def test_v2_to_v3_maps_every_field_and_null_in_stable_order(self):
        source = load_json("examples/migration/evaluation-v2-null-pass.input.json")
        source["metrics"] = [
            {"name": "quality", "unit": "ratio", "baseline": None, "treatment": 0.9},
            {"name": "latency", "unit": "ms", "baseline": 10, "treatment": None},
        ]
        result = migrate(source)
        for field in set(source) - {"schema", "outcome", "missingness"}:
            self.assertEqual(result[field], source[field])
        self.assertEqual(result["schema"], "skrsi.evaluation.v3")
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(
            result["missingness"],
            ["metrics[0].baseline is null", "metrics[1].treatment is null"],
        )

    def test_v2_to_v3_fails_closed_on_invalid_or_ambiguous_input(self):
        valid = load_json("examples/migration/evaluation-v2-null-pass.input.json")
        cases = []
        extra = copy.deepcopy(valid)
        extra["unknown"] = True
        cases.append(extra)
        failed_guardrail = copy.deepcopy(valid)
        failed_guardrail["metrics"][0]["treatment"] = 1
        failed_guardrail["guardrails"][0]["passed"] = False
        cases.append(failed_guardrail)
        explicit_missingness = copy.deepcopy(valid)
        explicit_missingness["metrics"][0]["treatment"] = 1
        explicit_missingness["missingness"] = ["ambiguous"]
        cases.append(explicit_missingness)
        nonfinite = copy.deepcopy(valid)
        nonfinite["metrics"][0]["treatment"] = float("nan")
        cases.append(nonfinite)
        for index, document in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    migrate(document)

    def test_v3_migration_is_idempotent_and_never_invents_pass(self):
        v2 = load_json("examples/migration/evaluation-v2-null-pass.input.json")
        first = migrate(v2)
        self.assertNotEqual(first["outcome"], "pass")
        self.assertEqual(migrate(first), first)

    def test_converter_enforces_every_bounded_schema_field(self):
        source = load_json("examples/migration/evaluation-v2-null-pass.input.json")
        validator = Draft202012Validator(
            load_json("schemas/evaluation-v3.schema.json"),
            format_checker=FormatChecker(),
        )
        cases = [
            ("evaluation_id", "aa", False),
            ("evaluation_id", "aaa", True),
            ("evaluation_id", "a" * 128, True),
            ("evaluation_id", "a" * 129, False),
            ("target_ref", "target@0", False),
            ("target_ref", "target@1", True),
            ("participant", "", False),
            ("participant", "a", True),
            ("participant", "a" * 128, True),
            ("participant", "a" * 129, False),
            ("participants", ["one"], False),
            ("participants", ["one", "two"], True),
            ("participants", ["one", "two", "three"], False),
            ("sample_size", -1, False),
            ("sample_size", 0, True),
            ("metrics", [], False),
            ("metrics", copy.deepcopy(source["metrics"]), True),
            ("guardrails", [], False),
            ("guardrails", copy.deepcopy(source["guardrails"]), True),
            ("missingness_item", "", False),
            ("missingness_item", "x", True),
            ("evidence_sha256", "a" * 63, False),
            ("evidence_sha256", "a" * 64, True),
            ("evidence_sha256", "a" * 65, False),
            ("outcome", "unknown", False),
            ("window_start", "2026-01-01", False),
        ]
        for field, value, accepted in cases:
            document = copy.deepcopy(source)
            if field == "participant":
                document["participants"][0] = value
            elif field == "sample_size":
                document["baseline"]["sample_size"] = value
            elif field == "missingness_item":
                document["outcome"] = "fail"
                document["missingness"] = [value]
            elif field == "window_start":
                document["baseline"]["window_start"] = value
            else:
                document[field] = value
            detail = len(value) if hasattr(value, "__len__") else value
            with self.subTest(field=field, boundary=detail):
                if accepted:
                    validator.validate(migrate(document))
                else:
                    with self.assertRaises(ValueError):
                        migrate(document)

    def test_every_successful_conversion_validates_against_v3(self):
        source = load_json("examples/migration/evaluation-v2-null-pass.input.json")
        validator = Draft202012Validator(
            load_json("schemas/evaluation-v3.schema.json"),
            format_checker=FormatChecker(),
        )
        corpus = []
        for outcome in ("pass", "fail", "blocked", "inconclusive", "regression"):
            document = copy.deepcopy(source)
            document["outcome"] = outcome
            if outcome != "pass":
                document["missingness"] = ["synthetic missing value"]
            corpus.append(document)
        for evaluation_id in ("abc", "a" * 128):
            document = copy.deepcopy(source)
            document["evaluation_id"] = evaluation_id
            corpus.append(document)
        for length in (1, 128):
            document = copy.deepcopy(source)
            document["participants"] = ["p" * length, "e" * length]
            corpus.append(document)

        for index, document in enumerate(corpus):
            with self.subTest(index=index):
                validator.validate(migrate(document))


if __name__ == "__main__":
    unittest.main()
