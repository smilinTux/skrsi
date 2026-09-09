#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any


FIELDS = {
    "schema", "evaluation_id", "target_ref", "participants", "baseline",
    "treatment", "metrics", "guardrails", "missingness", "outcome",
    "evidence_sha256",
}
OUTCOMES = {"pass", "fail", "blocked", "inconclusive", "regression"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: Any, field: str) -> None:
    _require(isinstance(value, str) and bool(value), f"{field} must be a nonempty string")


def _timestamp(value: Any, field: str) -> None:
    _text(value, field)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, f"{field} must include a timezone")


def _cohort(value: Any, field: str) -> None:
    _require(isinstance(value, dict), f"{field} must be an object")
    _require(set(value) == {"id", "sample_size", "window_start", "window_end"}, f"{field} fields are invalid")
    _text(value["id"], f"{field}.id")
    _require(isinstance(value["sample_size"], int) and not isinstance(value["sample_size"], bool) and value["sample_size"] >= 0, f"{field}.sample_size is invalid")
    _timestamp(value["window_start"], f"{field}.window_start")
    _timestamp(value["window_end"], f"{field}.window_end")


def validate(document: Any, version: int) -> None:
    _require(isinstance(document, dict), "evaluation must be an object")
    _require(set(document) == FIELDS, "evaluation fields are missing or ambiguous")
    _require(document["schema"] == f"skrsi.evaluation.v{version}", "schema discriminator is invalid")
    _text(document["evaluation_id"], "evaluation_id")
    _require(isinstance(document["target_ref"], str) and re.fullmatch(r"[a-z0-9._-]+@[1-9][0-9]*", document["target_ref"]) is not None, "target_ref is invalid")

    participants = document["participants"]
    _require(isinstance(participants, list) and len(participants) == 2, "participants must contain producer and evaluator")
    _text(participants[0], "participants[0]")
    _text(participants[1], "participants[1]")
    _require(participants[0] != participants[1], "producer and evaluator must differ")
    _cohort(document["baseline"], "baseline")
    _cohort(document["treatment"], "treatment")

    metrics = document["metrics"]
    _require(isinstance(metrics, list) and bool(metrics), "metrics must be a nonempty array")
    for index, metric in enumerate(metrics):
        _require(isinstance(metric, dict) and set(metric) == {"name", "unit", "baseline", "treatment"}, f"metrics[{index}] fields are invalid")
        _text(metric["name"], f"metrics[{index}].name")
        _text(metric["unit"], f"metrics[{index}].unit")
        for field in ("baseline", "treatment"):
            value = metric[field]
            _require(value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)), f"metrics[{index}].{field} is invalid")

    guardrails = document["guardrails"]
    _require(isinstance(guardrails, list) and bool(guardrails), "guardrails must be a nonempty array")
    for index, guardrail in enumerate(guardrails):
        _require(isinstance(guardrail, dict) and set(guardrail) == {"name", "passed"}, f"guardrails[{index}] fields are invalid")
        _text(guardrail["name"], f"guardrails[{index}].name")
        _require(isinstance(guardrail["passed"], bool), f"guardrails[{index}].passed is invalid")

    missingness = document["missingness"]
    _require(isinstance(missingness, list) and all(isinstance(item, str) and item for item in missingness), "missingness is invalid")
    _require(document["outcome"] in OUTCOMES, "outcome is invalid")
    _require(isinstance(document["evidence_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", document["evidence_sha256"]) is not None, "evidence_sha256 is invalid")
    if document["outcome"] == "pass":
        _require(all(item["passed"] for item in guardrails), "PASS cannot contain a failed guardrail")
        _require(not missingness, "PASS cannot contain explicit missingness")
        if version == 3:
            _require(all(metric["baseline"] is not None and metric["treatment"] is not None for metric in metrics), "v3 PASS cannot contain null metric values")


def migrate(document: Any) -> dict[str, Any]:
    schema = document.get("schema") if isinstance(document, dict) else None
    if schema == "skrsi.evaluation.v3":
        validate(document, 3)
        return copy.deepcopy(document)
    _require(schema == "skrsi.evaluation.v2", "only evaluation v2 or v3 is supported")
    validate(document, 2)

    result = copy.deepcopy(document)
    result["schema"] = "skrsi.evaluation.v3"
    nulls = [
        f"metrics[{index}].{field} is null"
        for index, metric in enumerate(result["metrics"])
        for field in ("baseline", "treatment")
        if metric[field] is None
    ]
    if result["outcome"] == "pass" and nulls:
        result["outcome"] = "inconclusive"
        result["missingness"] = list(dict.fromkeys([*result["missingness"], *nulls]))
    validate(result, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate one SKRSI evaluation v2 JSON record to v3")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    document = json.loads(args.input.read_text(encoding="utf-8"))
    migrated = migrate(document)
    args.output.write_text(json.dumps(migrated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
