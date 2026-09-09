import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.validate_public import scan


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


if __name__ == "__main__":
    unittest.main()
