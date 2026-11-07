# Public contract catalog

SKRSI publishes metadata-only contracts for evidence-first software lifecycle
improvement. Each JSON document declares its exact schema URI. A consumer must
reject unknown major versions and must not infer authorization from a valid
record.

| Contract | Schema | Purpose |
|---|---|---|
| Target | `schemas/target-v2.schema.json` | Registers a bounded improvement objective, measures, invariants, stop rules, expiry, and rollback owner. |
| Event envelope | `schemas/event-envelope-v1.schema.json` | Carries attributable metadata, lineage, hashes, and redaction class. |
| Evaluation v3 | `schemas/evaluation-v3.schema.json` | Records independent participants, complete PASS metrics, quality gates, missingness, and an evidence-bound outcome. |
| Evaluation v4 | `schemas/evaluation-v4.schema.json` | Evaluation v3 plus `evidence_class` and an `instrument` self-description; `pass` is impossible for synthetic evidence or when the instrument does not run production code. |
| Feedback handoff | `schemas/feedback-handoff-v1.schema.json` | Routes a proposal to an existing authority without granting permission to act. |

## Compatibility

- Every published schema identifier and file is immutable.
- Any field addition, removal, type change, constraint change, or invariant
  change requires a new schema identifier and file, even when the change would
  otherwise appear additive.
- Consumers process only explicitly supported exact schema identifiers. There
  is no same-identifier forward compatibility.
- Unknown properties fail validation. Extensions belong in a new versioned
  contract.
- Timestamps use RFC 3339 UTC values. Digests use lowercase SHA-256 hex.
- Identifiers are opaque. They must not contain credentials, protected content,
  personal filesystem paths, or private host names.

## Processing rules

1. Validate the complete record against the named schema before accepting it.
2. Store the exact serialized bytes or a canonical digest plus durable source
   reference.
3. Deduplicate retries by producer, event type, natural key, and schema version.
4. Preserve missing data as explicit missingness, never as zero or success.
5. Route proposals through the adopter's existing authorization and lifecycle
   gates.

The examples under `examples/` are public synthetic fixtures and have no
operational authority.

Evaluation v2 remains immutable for historical verification but is superseded
by v3 because v2 permits a PASS metric value to be null. New producers and
consumers must use evaluation v3. Existing records migrate through the exact
[`evaluation v2-to-v3 procedure`](./MIGRATION-V2-V3.md).

Evaluation v3 remains immutable but is superseded by v4 for producers that can state what kind of measurement
they made. v4 adds two required fields:

- `evidence_class`: `synthetic` (a fixture or offline corpus), `live_self_retrieval` or `live_labelled`.
- `instrument`: `runs_production_code` (the measured candidate is the production code path, not a
  reimplementation), `baselines_named` (the baselines the candidate was compared with), `sabotage_tested` (the
  instrument was shown failing when production was broken on purpose) and `dead_arms_reported` (a dead arm is reported
  as dead, never as zero).

Validator rule: outcome `pass` is impossible with `evidence_class` `synthetic` or with `runs_production_code` false.
Those records are capped at `inconclusive` (or the stronger `fail`, `blocked` or `regression`). The v3 rules still
apply to `pass`. The other three instrument flags are reported evidence; `scripts/build_evaluation.py` also caps its
own output when they are weak, but the schema does not require it.
