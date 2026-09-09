# Public contract catalog

SKRSI publishes metadata-only contracts for evidence-first software lifecycle
improvement. Each JSON document declares its exact schema URI. A consumer must
reject unknown major versions and must not infer authorization from a valid
record.

| Contract | Schema | Purpose |
|---|---|---|
| Target | `schemas/target-v2.schema.json` | Registers a bounded improvement objective, measures, invariants, stop rules, expiry, and rollback owner. |
| Event envelope | `schemas/event-envelope-v1.schema.json` | Carries attributable metadata, lineage, hashes, and redaction class. |
| Evaluation | `schemas/evaluation-v2.schema.json` | Records independent participants, cohort results, quality gates, missingness, and an evidence-bound outcome. |
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
