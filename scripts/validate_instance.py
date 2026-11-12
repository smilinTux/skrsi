#!/usr/bin/env python3
"""Validate a private skrsi-config-<cluster> instance, or scrub public trees.

Instance mode (instance CI, and the publish step before every push):

    validate_instance.py [--cluster FILE] INSTANCE_DIR

  * cluster.yaml passes the cluster-v1 rules, and its node short names are the
    only node names the instance may contain;
  * CONFIG_SCHEMA equals cluster.yaml config_schema and is supported here;
  * every file is UTF-8 text with no IPv4 or IPv6 address, no domain, no home or
    user path;
  * JSON and YAML records carry no content-like field (prompt, response, memory
    content, card or PR text, credentials), name no undeclared node, and a record
    naming a skrsi.* schema must be one an instance accepts, and valid;
  * evidence lives only at evidence/<declared node>/<YYYY-MM-DD>.json.
  The framework/ submodule is not walked: it is the public framework, scrubbed by
  its own CI.

Scrub mode (framework CI):

    validate_instance.py --scrub [--hostnames] PATH...

  See instance_text.scrub_errors. Real names come from the private
  SKRSI_ESTATE_DENYLIST environment variable, never from a public file.

Both modes print one line per violation to stderr and exit 1, or exit 0.
Stdlib only, offline and read-only: nothing here runs in a request or session.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_evaluation import Malformed, validate_v4  # noqa: E402
from cluster_config import CONFIG_SCHEMA, YamlSubsetError, cluster_errors, load_cluster_yaml, node_names  # noqa: E402
from instance_text import denylist_from_env, host_shaped, scrub_errors, text_findings  # noqa: E402

__all__ = ["instance_errors", "scrub_errors", "main"]

TOP_LEVEL_SKIP = {"framework", ".git"}  # only at the instance root: the pinned submodule and git itself
ROOT_FILES = {"CONFIG_SCHEMA", "cluster.yaml", ".gitignore", ".gitmodules", "README.md"}
RECORD_DIRS = {"baselines", "evidence", "evaluations"}
RECORD_SUFFIXES = (".json", ".yaml", ".yml")
CONTENT_TOKENS = {
    "prompt", "prompts", "response", "responses", "completion", "completions", "transcript", "transcripts",
    "message", "messages", "memory", "memories", "body", "title", "description", "content", "text",
    "comment", "comments", "secret", "secrets", "password", "passwords", "passwd", "passphrase", "pwd", "token",
    "tokens", "dsn", "credential", "credentials", "cookie", "authorization", "auth", "bearer", "apikey", "header",
    "headers", "query", "queries", "question", "questions", "answer", "answers", "input", "inputs", "output",
    "outputs", "notes", "summary", "snippet", "snippets", "excerpt", "excerpts", "stderr", "stdout", "log", "logs",
    "payload", "payloads", "sample", "samples",
}
TEXT_OF = {"card", "pr", "issue"}  # with one of TEXT_PARTS: card or PR text
TEXT_PARTS = {"name", "text", "title", "body", "description", "content"}
KEY_OF = {"api", "private", "access", "secret", "ssh"}  # with "key": a credential
NODE_KEYS = {"node", "nodes", "node_name", "nodename", "host", "hostname", "hosts", "source_node"}
EVIDENCE_NAME = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})\.json")
EVIDENCE_RULE = "evidence files must be evidence/<node>/<YYYY-MM-DD>.json with <node> declared in cluster.yaml"
YAML_SUFFIXES = (".yaml", ".yml", ".yaml.example", ".yml.example")


def _v4_errors(record) -> list[str]:
    try:
        validate_v4(record)
    except Malformed as exc:
        return [str(exc)]
    return []


RECORD_VALIDATORS = {"skrsi.evaluation.v4": _v4_errors, "skrsi.cluster.v1": cluster_errors}


def _key_tokens(key: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    return {token for token in re.split(r"[\s_.-]+", spaced.lower()) if token}


def _holds_string(value) -> bool:
    if isinstance(value, dict):
        return any(_holds_string(item) for item in value.values())
    if isinstance(value, list):
        return any(_holds_string(item) for item in value)
    return isinstance(value, str)


def _content_key(key: str, value) -> bool:
    """A content token in the key and a string in the value: a number cannot carry content."""
    tokens = _key_tokens(key)
    content = bool(tokens & CONTENT_TOKENS) or bool(tokens & TEXT_OF and tokens & TEXT_PARTS) \
        or ("key" in tokens and bool(tokens & KEY_OF))
    return content and _holds_string(value)


def _string_problems(text: str, nodes: set[str]) -> list[str]:
    """Rule 7 checks on one decoded string, so JSON escapes cannot hide anything."""
    return text_findings(text) + [f"host-shaped name {name!r} is not a node declared in cluster.yaml"
                                  for name in host_shaped(text, nodes)]


def _walk_record(value, path: str, nodes: set[str], check_nodes: bool, out: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            out.extend(_string_problems(str(key), nodes))
            if _content_key(str(key), item):
                out.append(f"content-like field {here!r}: no prompts, responses, memory content, "
                           "card or PR text, or credentials")
                continue
            if check_nodes and str(key).lower() in NODE_KEYS:
                names = [item] if isinstance(item, str) else item if isinstance(item, list) else []
                out.extend(f"{here}: node name {name!r} is not declared in cluster.yaml"
                           for name in names if isinstance(name, str) and name not in nodes)
            _walk_record(item, here, nodes, check_nodes, out)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_record(item, f"{path}[{index}]", nodes, check_nodes, out)
    elif isinstance(value, str):
        out.extend(_string_problems(value, nodes))


def _type_problem(parts: tuple[str, ...], cluster_name: str) -> list[str]:
    name = parts[-1]
    if parts[0] == ".github":
        ok = len(parts) == 3 and parts[1] == "workflows" and name.endswith((".yml", ".yaml"))
    elif len(parts) == 1:
        allowed = ROOT_FILES | {cluster_name} | ({".gitmodules.example"} if cluster_name.endswith(".example") else set())
        ok = name in allowed
    else:
        ok = parts[0] in RECORD_DIRS and (name.endswith(RECORD_SUFFIXES) or (name == "README.md" and len(parts) == 2))
    return [] if ok else ["file type or location not allowed: records are .json or .yaml under baselines/, "
                          "evidence/ or evaluations/, README.md only at the root or directly in those, workflows "
                          "only as .github/workflows/*.yml; the root holds only " + ", ".join(sorted(ROOT_FILES))]


def _instance_files(root: Path):
    """Files and symlinks, except the top-level framework/ and .git. Symlinks are not followed."""
    for path in sorted(root.rglob("*")):
        if path.relative_to(root).parts[0] not in TOP_LEVEL_SKIP and (path.is_symlink() or path.is_file()):
            yield path


def _load_cluster(path: Path, label: str, errors: list[str]) -> tuple[dict | None, set[str]]:
    if not path.is_file():
        errors.append(f"{label}: missing (copy cluster.yaml.example to cluster.yaml and fill it in)")
        return None, set()
    try:
        cluster = load_cluster_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YamlSubsetError) as exc:
        errors.append(f"{label}: cannot read: {exc}")
        return None, set()
    errors.extend(f"{label}: {problem}" for problem in cluster_errors(cluster))
    if not isinstance(cluster, dict):
        return None, set()
    names = node_names(cluster)
    errors.extend(f"{label}: node {name!r} is declared more than once"
                  for name in sorted({n for n in names if names.count(n) > 1}))
    return cluster, set(names)


def _check_config_schema(root: Path, cluster: dict | None, errors: list[str]) -> None:
    path = root / "CONFIG_SCHEMA"
    if not path.is_file():
        errors.append("CONFIG_SCHEMA: missing")
        return
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        errors.append(f"CONFIG_SCHEMA: must be a whole number, found {raw!r}")
        return
    version = int(raw)
    if version != CONFIG_SCHEMA:
        errors.append(f"CONFIG_SCHEMA: version {version} is not supported by this framework "
                      f"(supports {CONFIG_SCHEMA}); bump the framework submodule")
    if cluster is not None and cluster.get("config_schema") != version:
        errors.append(f"CONFIG_SCHEMA: {version} does not equal cluster.yaml config_schema "
                      f"{cluster.get('config_schema')!r}")


def _evidence_errors(parts: tuple[str, ...], nodes: set[str]) -> list[str]:
    if parts == ("evidence", "README.md"):
        return []
    if len(parts) != 3:
        return [EVIDENCE_RULE]
    problems = []
    if parts[1] not in nodes:
        problems.append(f"node {parts[1]!r} is not declared in cluster.yaml ({EVIDENCE_RULE})")
    match = EVIDENCE_NAME.fullmatch(parts[2])
    try:
        valid = bool(match) and bool(datetime.date.fromisoformat(match.group(1)))
    except ValueError:
        valid = False
    if not valid:
        problems.append(f"file name {parts[2]!r} is not a valid date ({EVIDENCE_RULE})")
    return problems


def _record(path: Path, parts: tuple[str, ...], text: str):
    if path.name.endswith(".json"):
        return json.loads(text)
    if path.name.endswith(YAML_SUFFIXES) and parts[0] != ".github":
        return load_cluster_yaml(text)
    return None


def _file_problems(path: Path, parts: tuple[str, ...], text: str, nodes: set[str], is_cluster: bool) -> list[str]:
    problems = _string_problems(text, nodes)
    try:
        record = _record(path, parts, text)
    except ValueError as exc:
        return problems + [f"cannot parse: {exc}"]
    if record is None:
        return problems
    _walk_record(record, "", nodes, not is_cluster, problems)
    if not isinstance(record, dict) or is_cluster:
        return problems
    schema_id = record.get("schema")
    if isinstance(schema_id, str) and schema_id.startswith("skrsi."):
        check = RECORD_VALIDATORS.get(schema_id)
        if check is None:
            problems.append(f"unknown schema {schema_id!r}: an instance accepts {', '.join(sorted(RECORD_VALIDATORS))}")
        else:
            problems.extend(f"{schema_id}: {problem}" for problem in check(record))
    if parts[0] == "evidence" and len(parts) == 3 and "node" in record and record["node"] != parts[1]:
        problems.append(f"node field {record['node']!r} does not match its path")
    return problems


def instance_errors(root: Path, cluster_file: Path | None = None) -> list[str]:
    """Every violation in an instance checkout, as "<relative path>: <problem>"."""
    root = Path(root).resolve()
    cluster_path = Path(cluster_file).resolve() if cluster_file else root / "cluster.yaml"
    try:
        label = cluster_path.relative_to(root).as_posix()
    except ValueError:
        label = cluster_path.name
    errors: list[str] = []
    cluster, nodes = _load_cluster(cluster_path, label, errors)
    _check_config_schema(root, cluster, errors)
    for path in _instance_files(root):
        rel, parts = path.relative_to(root).as_posix(), path.relative_to(root).parts
        problems = _type_problem(parts, cluster_path.name if cluster_path.parent == root else "cluster.yaml")
        problems += _evidence_errors(parts, nodes) if parts[0] == "evidence" else []
        if path.is_symlink():
            errors.append(f"{rel}: symlink not allowed; an instance holds plain files only")
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            problems.append("not UTF-8 text; an instance holds JSON, YAML and Markdown aggregates only")
        else:
            problems += _file_problems(path, parts, text, nodes, path == cluster_path)
        errors.extend(f"{rel}: {problem}" for problem in dict.fromkeys(problems))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a skrsi instance, or scrub public trees.")
    parser.add_argument("--cluster", type=Path, help="cluster file (default: INSTANCE_DIR/cluster.yaml)")
    parser.add_argument("--scrub", action="store_true", help="scrub public trees instead of validating an instance")
    parser.add_argument("--hostnames", action="store_true", help="with --scrub: also reject domains and host-shaped names")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    if args.scrub and args.paths and not args.cluster:
        errors = scrub_errors(args.paths, hostnames=args.hostnames, denylist=denylist_from_env())
        passed, failed = "scrub passed", "scrub failed"
    elif not args.scrub and len(args.paths) == 1 and not args.hostnames:
        errors = instance_errors(args.paths[0], args.cluster)
        passed, failed = "instance validation passed", "instance validation failed"
    else:
        parser.print_usage(sys.stderr)
        return 2
    if errors:
        print(failed, file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(passed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
