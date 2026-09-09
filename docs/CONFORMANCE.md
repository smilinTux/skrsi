# Conformance

An implementation may claim one of these cumulative levels:

## Record conformant

- Every emitted record validates against its declared schema.
- Unknown schema versions and unknown properties fail closed.
- Digests, timestamps, required identities, and redaction classes are valid.

## Evidence conformant

- Meets record conformance.
- Exact source and evidence digests are reproducible.
- Retries are idempotent and conflicting replay is rejected.
- Missing, negative, null, and aborted outcomes remain visible.

## Lifecycle conformant

- Meets evidence conformance.
- Producer and independent evaluator identities differ.
- Feedback is proposal-only and passes through the adopter's existing authority.
- Stop, expiry, rollback, and recovery behavior is tested.
- No valid SKRSI record grants merge, deployment, credential, or actuation power.

## Required checks

The repository CI validates each schema with the JSON Schema Draft 2020-12
meta-schema, validates every public example, checks documentation links, scans
public artifacts for private path and host patterns, and runs `git diff --check`.
