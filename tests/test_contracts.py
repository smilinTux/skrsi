import copy
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.validate_public import lesson_errors, load_lesson_tags, scan
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
        for path in sorted((ROOT / "schemas").glob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            schemas[path.relative_to(ROOT).as_posix()] = schema

        self.assertEqual(len(schemas), 8)
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


class LessonContractTests(unittest.TestCase):
    def lesson(self) -> dict:
        instance = load_json("examples/lesson-v1.json")
        instance.pop("$schema_file")
        return instance

    def assertRejected(self, instance: dict, needle: str, schema_too: bool = True) -> None:
        errors = lesson_errors(instance)
        self.assertTrue(errors, "lesson was accepted")
        self.assertTrue(any(needle in error for error in errors), errors)
        if schema_too:
            schema = load_json("schemas/lesson-v1.schema.json")
            with self.assertRaises(ValidationError):
                Draft202012Validator(schema, format_checker=FormatChecker()).validate(instance)

    def test_example_lesson_validates(self):
        self.assertEqual(lesson_errors(self.lesson()), [])

    def test_validator_and_schema_agree_on_schema_rules(self):
        schema = Draft202012Validator(load_json("schemas/lesson-v1.schema.json"), format_checker=FormatChecker())
        base = self.lesson()
        cases = [
            ("id", "lesson-ab"), ("id", "lesson-abc"), ("id", "note-abc"),
            ("title", ""), ("title", "x" * 120), ("title", "x" * 121), ("title", "a\nb"),
            ("service", []), ("service", ["skone"]), ("service", ["other"]), ("service", ["skone", "skone"]),
            ("tags", []), ("tags", ["apply-gate"] * 2), ("tags", ["Apply"]),
            ("class", "defect"), ("class", "style"),
            ("evidence", [{"kind": "pr", "ref": "a#1"}]), ("evidence", [{"kind": "chat", "ref": "a"}]),
            ("evidence", [{"kind": "pr", "ref": "has space"}]), ("evidence", [{"kind": "pr"}]),
            ("evidence", [{"kind": "pr", "ref": "a", "note": "x"}]), ("evidence", ["a#1"]),
            ("rule", ""), ("rule", "word"), ("rule", "two\nlines"),
            ("check", "none yet"), ("check", ""), ("status", "active"), ("visibility", "internal"),
            ("schema", "skrsi.lesson.v2"), ("type", "postmortem"),
        ]
        for field, value in cases:
            instance = copy.deepcopy(base)
            instance[field] = value
            schema_ok = schema.is_valid(instance)
            with self.subTest(field=field, value=value):
                self.assertEqual(lesson_errors(instance) == [], schema_ok)

    def test_schema_has_spec_fields_plus_visibility_and_no_recurrence(self):
        schema = load_json("schemas/lesson-v1.schema.json")
        expected = {
            "schema", "id", "type", "title", "service", "tags", "class",
            "evidence", "rule", "check", "status", "visibility",
        }
        envelope = {"namespace", "lifecycle", "edges", "created", "updated", "state_refs"}
        self.assertEqual(set(schema["properties"]), expected | envelope)
        for excluded in ("origin", "source_repo", "source_path"):
            self.assertNotIn(excluded, schema["properties"])
        self.assertEqual(set(schema["required"]), expected)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["visibility"]["enum"], ["public", "private"])
        self.assertNotIn("recurrence", schema["properties"])

    def test_rejects_rule_over_40_words(self):
        instance = self.lesson()
        instance["rule"] = " ".join(["word"] * 41)
        self.assertRejected(instance, "rule")
        instance["rule"] = " ".join(["word"] * 40)
        self.assertEqual(lesson_errors(instance), [])

    def test_rejects_missing_or_empty_evidence(self):
        instance = self.lesson()
        instance["evidence"] = []
        self.assertRejected(instance, "evidence")
        instance.pop("evidence")
        self.assertRejected(instance, "evidence")

    def test_rejects_unknown_status(self):
        instance = self.lesson()
        instance["status"] = "archived"
        self.assertRejected(instance, "status")

    def test_rejects_unknown_tag(self):
        instance = self.lesson()
        instance["tags"] = ["not-in-vocabulary"]
        self.assertRejected(instance, "not-in-vocabulary", schema_too=False)

    def test_rejects_fallback_class_as_lesson_tag(self):
        instance = self.lesson()
        instance["tags"] = [load_lesson_tags()["fallback"]]
        self.assertRejected(instance, "unknown tag", schema_too=False)

    def test_rejects_authored_recurrence(self):
        instance = self.lesson()
        instance["recurrence"] = 3
        self.assertRejected(instance, "recurrence")

    def test_enforced_lesson_names_its_check(self):
        instance = self.lesson()
        instance["status"] = "enforced"
        instance["check"] = "none yet"
        self.assertRejected(instance, "check")

    def test_rejects_private_host_in_lesson_text(self):
        instance = self.lesson()
        instance["title"] = "seen on " + "chi" + "ap99"
        self.assertRejected(instance, "title", schema_too=False)

    def envelope(self) -> dict:
        instance = self.lesson()
        instance.update({
            "namespace": "ops",
            "lifecycle": "canon",
            "edges": [
                {"target": "service-skexample", "type": "touches"},
                {"target": "ke-example-gate-scope"},
            ],
            "created": "2026-11-12",
            "updated": "2026-11-12",
            "state_refs": {"kedb": "ke-example-gate-scope", "actions": ["restart-example", "rerun-gate"]},
        })
        return instance

    def test_canon_envelope_fields_are_optional_and_validate(self):
        schema = Draft202012Validator(load_json("schemas/lesson-v1.schema.json"), format_checker=FormatChecker())
        instance = self.envelope()
        self.assertEqual(lesson_errors(instance), [])
        self.assertTrue(schema.is_valid(instance), [e.message for e in schema.iter_errors(instance)])
        for field in ("namespace", "lifecycle", "edges", "created", "updated", "state_refs"):
            partial = self.envelope()
            partial.pop(field)
            with self.subTest(missing=field):
                self.assertEqual(lesson_errors(partial), [])
                self.assertTrue(schema.is_valid(partial))

    def test_validator_and_schema_agree_on_envelope_fields(self):
        schema = Draft202012Validator(load_json("schemas/lesson-v1.schema.json"), format_checker=FormatChecker())
        base = self.envelope()
        cases = [
            ("namespace", "ops"), ("namespace", ""), ("namespace", "Ops"), ("namespace", "ops\n"),
            ("lifecycle", "draft"), ("lifecycle", "reviewed"), ("lifecycle", "published"),
            ("edges", []), ("edges", [{"target": "node-example-1", "type": "runs_on"}]),
            ("edges", [{"type": "touches"}]), ("edges", [{"target": "a", "type": "touches", "weight": 1}]),
            ("edges", ["service-skexample:touches"]), ("edges", "service-skexample"),
            ("edges", [{"target": "has space"}]), ("edges", [{"target": "a\n"}]),
            ("edges", [{"target": "a", "type": "Touches"}]), ("edges", [{"target": "a", "type": "touches\n"}]),
            ("created", "2026-11-12"), ("created", "2026-02-30"), ("created", "20261112"),
            ("created", "2026-11-12\n"), ("created", "2026-11-12T00:00:00Z"), ("updated", 20261112),
            ("state_refs", {}), ("state_refs", {"kedb": "ke-example-gate-scope"}),
            ("state_refs", {"kedb": "ke-2d21671f"}), ("state_refs", {"actions": ["restart-example"]}),
            ("state_refs", {"actions": []}), ("state_refs", {"kedb": "ke-a", "actions": ["rerun-gate", "purge-x"]}),
            ("state_refs", {"kedb": "ke-a", "incident": "inc-1"}), ("state_refs", {"kedb": "lesson-abc"}),
            ("state_refs", {"kedb": "ke-"}), ("state_refs", {"kedb": "KE-abc"}), ("state_refs", {"kedb": "ke-abc\n"}),
            ("state_refs", {"kedb": ["ke-abc"]}), ("state_refs", {"actions": ["rerun-gate", "rerun-gate"]}),
            ("state_refs", {"actions": ["rerun-gate\n"]}), ("state_refs", {"actions": ["Rerun_Gate"]}),
            ("state_refs", {"actions": "rerun-gate"}), ("state_refs", {"actions": ["a"] + [f"a{i}" for i in range(10)]}),
            ("state_refs", "ke-abc"), ("state_refs", []),
        ]
        for field, value in cases:
            instance = copy.deepcopy(base)
            instance[field] = value
            schema_ok = schema.is_valid(instance)
            with self.subTest(field=field, value=value):
                self.assertEqual(lesson_errors(instance) == [], schema_ok)

    def test_state_refs_rejections_named_in_review(self):
        cases = [
            ({"kedb": "ke-a", "incident": "inc-1"}, "state_refs"),
            ({"kedb": "postmortem-a"}, "state_refs.kedb"),
            ({"actions": ["rerun-gate", "rerun-gate"]}, "state_refs.actions"),
            ({"kedb": "ke-abc\n"}, "state_refs.kedb"),
            ({"actions": ["rerun-gate\n"]}, "state_refs.actions"),
        ]
        for value, needle in cases:
            instance = self.envelope()
            instance["state_refs"] = value
            with self.subTest(state_refs=value):
                self.assertRejected(instance, needle)

    def test_source_fields_are_not_part_of_the_lesson(self):
        for field, value in (("origin", "skbrain-ops"), ("source_repo", "skbrain-ops"),
                             ("source_path", "pages/lessons/lesson-x.md")):
            instance = self.envelope()
            instance[field] = value
            with self.subTest(field=field):
                self.assertRejected(instance, field)

    def test_service_names_are_tag_shaped(self):
        schema = Draft202012Validator(load_json("schemas/lesson-v1.schema.json"), format_checker=FormatChecker())
        for name, ok in (("sk-standards", True), ("sk-cron-run", True), ("cloud9", True), ("capauth", True),
                         ("SK_Bad", False), ("a--b", False)):
            instance = self.lesson()
            instance["service"] = [name]
            with self.subTest(service=name):
                self.assertEqual(lesson_errors(instance) == [], ok)
                self.assertEqual(schema.is_valid(instance), ok)

    def test_validator_and_schema_agree_on_trailing_newline(self):
        schema = Draft202012Validator(load_json("schemas/lesson-v1.schema.json"), format_checker=FormatChecker())
        cases = [
            ("title", "A title\n"), ("check", "tests/test_gate.py\n"), ("id", "lesson-abc\n"),
            ("service", ["skexample\n"]), ("tags", ["apply-gate\n"]),
            ("evidence", [{"kind": "pr", "ref": "example-org/example-repo#1\n"}]), ("rule", "One rule.\n"),
        ]
        for field, value in cases:
            instance = self.lesson()
            instance[field] = value
            with self.subTest(field=field):
                self.assertNotEqual(lesson_errors(instance), [])
                self.assertFalse(schema.is_valid(instance))

    def test_validator_and_schema_agree_on_key_reordered_duplicate_evidence(self):
        schema = Draft202012Validator(load_json("schemas/lesson-v1.schema.json"), format_checker=FormatChecker())
        instance = self.lesson()
        instance["evidence"] = [{"kind": "pr", "ref": "a#1"}, {"ref": "a#1", "kind": "pr"}]
        self.assertFalse(schema.is_valid(instance))
        self.assertTrue(any("unique" in error for error in lesson_errors(instance)), lesson_errors(instance))

    def test_private_text_floor_applies_to_public_lessons_only(self):
        instance = self.lesson()
        instance["rule"] = "Never write scratch files under " + "/" + "home/ in a shared checkout."
        instance["visibility"] = "private"
        self.assertEqual(lesson_errors(instance), [])
        instance["visibility"] = "public"
        self.assertTrue(any(error.startswith("rule: private") for error in lesson_errors(instance)))

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_public.py"), *args],
                              capture_output=True, text=True, cwd=ROOT)

    def test_cli_lesson_without_file_is_a_usage_error(self):
        result = self.run_cli("--lesson")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_lesson_file_that_is_not_an_object_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in (("list.json", "[1, 2]"), ("broken.json", "{not json")):
                path = Path(tmp) / name
                path.write_text(text, encoding="utf-8")
                result = self.run_cli("--lesson", str(path))
                with self.subTest(file=name):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(name, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_cli_validates_the_example(self):
        result = self.run_cli("--lesson", "examples/lesson-v1.json")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tag_vocabulary_checksum_is_recorded_in_contracts_doc(self):
        digest = hashlib.sha256((ROOT / "schemas" / "lesson-tags-v1.json").read_bytes()).hexdigest()
        contracts = (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
        self.assertIn(f"sha256:{digest}", contracts)
        self.assertIn("lesson-tags-v2", contracts)

    def test_changelog_records_lesson_contract(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = changelog.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
        self.assertIn("skrsi.lesson.v1", unreleased)
        self.assertIn("3c64c941", unreleased)

    def test_tag_vocabulary_is_one_simple_versioned_list(self):
        vocabulary = load_lesson_tags()
        self.assertEqual(set(vocabulary), {"schema", "fallback", "tags"})
        self.assertEqual(vocabulary["schema"], "skrsi.lesson-tags.v1")
        self.assertEqual(vocabulary["fallback"], "unclassified")
        self.assertNotIn(vocabulary["fallback"], vocabulary["tags"])
        self.assertTrue(8 <= len(vocabulary["tags"]) <= 24)
        tag_shape = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
        for tag, meaning in vocabulary["tags"].items():
            with self.subTest(tag=tag):
                self.assertRegex(tag, tag_shape)
                self.assertIsInstance(meaning, str)
                self.assertTrue(0 < len(meaning) <= 120)
                self.assertNotIn("\n", meaning)

    def test_tag_vocabulary_covers_the_p1_5_seed_lessons(self):
        # Card c2a1f223 (P1.5): tags for the seed lesson candidates, appended within v1.
        tags = load_lesson_tags()["tags"]
        for tag in ("disk-budget", "no-failover", "post-apply-check", "stale-evidence", "start-order",
                    "test-isolation", "turn-end-wait", "unalerted-failure", "unclaimed-work"):
            with self.subTest(tag=tag):
                self.assertIn(tag, tags)


if __name__ == "__main__":
    unittest.main()
