# Adopter guide

## Minimum integration

1. Select one registered target and pin its exact target revision.
2. Emit metadata-only observations in the event envelope.
3. Compare a declared baseline and treatment using the evaluation contract (v4 for new producers).
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

## Make SKRSI part of every build

Adoption is one caller workflow plus a target file. The workflow is advisory: it never fails the PR, including when
SKRSI itself breaks, and it must not be added to required checks.

```yaml
name: skrsi
on:
  pull_request:
    paths: ["src/**", "skrsi/**"]
jobs:
  evaluate:
    permissions: {contents: read, pull-requests: write}
    uses: smilinTux/skrsi/.github/workflows/evaluate.yml@main
    with:
      target_file: skrsi/targets/my-target.json
      evidence_class: synthetic          # or live_self_retrieval, live_labelled
      setup_command: pip install -e .
      bench_command: python -m my_pkg.bench --skrsi-out "$SKRSI_METRICS_OUT"
```

The workflow runs `bench_command` on the PR head and again at the PR base (or `baseline_command`), each from its own
checkout. A PR that edits its bench therefore compares two versions of the instrument, and nothing flags that, so read a
moved number in such a PR with care. It builds `evaluation.json` and `evaluation.md`, uploads them as the
`skrsi-evaluation` artifact, and upserts one comment per target. To freeze behaviour, pin both `uses:` and `skrsi_ref`
to one commit: `skrsi_ref` alone freezes the scripts but not the workflow that calls them.

### Metrics file

Baseline and metrics share one shape. Rules (`direction`, `tolerance`, `min`, `max`, `stop_rule`, `unit`) and the
`instrument` description are read from the BASE side only, so a PR cannot relax its own bar; the metrics side supplies
values and invariants. If the PR's file changes a rule, the base rule applies and the change is listed as missingness.
The workflow also reads the target at the base ref (falling back to the head copy for a target's first PR).

```json
{
  "cohort": {"id": "pr-head", "sample_size": 30, "window_start": "...Z", "window_end": "...Z"},
  "instrument": {"runs_production_code": true, "baselines_named": ["bm25_only"], "sabotage_tested": true, "dead_arms_reported": true},
  "invariants": {"<exact target invariant text>": true},
  "metrics": {
    "recall_at_5": {"value": 0.7, "unit": "ratio", "direction": "higher", "tolerance": 0.02, "min": 0.5, "stop_rule": "recall-floor"}
  }
}
```

- `value: null` is a dead or unmeasured arm. It becomes missingness, never zero.
- `tolerance` is relative to the baseline value. `min` and `max` are absolute bounds. A metric with a bound triggers
  its `stop_rule` only by breaking the bound; without a bound, any regression beyond tolerance triggers it.
- `stop_rule` must name a rule id in the target. Stop rules without a binding are listed as not machine-checked.
- Every target invariant needs a boolean in `invariants`, or it is listed as missingness and the outcome stays
  `inconclusive`.

### Outcome rules

`regression` (a metric worse than baseline beyond tolerance, or out of bounds) beats `fail` (an asserted invariant is
false), which beats `inconclusive` (any missingness, a synthetic evidence class, an instrument that does not run
production code, has no sabotage test, does not report dead arms or names no baseline, or no metric improved), which
beats `pass`. `scripts/build_evaluation.py` exits 0 for every outcome and 2 only when its own input is malformed.

## Getting signal out of a target

What the first adopter learned, with the evidence in the [case study](./CASE-STUDY-SKMEMORY-RETRIEVAL.md):

- **Do not collapse what you need to see.** A worst-of or mean metric hides any change that stays above the floor. Name one
  metric per class in the target (`class_mrr.semantic`). The comment shows only what moved, so more metrics add no noise.
- **Measure the objective itself.** If the objective compares two arms, emit their difference from one run, in the same
  bench: `recall_margin_vs_bm25 = min over classes of (candidate - bm25)`. No schema change is needed.
- **Machine-check a cross-arm stop rule with a margin.** Give that metric `"min": 0` and a `stop_rule`. Leave the bound off
  when the margin is already negative and the rule does not apply to this evidence class, or every PR reports a
  regression it did not cause. A bound that was already broken at base says so in the comment.
- **Ask what real change would leave each metric flat.** A sabotage test shows the instrument can go red. It does not show
  that it sees the change you care about.
- **Only list invariants someone can assert.** `build_evaluation.py` already enforces distinct producer and evaluator, so
  listing that as a target invariant only adds a permanent missingness line.
- **Filter on what the bench imports.** A path filter narrower than the code under test skips the PRs that matter.

### Use relative paths in `bench_command`

The bench runs with the checkout of the side it measures as its working directory. `$GITHUB_WORKSPACE` always points at the
PR's merge commit, so a flattener called through it measures the base with the PR's rules. The measured treatment is that
merge commit, labelled with the PR head.

This protects against accidents, not a hostile PR. A `pull_request` run takes its caller workflow, and so `bench_command`,
from the PR itself. SKRSI is advisory evidence, and review remains the control.

### Live evidence

CI cannot reach a private corpus, and the workflow must not be given access to one. Live evidence goes through the same
builder, run where the data is:

```bash
python scripts/build_evaluation.py --target skrsi/targets/my-target.json \
  --baseline before.json --metrics after.json --producer my-runner --evaluator skrsi-local \
  --evidence-class live_self_retrieval --baseline-id "$BASE_SHA" --treatment-id "$HEAD_SHA" \
  --out evaluation.json --md evaluation.md
```

Flatten the live output into the metrics shape first, aggregates only, and report a dead arm as `null`. Only commit or post
what the data boundary above allows. A private corpus can only reach `pass` this way, because synthetic evidence is capped.
`evidence_class` is whatever the caller declares and nothing verifies it, so declaring a fixture run as live is a false
record, and review is what catches it.

`--note` records a known gap as missingness and leads the comment. The workflow uses it to name a bench that failed.

## Build-quality telemetry

`python3 scripts/build_telemetry.py --repo OWNER/NAME --prs 92-117 --md report.md --metrics-out window.json` computes
review rounds to PASS, first-pass review rate, CI failures before merge, time from open to merge, reverted merges and
allowlisted `defect:<class>` label counts from `gh` and the coordination event log only. It reads no prompts, PR
bodies, review text or diffs. Run it on two windows and feed both files to `build_evaluation.py` against
`targets/sk-build-quality.json` to see whether builds got better.

Review metrics need recorded rounds. Each round (FAIL included) is written as
`coord link <card> review_round "<pr> <head> PASS|FAIL <class,class>"` (see the [lane playbook](./PLAYBOOK-LANES.md)).
First-pass rate and rounds to PASS cover only PRs whose rounds are all recorded; the rest are missing and the count of
covered rounds is printed. A reviewed-head or commit-after-review signal is a labelled proxy, never the rate. Review text
is never read, and direct-push reverts are not seen. A target can state a floor with an invariant of the exact form
`min_sample_size: <n> ...`; a window below it is capped at `inconclusive`.

Defect classes come from recorded FAIL rounds and from `defect:<class>` labels, over the allowlist `privacy`, `data-loss`,
`false-close`, `non-hermetic-test`, `instrument-validity`, `concurrency`, `conflict-rebase`, `wrong-default`, `style`.

See the [lane playbook](./PLAYBOOK-LANES.md) for the briefs and the landing script.
