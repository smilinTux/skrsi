# Changelog

All notable changes follow Keep a Changelog and Semantic Versioning.

## [Unreleased]

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
