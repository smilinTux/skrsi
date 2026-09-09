import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]


class PublicContractTests(unittest.TestCase):
    def test_schemas_and_examples_validate(self):
        schemas = {}
        for path in sorted((ROOT / "schemas").glob("*.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            schemas[path.relative_to(ROOT).as_posix()] = schema

        self.assertEqual(len(schemas), 4)
        for path in sorted((ROOT / "examples").glob("*.json")):
            instance = json.loads(path.read_text(encoding="utf-8"))
            schema_path = instance.pop("$schema_file")
            self.assertIn(schema_path, schemas)
            Draft202012Validator(
                schemas[schema_path], format_checker=FormatChecker()
            ).validate(instance)

    def test_examples_are_synthetic_and_sanitized(self):
        forbidden = re.compile(
            r"(?:/home/|/mnt/|chiap[0-9]+|GH_TOKEN|api-keys|sklegal)", re.IGNORECASE
        )
        for directory in ("examples", "schemas", "docs"):
            for path in (ROOT / directory).rglob("*"):
                if path.is_file():
                    self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")), path)

    def test_markdown_links_resolve(self):
        link = re.compile(r"\[[^]]+\]\((?!https?://)([^)#]+)(?:#[^)]+)?\)")
        for path in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
            for target in link.findall(path.read_text(encoding="utf-8")):
                self.assertTrue((path.parent / target).resolve().exists(), f"{path}: {target}")

    def test_evaluator_is_independent(self):
        document = json.loads((ROOT / "examples/evaluation-v1.json").read_text())
        self.assertNotEqual(document["producer"], document["evaluator"])


if __name__ == "__main__":
    unittest.main()
