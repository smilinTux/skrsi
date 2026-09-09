# Security policy

SKRSI is currently documentation-only, but its contracts influence automated software lifecycle decisions. Incorrect metrics, stale authority, reviewer collisions, unbounded retries, and fabricated improvement claims are security-relevant.

## Reporting

Use GitHub private vulnerability reporting for `smilinTux/skrsi`. Do not open a public issue containing an unpatched vulnerability, credential, protected payload, or exploit detail.

## Rules

- Never commit credentials, protected product data, prompts, responses, or raw mail content.
- Treat observations as non-authoritative metadata.
- Require independent review before accepting an improvement proposal.
- Fail closed on missing policy, stale evidence, malformed input, overload, or timeout.
- Preserve exact evidence hashes and rollback references.

SKRSI is not a crypto component and makes no cryptographic security claim.
