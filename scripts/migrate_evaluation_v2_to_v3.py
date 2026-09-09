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


ROOT = Path(__file__).resolve().parents[1]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _schema(version: int) -> dict[str, Any]:
    path = ROOT / "schemas" / f"evaluation-v{version}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _text(value: Any, field: str, rule: dict[str, Any]) -> None:
    _require(isinstance(value, str), f"{field} must be a string")
    _require(len(value) >= rule.get("minLength", 0), f"{field} is too short")
    if "maxLength" in rule:
        _require(len(value) <= rule["maxLength"], f"{field} is too long")
    if "pattern" in rule:
        _require(re.fullmatch(rule["pattern"], value) is not None, f"{field} has an invalid pattern")


def _timestamp(value: Any, field: str, rule: dict[str, Any]) -> None:
    _text(value, field, rule)
    _require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})", value) is not None, f"{field} must be an RFC 3339 date-time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, f"{field} must include a timezone")


def _cohort(value: Any, field: str, rule: dict[str, Any]) -> None:
    _require(isinstance(value, dict), f"{field} must be an object")
    _require(set(value) == set(rule["required"]), f"{field} fields are invalid")
    properties = rule["properties"]
    _text(value["id"], f"{field}.id", properties["id"])
    sample_size = value["sample_size"]
    _require(isinstance(sample_size, int) and not isinstance(sample_size, bool), f"{field}.sample_size is invalid")
    _require(sample_size >= properties["sample_size"].get("minimum", sample_size), f"{field}.sample_size is invalid")
    _timestamp(value["window_start"], f"{field}.window_start", properties["window_start"])
    _timestamp(value["window_end"], f"{field}.window_end", properties["window_end"])


def _array(value: Any, field: str, rule: dict[str, Any]) -> None:
    _require(isinstance(value, list), f"{field} must be an array")
    _require(len(value) >= rule.get("minItems", 0), f"{field} has too few items")
    if "maxItems" in rule:
        _require(len(value) <= rule["maxItems"], f"{field} has too many items")


def validate(document: Any, version: int) -> None:
    contract = _schema(version)
    properties = contract["properties"]
    _require(isinstance(document, dict), "evaluation must be an object")
    _require(set(document) == set(contract["required"]) == set(properties), "evaluation fields are missing or ambiguous")
    _require(document["schema"] == properties["schema"]["const"], "schema discriminator is invalid")
    _text(document["evaluation_id"], "evaluation_id", properties["evaluation_id"])
    _text(document["target_ref"], "target_ref", properties["target_ref"])

    participants = document["participants"]
    participant_rule = properties["participants"]
    _array(participants, "participants", participant_rule)
    _require(len(participants) == len(participant_rule["prefixItems"]), "participants shape is invalid")
    _text(participants[0], "participants[0]", participant_rule["prefixItems"][0])
    _text(participants[1], "participants[1]", participant_rule["prefixItems"][1])
    _require(participants[0] != participants[1], "producer and evaluator must differ")
    cohort_rule = contract["$defs"]["cohort"]
    _cohort(document["baseline"], "baseline", cohort_rule)
    _cohort(document["treatment"], "treatment", cohort_rule)

    metrics = document["metrics"]
    _array(metrics, "metrics", properties["metrics"])
    metric_rule = contract["$defs"]["metric"]
    for index, metric in enumerate(metrics):
        _require(isinstance(metric, dict) and set(metric) == set(metric_rule["required"]), f"metrics[{index}] fields are invalid")
        _text(metric["name"], f"metrics[{index}].name", metric_rule["properties"]["name"])
        _text(metric["unit"], f"metrics[{index}].unit", metric_rule["properties"]["unit"])
        for field in ("baseline", "treatment"):
            value = metric[field]
            _require(value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)), f"metrics[{index}].{field} is invalid")

    guardrails = document["guardrails"]
    guardrail_array_rule = properties["guardrails"]
    _array(guardrails, "guardrails", guardrail_array_rule)
    guardrail_rule = guardrail_array_rule["items"]
    for index, guardrail in enumerate(guardrails):
        _require(isinstance(guardrail, dict) and set(guardrail) == set(guardrail_rule["required"]), f"guardrails[{index}] fields are invalid")
        _text(guardrail["name"], f"guardrails[{index}].name", guardrail_rule["properties"]["name"])
        _require(isinstance(guardrail["passed"], bool), f"guardrails[{index}].passed is invalid")

    missingness = document["missingness"]
    _array(missingness, "missingness", properties["missingness"])
    for index, item in enumerate(missingness):
        _text(item, f"missingness[{index}]", properties["missingness"]["items"])
    _require(document["outcome"] in properties["outcome"]["enum"], "outcome is invalid")
    _text(document["evidence_sha256"], "evidence_sha256", properties["evidence_sha256"])
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
