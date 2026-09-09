# SKRSI architecture

## Identity

SKRSI is **SK Recursive SELF Improvement**. SELF expands to **Systematic Evaluation, Learning, and Feedback**.

## Responsibility boundary

| Component | Owns | Does not own |
|---|---|---|
| Target registry | Versioned targets, invariants, metrics, expiry, rollback proposal | Runtime activation |
| Collector | Bounded metadata ingestion, deduplication, lineage, missingness | Protected payload access |
| Evaluator | Deterministic cohort comparison and quality gates | Approval or causal claims without evidence |
| Experiment controller | Bounded proposal state and stop rules | Deployment or service mutation |
| Estate adapters | Typed software lifecycle handoffs | Product-domain policy |
| Dashboard projection | Authorized aggregate visibility | Source content or authorization decisions |
| Independent reviewer | Exact-evidence PASS, FAIL, or BLOCKED verdict | Producing the candidate it reviews |

## Invariants

1. Models and agents produce proposals. Existing policy owners authorize actions.
2. Producer and reviewer identities differ.
3. Missing data remains explicit and never becomes zero.
4. Throughput cannot override security, quality, independence, or rollback gates.
5. Every retry revalidates current authority and keeps the same idempotency boundary.
6. Historical evidence is append-only and is not rewritten for terminology changes.

## Current implementation

The first-wave implementation currently lives in SKCapstone modules named `skrsi_registry`, `skrsi_collector`, `skrsi_evaluator`, `skrsi_experiment_controller`, `skrsi_estate_adapters`, `skrsi_handoffs`, and `skrsi_runtime`. SKDashboard provides bounded aggregate visibility. Extraction into this repository is a future governed change, not implied by this documentation initialization.

## Public contract boundary

This repository owns portable, versioned data contracts for targets, event
envelopes, evaluations, and feedback handoffs. It does not provide a scheduler,
authorization service, model router, deployment controller, or product data
plane. Adopters validate records before persistence or transport, reject
unknown schema versions, preserve source bytes and hashes, and keep producer
and reviewer identities independent.
