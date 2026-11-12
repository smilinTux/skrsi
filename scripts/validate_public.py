#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache"}
FORBIDDEN = (
    re.compile("/" + "home/", re.IGNORECASE),
    re.compile("/" + "mnt/", re.IGNORECASE),
    re.compile(r"chi" + r"ap[0-9]+", re.IGNORECASE),
    re.compile(r"[a-z]:\\" + r"users\\", re.IGNORECASE),
    re.compile("GH" + "_TOKEN", re.IGNORECASE),
    re.compile("api" + "-keys", re.IGNORECASE),
)


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in FORBIDDEN:
            if pattern.search(text):
                findings.append(f"{path.relative_to(root)}: {pattern.pattern}")
    return findings


REPO = Path(__file__).resolve().parents[1]
LESSON_SCHEMA = REPO / "schemas" / "lesson-v1.schema.json"
LESSON_TAGS = REPO / "schemas" / "lesson-tags-v1.json"


def load_lesson_tags() -> dict:
    """The one tag vocabulary shared by lessons and review failure classes."""
    return json.loads(LESSON_TAGS.read_text(encoding="utf-8"))


def _strings(value, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{path}.{key}" if path else key)


LESSON_FIELDS = (
    "schema", "id", "type", "title", "service", "tags", "class",
    "evidence", "rule", "check", "status", "visibility",
)
LESSON_ENUMS = {
    "class": ("defect", "efficiency", "reliability", "process"),
    "status": ("proposed", "active", "enforced", "retired"),
    "visibility": ("public", "private"),
}
EVIDENCE_KINDS = ("pr", "review", "incident", "problem", "card", "artifact", "evaluation")
LESSON_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,95}")
SERVICE = re.compile(r"sk[a-z0-9]+(?:-[a-z0-9]+)*")
TAG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
EVIDENCE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#@-]{0,199}")


def _line(value, max_length: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= max_length and "\n" not in value


def _name_list(errors: list[str], field: str, value, shape: re.Pattern, max_items: int) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= max_items:
        errors.append(f"{field}: must be a list of 1 to {max_items} items")
        return
    if len(set(map(repr, value))) != len(value):
        errors.append(f"{field}: items must be unique")
    for item in value:
        if not (isinstance(item, str) and len(item) <= 64 and shape.fullmatch(item)):
            errors.append(f"{field}: malformed item {item!r}")


def lesson_errors(instance) -> list[str]:
    """Normative skrsi.lesson.v1 validator (stdlib only).

    Applies every rule of schemas/lesson-v1.schema.json, checks each tag against
    schemas/lesson-tags-v1.json, and rejects private or secret-shaped text in any
    field. Returns "<field>: <problem>" strings; an empty list means valid.
    """
    if not isinstance(instance, dict):
        return ["(record): must be an object"]
    errors: list[str] = []
    for field in sorted(set(instance) - set(LESSON_FIELDS)):
        reason = "recurrence is counted in evidence records, never authored" if field == "recurrence" else "unknown field"
        errors.append(f"{field}: {reason}")
    for field in LESSON_FIELDS:
        if field not in instance:
            errors.append(f"{field}: required field missing")
    get = instance.get
    if "schema" in instance and get("schema") != "skrsi.lesson.v1":
        errors.append("schema: must be skrsi.lesson.v1")
    if "id" in instance and not (isinstance(get("id"), str) and get("id").startswith("lesson-") and LESSON_ID.fullmatch(get("id")[7:])):
        errors.append("id: must match lesson-<slug>")
    if "type" in instance and get("type") != "lesson":
        errors.append("type: must be lesson")
    if "title" in instance and not _line(get("title"), 120):
        errors.append("title: must be one line of 1 to 120 characters")
    if "service" in instance:
        _name_list(errors, "service", get("service"), SERVICE, 8)
    if "tags" in instance:
        _name_list(errors, "tags", get("tags"), TAG, 5)
        known = load_lesson_tags()["tags"]
        for tag in get("tags") if isinstance(get("tags"), list) else []:
            if isinstance(tag, str) and tag not in known:
                errors.append(f"tags: unknown tag {tag!r} (not in {LESSON_TAGS.name})")
    for field, allowed in LESSON_ENUMS.items():
        if field in instance and get(field) not in allowed:
            errors.append(f"{field}: {get(field)!r} is not one of {', '.join(allowed)}")
    if "evidence" in instance:
        evidence = get("evidence")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 10:
            errors.append("evidence: must be a list of 1 to 10 links")
        else:
            if len(set(map(repr, evidence))) != len(evidence):
                errors.append("evidence: links must be unique")
            for index, link in enumerate(evidence):
                if not (isinstance(link, dict) and set(link) == {"kind", "ref"}
                        and link["kind"] in EVIDENCE_KINDS
                        and isinstance(link["ref"], str) and EVIDENCE_REF.fullmatch(link["ref"])):
                    errors.append(f"evidence[{index}]: must be {{kind, ref}} with kind in {', '.join(EVIDENCE_KINDS)}")
    if "rule" in instance:
        rule = get("rule")
        if not (isinstance(rule, str) and len(rule) <= 400 and "\n" not in rule and 1 <= len(rule.split()) <= 40):
            errors.append("rule: must be one line of 1 to 40 words")
    if "check" in instance and not _line(get("check"), 200):
        errors.append("check: must be one line of 1 to 200 characters")
    if get("status") == "enforced" and get("check") == "none yet":
        errors.append("check: an enforced lesson must name the check that enforces it")
    for field, text in _strings(instance, ""):
        for pattern in FORBIDDEN:
            if pattern.search(text):
                errors.append(f"{field}: private or secret-shaped text ({pattern.pattern})")
    return errors


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--lesson":
        failed = 0
        for name in sys.argv[2:]:
            instance = json.loads(Path(name).read_text(encoding="utf-8"))
            instance.pop("$schema_file", None)
            for error in lesson_errors(instance):
                print(f"{name}: {error}", file=sys.stderr)
                failed = 1
        if not failed:
            print("lesson validation passed")
        return failed
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    findings = scan(root)
    if findings:
        print("public artifact sanitization failed", file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
        return 1
    print("public artifact sanitization passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
