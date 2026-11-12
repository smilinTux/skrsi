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
| Lesson v1 | `schemas/lesson-v1.schema.json` | Front matter of a canon `lesson` page: one actionable rule of 40 words or fewer, its evidence, the check that enforces it, a lifecycle status and a visibility. |
| Lesson tags v1 | `schemas/lesson-tags-v1.json` | The one tag vocabulary, shared by lesson `tags` and review failure classes. |
| Cluster v1 | `schemas/cluster-v1.schema.json` | `cluster.yaml` of a private instance repo: cluster name, `config_schema`, nodes (short name, role, enabled readers), adopted services, `canon: {repo, namespace}` and target overrides. Its node names are the instance allowlist. See [the instance model](./INSTANCE-MODEL.md). |

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
- Carve-out for vocabularies: `schemas/lesson-tags-v1.json` is not a schema but a
  list, and it is append-only within v1. A tag may be added in place. Renaming or
  removing a tag, or changing its meaning, requires a new file
  `schemas/lesson-tags-v2.json` with identifier `skrsi.lesson-tags.v2`. Consumers
  either read the list from the skrsi checkout path or pin it by digest. The
  current file is `sha256:f022ecd16d8858165e6ed88da77958f0f80f7be4c24e2447c532b5203e37bc1f`; every append updates
  this line (a test enforces it). A consumer on an older pin does not know a newly
  appended tag and maps it to the `fallback` value, `unclassified`, which is the
  expected degradation, not an error.
- Canon envelope dates (`created`, `updated` on a lesson) are ISO 8601 calendar
  dates (`YYYY-MM-DD`), matching canon pages, not RFC 3339 timestamps.

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

Each metric carries `direction`, `verdict` (improved, flat, regressed, missing) and optional `tolerance`, `min`, `max`.

Contract rule: outcome `pass` requires `evidence_class` other than `synthetic`, all four instrument flags true (production
code, sabotage tested, dead arms reported) and at least one named baseline, no regressed or missing metric and at least
one improved, plus the v3 conditions (passed guardrails, no missingness, numeric values). Records that do not meet this
are capped at `inconclusive` (or the stronger `fail`, `blocked` or `regression`). JSON Schema cannot do arithmetic or
compare dates, so `validate_v4` in `scripts/build_evaluation.py` is the normative validator: it also recomputes every
`verdict` from the numbers and requires `window_end` not before `window_start`. The schema carries every rule it can
express, and the two are tested against each other.

## Lesson v1 and the tag vocabulary

`skrsi.lesson.v1` is the front matter of a canon page of type `lesson`. Fields: `schema`, `id`, `type` (`lesson`),
`title`, `service` (one or more sk* names), `tags`, `class` (`defect`, `efficiency`, `reliability`, `process`),
`evidence` (one to ten `{kind, ref}` links to a PR, review, incident, problem, card, artifact or evaluation), `rule`
(one sentence, 40 words at most), `check` (the test, lint or doctor rule that enforces it, or `none yet`), `status`
(`proposed`, `active`, `enforced`, `retired`) and `visibility` (`public` or `private`). An `enforced` lesson must name
its check. Only `public` lessons may be quoted outside the canon, for example on a public PR. `service` names are
tag-shaped (`^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`), so `sk-standards`, `cloud9` and `capauth` are valid. No string field
may end in a newline; the schema patterns are anchored so a trailing newline is rejected there as it is in the
validator.

Because a lesson is canon page front matter, it may also carry the canon envelope that every skbrain-ops page has. All
six fields are optional, and any other field still fails validation:

- `namespace`: the canon namespace, a lowercase slug (`ops` for skbrain-ops).
- `lifecycle`: `draft`, `reviewed` or `canon`.
- `edges`: up to 50 typed links in the canon edge shape, `{target, type?}`, where `target` is a page slug and `type`
  is a snake_case edge type such as `touches` or `remediates`. The canon parser's string shorthand is not accepted.
- `created`, `updated`: ISO 8601 date strings. YAML parses an unquoted `2026-11-12` into a date object, so a consumer
  that reads front matter (skbrain lint, P1.4) must convert dates to `YYYY-MM-DD` strings before validating. Neither
  the schema nor the validator accepts a date object.
- `state_refs`: pointers into operational stores, never state itself, in the shape skbrain-ops pages use. An object
  with at most two keys, both optional: `kedb`, a known-error id of the form `ke-<slug>` (for example
  `ke-2d21671f` or `ke-outbox-flood`), and `actions`, a list of up to 10 unique operator standard-action names in the
  tag shape (for example `[restart-dashboard]`; an empty list is valid). Any other key fails validation.

A lesson derived from a known error points to its `ke-*` page through `edges` (for example
`{target: ke-outbox-flood, type: remediates}`) or through `state_refs.kedb`, not through an `evidence` kind. Evidence
kinds name the records that show the lesson happened (a PR, a review, an incident); the known error is a canon page,
and canon pages are linked by edges.

`origin`, `source_repo` and `source_path` are deliberately not part of v1, and a lesson that carries one fails
validation. Lessons live in `pages/lessons/` of the canon repo, and the canon parser supplies the origin of every page
it reads, so an authored copy could only disagree with it. A `source_path` would also put a path into front matter
that may be quoted outside the canon.

Canon `tags` on a lesson are limited to the vocabulary below. That differs from other canon page types, whose `tags`
are free topic tags. P1.3 records this rule in the skbrain-ops `AGENTS.md`.

There is deliberately no `recurrence` field, and an authored one fails validation. Recurrence is counted from
evidence records by the collector. Writing it into front matter would turn every count into an automated canon
commit.

`schemas/lesson-tags-v1.json` (`skrsi.lesson-tags.v1`) maps each tag to a one-line meaning. It is the single list
for lesson `tags` and for the `failure_class` of a FAIL review verdict, so one counter covers both. A consumer checks
membership with a set lookup on the keys of `tags`. A review class that is not in the list is stored as the
`fallback` value, `unclassified`, and never blocks a verdict. `unclassified` is not a valid lesson tag.

The vocabulary is append-only within v1: adding a tag does not change any existing record's validity, so it does
not need a new identifier. Removing or renaming a tag, or changing its meaning, breaks recurrence history keyed on
that tag and requires `lesson-tags-v2`.

`lesson_errors` in `scripts/validate_public.py` is the normative validator (`python scripts/validate_public.py
--lesson FILE...`). Like `validate_v4`, it is stdlib only. It applies every schema rule, checks every tag against
the vocabulary, and, for a lesson whose `visibility` is not `private`, rejects text matching the repo's public
artifact floor (`FORBIDDEN`: home and mount paths, one private host prefix, token names). That floor is only a floor.
It is not the private hostname check and does not know the estate's host names. The real check is skbrain lint
(P1.4, skos `secrets.py`), which runs before every canon commit. A `private` lesson may name local paths, since it is
never quoted outside the canon. The schema cannot read the vocabulary file, so a tag check needs the validator; the
two are tested against each other on the rules they share. A consumer that uses the schema alone must turn on format checking (for
example `jsonschema.FormatChecker` in Python); without it `format: date` is an annotation only, and a date such as
`2026-02-30` passes. A consumer that cannot do that uses `lesson_errors`.
