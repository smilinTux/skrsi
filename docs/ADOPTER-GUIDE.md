# Adopter guide

## Minimum integration

1. Select one registered target and pin its exact target revision.
2. Emit metadata-only observations in the event envelope.
3. Compare a declared baseline and treatment using the evaluation contract.
4. Encode participants as `[producer, independent evaluator]`; the two values
   must differ.
5. Send any accepted recommendation as a feedback handoff to an existing
   authority. A handoff is never permission to merge, deploy, or actuate.

## Worked example

`examples/target-v2-agent-workflow-latency.json` and
`examples/evaluation-v3-agent-workflow-latency-inconclusive.json` are a matched
pair showing the most common way a first target goes wrong: a latency metric
that looks like a win but is measured on a self-selected fraction of the
population.

The target declares measurement coverage as a metric in its own right and gives
it a stop rule, so the latency numbers cannot be read without it. The evaluation
then reports `inconclusive` rather than `pass`, and names the gap in
`missingness` instead of imputing the unmeasured remainder. A metric that
describes a tenth of the population is not a small version of the truth.

## Storage and replay

Keep an append-only journal or equivalent immutable record, a query index, and
an outbox for delivery. Retrying the same natural event must return the original
event identifier. Conflicting bytes under the same idempotency key must fail.
Unknown schemas, invalid hashes, future timestamps, missing source authority,
and malformed records fail closed.

## Data boundary

Collect only allowlisted lifecycle metadata. Never include prompts, model
responses, mailbox bodies, credentials, capability tokens, private host names,
personal paths, or protected product content. Hash opaque identifiers when an
aggregate does not require their clear value.

## Rollout

Begin with a proposal-only canary. Predeclare sample size, metrics, stop rules,
quality and security invariants, expiry, evidence retention, and rollback owner.
An inconclusive or missing result remains inconclusive. Throughput improvement
cannot override a failed guardrail.

Run `python3 -m unittest discover -s tests -v` before adopting or modifying a
contract.

## Evaluation migration

Existing evaluation v2 records use the deterministic, fail-closed procedure in
[`MIGRATION-V2-V3.md`](./MIGRATION-V2-V3.md). Do not edit v2 records in place.
Write the v3 result as a new record and preserve the source bytes and digest.
