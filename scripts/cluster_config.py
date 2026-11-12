#!/usr/bin/env python3
"""cluster.yaml of a skrsi instance: a strict YAML subset loader and the stdlib
mirror of schemas/cluster-v1.schema.json. Stdlib only, so instance CI and the
publish step need no installs.

The subset is what cluster.yaml.example uses: block mappings, block sequences,
`- key: value` items, flow lists `[a, b]`, flow maps `{a: b}`, plain or quoted
scalars, integers, floats, true/false/null and `#` comments. Anything else
(anchors, tags, multi-line strings, tabs) is rejected, never guessed.
"""
from __future__ import annotations

import json
import math
import re

INT = re.compile(r"[-+]?[0-9]+")
FLOAT = re.compile(r"[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+][0-9]+)?")  # YAML 1.1, as PyYAML reads it
KEY = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_-]*):(?:\s+(.*))?$")
SLUG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:/[A-Za-z0-9][A-Za-z0-9_-]*)?")
TARGET_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,127}")
METRIC = re.compile(r"[a-z][a-z0-9_]{0,63}")
SCHEMA_ID = "skrsi.cluster.v1"
CONFIG_SCHEMA = 1


class YamlSubsetError(ValueError):
    """cluster.yaml uses YAML outside the supported subset."""


def _strip_comment(line: str) -> str:
    quote = None
    for index, char in enumerate(line):
        if quote:
            quote = None if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index].rstrip()
    return line.rstrip()


def _scalar(text: str, lineno: int):
    text = text.strip()
    if text.startswith("["):
        if not text.endswith("]"):
            raise YamlSubsetError(f"line {lineno}: unclosed flow list")
        inner = text[1:-1].strip()
        return [_scalar(item, lineno) for item in inner.split(",")] if inner else []
    if text.startswith("{"):
        if not text.endswith("}"):
            raise YamlSubsetError(f"line {lineno}: unclosed flow map")
        result = {}
        for item in filter(None, (part.strip() for part in text[1:-1].split(","))):
            match = KEY.match(item)
            if not match or match.group(1) in result:
                raise YamlSubsetError(f"line {lineno}: bad or duplicate flow map entry {item!r}")
            result[match.group(1)] = _scalar(match.group(2) or "", lineno)
        return result
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        if "\\" in text:
            raise YamlSubsetError(f"line {lineno}: escapes are not supported")
        return text[1:-1]
    if text and (text[0] in "&*!|>%@`[]{}," or text.startswith(("- ", "? "))):
        raise YamlSubsetError(f"line {lineno}: unsupported YAML {text!r}")
    lowered = text.lower()
    if lowered in ("", "~", "null"):
        return None
    if lowered in ("true", "false"):
        return lowered == "true"
    if INT.fullmatch(text):
        return int(text)
    if FLOAT.fullmatch(text):
        return float(text)
    return text


def _block(lines, i, indent):
    if lines[i][2].startswith("-") and lines[i][2][1:2] in ("", " "):
        return _sequence(lines, i, indent)
    return _mapping(lines, i, indent)


def _child(lines, i, indent, lineno, dash_ok):
    """The nested block after `key:` or `-`, or None when nothing is nested."""
    if i < len(lines) and (lines[i][1] > indent or (dash_ok and lines[i][1] == indent and lines[i][2].startswith("- "))):
        return _block(lines, i, lines[i][1])
    return None, i


def _sequence(lines, i, indent):
    result = []
    while i < len(lines) and lines[i][1] == indent and lines[i][2].startswith("-"):
        lineno, _, text = lines[i]
        rest = text[1:].lstrip()
        if not rest:
            value, i = _child(lines, i + 1, indent, lineno, False)
        elif KEY.match(rest) and not rest.startswith(("'", '"')):
            lines[i] = (lineno, indent + len(text) - len(rest), rest)
            value, i = _mapping(lines, i, lines[i][1])
        else:
            value, i = _scalar(rest, lineno), i + 1
        result.append(value)
    return result, i


def _mapping(lines, i, indent):
    result = {}
    while i < len(lines) and lines[i][1] == indent and not lines[i][2].startswith("- "):
        lineno, _, text = lines[i]
        match = KEY.match(text)
        if not match:
            raise YamlSubsetError(f"line {lineno}: expected 'key: value', found {text!r}")
        key = match.group(1)
        if key in result:
            raise YamlSubsetError(f"line {lineno}: duplicate key {key!r}")
        if match.group(2):
            result[key], i = _scalar(match.group(2), lineno), i + 1
        else:
            result[key], i = _child(lines, i + 1, indent, lineno, True)
    return result, i


def load_cluster_yaml(text: str):
    """Parse cluster.yaml text. Raises YamlSubsetError outside the subset."""
    lines = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlSubsetError(f"line {lineno}: tabs are not allowed in indentation")
        stripped = _strip_comment(raw)
        if stripped.strip() in ("---", "..."):
            raise YamlSubsetError(f"line {lineno}: one document only, no markers")
        if stripped.strip():
            lines.append((lineno, len(stripped) - len(stripped.lstrip()), stripped.strip()))
    if not lines:
        return None
    value, i = _block(lines, 0, lines[0][1])
    if i != len(lines):
        raise YamlSubsetError(f"line {lines[i][0]}: unexpected indentation")
    return value


def _slug(value, max_length: int, min_length: int = 1) -> bool:
    return isinstance(value, str) and min_length <= len(value) <= max_length and bool(SLUG.fullmatch(value))


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _items(errors, field, value, max_items, check, unique=False) -> None:
    if not isinstance(value, list) or len(value) > max_items:
        errors.append(f"{field}: must be a list of at most {max_items} items")
        return
    if unique and len({json.dumps(item, sort_keys=True, default=str) for item in value}) != len(value):
        errors.append(f"{field}: items must be unique")
    for index, item in enumerate(value):
        check(errors, f"{field}[{index}]", item)


def _node(errors, where, node) -> None:
    if not isinstance(node, dict) or set(node) != {"name", "role", "readers"}:
        errors.append(f"{where}: must have exactly name, role, readers")
        return
    if not _slug(node["name"], 32, 2):
        errors.append(f"{where}.name: must be a short name (lowercase letters, digits, inner hyphens, no dots)")
    if not _slug(node["role"], 32):
        errors.append(f"{where}.role: must be a lowercase slug")
    _items(errors, f"{where}.readers", node["readers"], 32,
           lambda e, w, r: _slug(r, 32) or e.append(f"{w}: must be a lowercase slug"), unique=True)


def _service(errors, where, service) -> None:
    if not (isinstance(service, dict) and set(service) == {"name", "repo"} and _slug(service["name"], 64)
            and isinstance(service["repo"], str) and len(service["repo"]) <= 128 and REPO.fullmatch(service["repo"])):
        errors.append(f"{where}: must be {{name, repo}} with a slug name and an owner/name repo")


def _override(errors, where, item) -> None:
    keys = set(item) if isinstance(item, dict) else set()
    if not ({"target_id", "metric", "threshold"} <= keys <= {"target_id", "metric", "threshold", "paused"}
            and isinstance(item["target_id"], str) and TARGET_ID.fullmatch(item["target_id"])
            and isinstance(item["metric"], str) and METRIC.fullmatch(item["metric"])
            and _number(item["threshold"]) and isinstance(item.get("paused", False), bool)):
        errors.append(f"{where}: must be {{target_id, metric, threshold, paused?}} with a numeric threshold")


def cluster_errors(cluster) -> list[str]:
    """Normative stdlib mirror of schemas/cluster-v1.schema.json. Empty means valid."""
    if not isinstance(cluster, dict):
        return ["(cluster): must be a mapping"]
    errors = [f"{key}: unknown field" for key in sorted(set(cluster) - {
        "schema", "config_schema", "cluster", "nodes", "services", "canon", "overrides"})]
    errors += [f"{key}: required field missing" for key in (
        "schema", "config_schema", "cluster", "nodes", "canon") if key not in cluster]
    if "schema" in cluster and cluster["schema"] != SCHEMA_ID:
        errors.append(f"schema: must be {SCHEMA_ID}")
    if "config_schema" in cluster and (cluster["config_schema"] != CONFIG_SCHEMA or isinstance(cluster["config_schema"], bool)):
        errors.append(f"config_schema: must be {CONFIG_SCHEMA}")
    if "cluster" in cluster and not _slug(cluster["cluster"], 32):
        errors.append("cluster: must be a lowercase slug of at most 32 characters")
    if "nodes" in cluster:
        if isinstance(cluster["nodes"], list) and not cluster["nodes"]:
            errors.append("nodes: must declare at least one node")
        _items(errors, "nodes", cluster["nodes"], 64, _node)
    if "services" in cluster:
        _items(errors, "services", cluster["services"], 64, _service, unique=True)
    if "canon" in cluster:
        canon = cluster["canon"]
        if not (isinstance(canon, dict) and set(canon) == {"repo", "namespace"} and isinstance(canon["repo"], str)
                and len(canon["repo"]) <= 128 and REPO.fullmatch(canon["repo"]) and _slug(canon["namespace"], 64)):
            errors.append("canon: must be {repo, namespace} with an owner/name repo and a slug namespace")
    if "overrides" in cluster:
        _items(errors, "overrides", cluster["overrides"], 256, _override)
    return errors


def node_names(cluster) -> list[str]:
    """Declared node short names, in order; the instance allowlist."""
    nodes = cluster.get("nodes") if isinstance(cluster, dict) else None
    return [n["name"] for n in nodes or [] if isinstance(n, dict) and isinstance(n.get("name"), str)]
