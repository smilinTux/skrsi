# Changelog

All notable changes follow Keep a Changelog and Semantic Versioning.

## [Unreleased]

### Added

- Card `3611ea28` (P0.1): `scripts/baseline_{sessions,gateway,cron,github,cardstore,itil,skmemory,skwhisper}.py` and
  `baseline_common.py`. Each recomputes baseline numbers from its source (read-only, stdlib, window end date as an
  argument) and prints one JSON aggregate. `tests/test_baseline_scripts.py` pins them to hand-computed fixtures and
  scans their output for content, addresses and host-shaped names. Documented in `docs/ADOPTER-GUIDE.md`.
- Card `7aa8e01f` (P1.7): the framework and instance model. `docs/INSTANCE-MODEL.md`; `skrsi.cluster.v1`
  (`schemas/cluster-v1.schema.json`) for an instance `cluster.yaml`; the instance fixture `templates/instance-repo/`
  (`CONFIG_SCHEMA` 1, synthetic `cluster.yaml.example`, `baselines/`, `evidence/`, `evaluations/`, a submodule
  example and an instance CI workflow); `scripts/validate_instance.py`, which uses the `cluster.yaml` node names as
  the allowlist and rejects addresses, domains, home paths, undeclared nodes, misplaced evidence and content-like
  fields (also `--scrub` for public trees); its stdlib helpers `scripts/cluster_config.py` (strict YAML subset loader
  and normative cluster validator) and `scripts/instance_text.py` (address, domain, path and host-name detectors);
  `scripts/generate-config-template.sh <output-dir>`; and the advisory `instance-template.yml` drift and scrub
  workflow. `requirements-dev.txt` adds PyYAML for the tests only.
- Card `3c64c941` (P1.1): `skrsi.lesson.v1` (`schemas/lesson-v1.schema.json`), the front matter of a canon `lesson`
  page, with optional canon envelope fields (`namespace`, `lifecycle`, `edges`, `created`, `updated`,
  `state_refs`); the shared tag vocabulary `schemas/lesson-tags-v1.json` (append-only within v1, pinned by sha256
  in `docs/CONTRACTS.md`); a synthetic example; and `lesson_errors`, the stdlib-only normative validator (`validate_public.py --lesson FILE...`).
  No `recurrence` field: recurrence is counted in evidence records, never authored.
- `templates/merge.sh`: merge a reviewed PR without a board, with the same gates as `land.sh`.
- `templates/remote-test.sh`: generic remote Rust build and test runner (rsync, shared target directory, throwaway
  tmpfs Postgres on a non-default loopback port, disk floor). Host, ports, image and env are parameters.

### Changed

- `templates/land.sh`: the check rollup is deduplicated to the latest run per (workflow, check name), so cancelled
  duplicates from re-runs or retargets no longer abort a land; waits for the reported head to equal the reviewed head
  after a push (`HEAD_WAIT_POLLS`); evidence also accepts cargo `test result: ok. N passed; 0 failed` (default
  `EVIDENCE_REGEX` now requires at least one pass); waits for CI on the merged commit (exit 7); retries the accept hook
  once against the merged commit when change lines are identical; `LAND_READBACK_CMD` reads the accepted state back
  (exit 9); a bare repo name is accepted with `LANE_REPO_OWNER`.
- Worker brief: Rust lane gates, `AGENTS.md` only, squash-merge rebase recipe. Review brief: remote runner item,
  vacuous dual-engine leg check, tests must fail without the fix, dash rule.
- `docs/PLAYBOOK-LANES.md`: landing kit table and the lessons behind the new gates.

### Documentation

- Card `36e7cca0`: `docs/ARCHITECTURE.md` gains the Start here section and the four diagrams required by
  sk-standards `ARCHITECTURE_AND_DATAFLOW_STANDARD` (context, components, data flow with classification and posture,
  PR evaluation sequence), and invariant 7: SKRSI never selects what a final gate runs. The case study records the
  skmemory live evaluations and the planned skstacks adoption. README lists adopters and links the site.

### Fixed

- Card `b31ab30b`, from the first real adopter: a stop rule bound to an unmeasured metric no longer records a passed
  guardrail; commit labels are kept in cohort ids and covered by `evidence_sha256`, so evaluations differ per commit
  (the golden examples change only in `evaluation_id` and `evidence_sha256`); notes are covered by the digest; a metric
  only the head measured is shown rather than called unmeasured; a non-string cohort id is malformed; a failed bench is named in the record and
  leads the comment, and its partial output is discarded; checkouts no longer persist a token while PR code runs.

### Changed

- The PR comment leads with what moved and folds repeated missingness. The record still holds every entry.

### Added

- Optional per-metric `noise` on the base side of a metrics file: a delta within it is `flat` regardless of tolerance; recorded in the v4 metric row. Adopter guide, playbook pitfall and worker brief updated.
- `docs/CASE-STUDY-SKMEMORY-RETRIEVAL.md`, adopter guidance on getting signal (per-class metrics, margin metrics, relative
  bench paths, live evidence) and `build_evaluation.py --note`.
- Review rounds are recorded as `review_round` links; build telemetry reports review metrics as missing unless every round is recorded and a target can state `min_sample_size`.
- Card `d8aecd58` (SKRSI-02): publish evaluation v4 (`evidence_class`, `instrument`; `pass` impossible for
  synthetic evidence or a non-production instrument), `scripts/build_evaluation.py`, the advisory reusable
  `evaluate.yml` workflow, `scripts/build_telemetry.py` with the `sk-build-quality` target, and the lane
  playbook with worker, review and landing templates. No merge gate is added.
- Card `859124d5`: publish sanitized versioned target, event, evaluation, and
  feedback handoff contracts with examples, adopter guidance, conformance
  checks, and pinned documentation CI.
- Record three explicitly noncanonical SELF naming alternatives while keeping
  Systematic Evaluation, Learning, and Feedback authoritative.
- Correct the first independent review findings with immutable target and
  evaluation v2 schemas, schema-level safety invariants, bounded stop rules,
  and repository-wide public artifact sanitization.
- Preserve evaluation v2 unchanged and publish evaluation v3 so PASS rejects
  null baseline or treatment values across every required metric.
- Add a standard-library, fail-closed evaluation v2-to-v3 migration with an
  executable synthetic fixture, exact field mapping, and idempotence tests.

## [0.1.0] - 2026-09-09

### Added

- Initialized the SKRSI documentation project from `sk-standards` requirements.
- Defined SKRSI as SK Recursive SELF Improvement.
- Defined SELF as Systematic Evaluation, Learning, and Feedback.
- Documented responsibility, evidence, authorization, review, and rollback boundaries.
