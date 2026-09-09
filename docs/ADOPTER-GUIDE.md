# Adopter guide

## Minimum integration

1. Select one registered target and pin its exact target revision.
2. Emit metadata-only observations in the event envelope.
3. Compare a declared baseline and treatment using the evaluation contract.
4. Require an evaluator who differs from the producer.
5. Send any accepted recommendation as a feedback handoff to an existing
   authority. A handoff is never permission to merge, deploy, or actuate.

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
