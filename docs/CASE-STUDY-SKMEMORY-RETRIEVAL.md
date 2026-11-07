# Case study: the first code adopter

`skmemory-retrieval` was the first target to run the reusable evaluate workflow on real pull requests, in November
2026. This records what that evidence could and could not say, so the next adopter starts from it. The target and the
pinned live baseline are in the skmemory repository under `skrsi/`.

## What the adoption got right

- Evaluation v4 capped every synthetic run at `inconclusive`. Nothing claimed a win on fixture data.
- Pinning a live baseline before any fix recorded the uncomfortable number: fused recall at 5 of 0.601 against 0.729 for
  plain BM25 on 398 self-retrieval queries, which is what started the fusion work.
- The sabotage claim held up when rerun. Reversing fusion output, dropping the top hit, or reversing and cutting to one
  result took the worst class recall from 0.64 to 0.0. Moving the top hit to rank 5 took it to 0.625.
- The workflow stayed advisory and blocked nothing. Two early runs were startup failures with no jobs, both before the
  reusable workflow reached `main`, which the adopter PR had predicted. Later runs with the same caller file succeeded. A
  startup failure shows red on the PR, which no `continue-on-error` can prevent, so it must never be made a required check.

## What it missed

| Finding | Evidence | Where the fix belongs |
|---|---|---|
| One scalar hid a ranking change | PR 120 moved mean reciprocal rank in five classes (semantic 0.622 to 0.512, entity 0.917 to 0.806, multihop 0.542 to 0.812). The only quality metric, worst class recall, stayed 0.64, so the comment said flat. | Adopter: one metric per class, and rank as well as recall. |
| Objective not measured | The objective is "at or above BM25 for every query type", yet no metric compared those arms. Offline, procedural recall sits at 0.667 against 1.000 for BM25 on both sides of that PR, unseen. | Adopter: margin metrics. |
| Top stop rule unchecked | `fusion-below-bm25` is the decision rule for a later rewrite and was never machine-checked. | Adopter: a bound margin metric on live evidence. |
| Live evidence had no route in | The pinned baseline uses a private shape, and CI cannot reach a private corpus, so five of seven metrics were missing on every PR. | Adopter: a local flattener. SKRSI: this guide. |
| Same comment on every PR | Runs on five different PRs gave one identical `evaluation_id`, and a dozen lines repeated regardless of the change. | SKRSI: fixed. |
| Evidence not tied to a commit | The metrics file's cohort id overwrote the commit label, and the digest ignored both. | SKRSI: fixed. |
| Unmeasured stop rule read as passed | A dead metric produced `stop_rule:<id> passed: true`. | SKRSI: fixed. |
| A crashed bench was quiet | It looked the same as an instrument that measures nothing. | SKRSI: fixed. |
| The base side used the PR's instrument | The caller ran its flattener from `$GITHUB_WORKSPACE` on both sides. | Adopter: relative paths. SKRSI: documented. |
| Path filter missed the code under test | The candidate arm runs `MemoryStore.search` over the SQLite backend, and neither file was in the filter. | Adopter: filter on what the bench imports. |

## The lesson underneath

Sensitivity to gross breakage is not sensitivity to the change you care about. A sabotage test proves an instrument can
go red. It does not prove the instrument sees a PR that trades one class for another. Ask of every metric: what real
change would leave this number flat? PR 120 answered that for worst class recall.

## Still open

- Evaluations live in PR comments and in artifacts that expire, and nothing feeds them back into a target revision. The
  Learning and Feedback half of SELF is not yet closed for this adopter.
- Recall tolerance is a fixed number. A self-retrieval run of 398 queries has a standard error near 0.025, so a tolerance
  below that reads noise as change.
- `latency-regression` asks for comparable box load, and no field records load. Run both sides back to back on one box.
