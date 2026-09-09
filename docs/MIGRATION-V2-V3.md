# Evaluation v2 to v3 migration

Use `scripts/migrate_evaluation_v2_to_v3.py` for one record at a time. It uses
only the Python standard library.

```bash
python3 scripts/migrate_evaluation_v2_to_v3.py input.json output.json
```

## Exact mapping

The input must contain exactly the evaluation v2 fields. Unknown, missing,
malformed, nonfinite, or ambiguous values fail with no output record.

| v2 field | v3 mapping |
|---|---|
| `schema` | Replaced with the exact value `skrsi.evaluation.v3`. |
| `evaluation_id` | Copied unchanged after nonempty-string validation. |
| `target_ref` | Copied unchanged after target and positive revision validation. |
| `participants` | Copied unchanged after requiring exactly two distinct nonempty identities in producer, evaluator order. |
| `baseline` | Copied unchanged after exact cohort-field, sample-size, and timezone validation. |
| `treatment` | Copied unchanged after exact cohort-field, sample-size, and timezone validation. |
| `metrics` | Copied unchanged after exact field, finite-number-or-null, name, and unit validation. |
| `guardrails` | Copied unchanged after exact field and Boolean validation. |
| `missingness` | Copied unchanged, then deterministic null paths are appended only for a downgraded PASS. |
| `outcome` | Copied unchanged unless PASS contains a null metric; that case becomes `inconclusive`. |
| `evidence_sha256` | Copied unchanged after lowercase 64-hex validation. |

No v3-only field or invented metric value is added. The v3 discriminator and,
when required, the deterministic outcome and missingness entries are the only
changes.

## Null and failure rules

For a v2 PASS, null metric values are enumerated in metric order, baseline then
treatment. Each produces `metrics[index].field is null`. The outcome becomes
`inconclusive`; it never remains or becomes PASS. Existing missingness on a v2
PASS, a failed guardrail on PASS, unknown fields, invalid identities, malformed
cohorts, invalid types, nonfinite numbers, and invalid hashes fail closed.

Valid non-PASS v2 records retain their outcome and nullable metric values. A
valid v3 record is returned unchanged, which makes reruns idempotent. Invalid v3
records fail closed.

The executable synthetic pair is under `examples/migration/`.
