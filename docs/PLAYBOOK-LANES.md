# Lane playbook

A lane is one card, one worktree, one branch, one PR, one independent review, one landing. This page is the
generalized form of a run that shipped many such lanes in a day. Every rule below caught a real problem in that run.
The templates in [`templates/`](../templates/) carry the rules into the briefs, so a worker or reviewer that reads only
its brief still obeys them.

Nothing here adds a merge gate. Existing CI and regression gates stay the only blockers. SKRSI records evidence and
proposes; it never approves or merges, and no step needs a human.

## Roles

| Role | Does | Never does |
|---|---|---|
| Orchestrator | Cuts cards, hands out briefs, lands reviewed PRs with `land.sh`, updates the board | Implements a card it also reviews |
| Worker | Implements one card from [`worker-brief.md`](../templates/worker-brief.md), opens one PR | Merges, edits the board, opens other cards |
| Reviewer | Follows [`review-brief.md`](../templates/review-brief.md), returns `VERDICT: PASS` or `FAIL` | Writes code in the PR, merges, comments |

Producer and reviewer identities differ. The evaluation contract encodes this as `participants`.

## The loop

1. **Brief.** Fill the worker brief placeholders: repo, card, worktree, branch, state dir, CI-parity test command.
2. **Build.** Worker verifies the card's claims, writes the failing test, implements, passes, **commits**, then runs the
   full suite under CI-parity environment, then pushes and opens the PR.
3. **Evidence.** The advisory [`evaluate.yml`](../.github/workflows/evaluate.yml) runs the repo's bench on the head and
   the base, builds a `skrsi.evaluation.v4` record, and upserts one PR comment. It never fails the PR.
4. **Review.** A different agent reviews the exact head SHA.
5. **Land.** [`land.sh`](../templates/land.sh) refuses unless the head is the reviewed head, every check (latest run
   per check) is concluded green, and test evidence exists, then squash-merges, waits for CI on the merged commit, runs
   an optional board hook and reads the accepted state back. [`merge.sh`](../templates/merge.sh) is the same gate
   without a board, for PRs that must merge but not accept a card.
6. **Learn.** [`build_telemetry.py`](../scripts/build_telemetry.py) turns PR and board metadata into the
   `sk-build-quality` metrics so the next brief can change what the numbers say is slow or leaky.

## Record every review round

Build-quality metrics are only as honest as the record. Every review round, FAIL included, is recorded on the card
the moment the review returns, in one line:

```
coord link <card> review_round "<pr> <head-sha> PASS|FAIL <class,class>"
```

Defect classes are an allowlist: `privacy`, `data-loss`, `false-close`, `non-hermetic-test`, `instrument-validity`,
`concurrency`, `conflict-rebase`, `wrong-default`, `doc-drift`, `style`. A PASS round carries no class. The reviewer's last line
(`ROUND: ...` in the review brief) is exactly this value, so the orchestrator records it verbatim; `land.sh` records the
final PASS round through `LAND_ROUND_CMD`. A different board can use its own command, the format is what matters.

`build_telemetry.py` reports first-pass rate and rounds to PASS only over PRs whose rounds are all recorded (last round
PASS on the merged head, at least one record per reviewed head). A PR with no record is missing, never first-pass, and the
report prints how many rounds were covered. Commits pushed after the first review are shown as a labelled proxy
(`proxy_rework_rate`), never as the rate. `sk-build-quality` also states a minimum sample size (20 merged PRs); a smaller
window is capped at `inconclusive` whatever the deltas say.

## Lessons, each with its check

| Lesson | Rule | Where it is enforced |
|---|---|---|
| An instrument that cannot fail proves nothing | The candidate must run production code, and a sabotage test must be shown failing | `instrument.runs_production_code` and `sabotage_tested`; the contract caps `pass` at `inconclusive` when production code is not run |
| Strawman baselines inflate wins | Baselines must be fair; a lexical baseline greps per token, not per phrase | Name them in `instrument.baselines_named`; reviewers attack them; none named caps the outcome |
| A dead arm is not a zero | A crashed or unreachable arm reports `dead`, never `0` | A null metric becomes explicit missingness; `dead_arms_reported` is an instrument claim |
| Synthetic is not live | Declare `evidence_class`. Synthetic data never supports `pass` | `skrsi.evaluation.v4` validator |
| CI lacks what your machine has | Run the full suite under the CI-parity environment (no database, no embedding endpoint, no local config) | Worker and reviewer briefs name the exact command |
| A conflicting PR runs no tests | All-green checks without test output block landing | `land.sh` exit 5 |
| In-progress looks like nothing | A check with an empty conclusion is pending, not success | `land.sh` treats it as pending, covered by a test |
| The head moved after review | Land only the exact reviewed SHA | `land.sh` exit 2 |
| `/tmp` gets wiped | Never keep orchestration state, scratch or evidence in `/tmp`; use a state directory | Briefs and `land.sh` default to `$XDG_STATE_HOME` |
| Unverified fixture values rot | Never store a fixture's expected value you did not verify against the producing code; regenerate through the generator | Worker brief |
| A bare constructor can open live state | When an agent or profile is declared in the environment, a bare store constructor writes to live data. Pass an explicit temporary backend | Worker and reviewer briefs |
| Privacy and false-close are where defects hide | Reviewers attack them first, on live-like stores, not mocks | Review brief |
| Rule-only classifiers must not destroy | A classifier with no positive store evidence may only propose; deletes, closes and supersedes need evidence | Worker and review briefs |
| Globs delete the wrong thing | Cleanup removes exact paths only | Briefs, `land.sh` |
| Uncommitted work is not saved | Commit before the long verification, never gate a commit on a background job | Worker brief |
| Re-runs and retargets leave cancelled duplicates | The check rollup holds every run of a check. Reduce it to the latest run per (workflow, check name) before judging it | `land.sh` and `merge.sh`, covered by a test |
| GitHub lags a push | After you push a rebase, the PR head the API reports is stale for seconds. Wait for it to equal the head you mean to land, then abort if it never does | `HEAD_WAIT_POLLS` in both scripts |
| A merge can still race | A push between the last poll and the merge lands an unreviewed head | `--match-head-commit` |
| Green on the PR is not green on main | Wait for CI on the merged commit; a red base after merge leaves the card open | `land.sh` exit 7 |
| Success is not the stored state | Read the accepted card status back by a different path than the one that wrote it | `LAND_READBACK_CMD` |
| Rust CI prints no pytest line | Evidence also accepts cargo `test result: ok. N passed; 0 failed` with N of at least 1 | `EVIDENCE_REGEX` default |
| Squash merges break stacked PRs | After the base PR is squash-merged, the child still carries the original commits and conflicts. Do not merge main into it: `git rebase --onto origin/main <last-squashed-commit>`, push, then retarget the PR to main | Worker brief |
| The repo argument has one shape | The scripts take `OWNER/NAME`; a bare name is prefixed only when `LANE_REPO_OWNER` is set. A kit that prefixes the owner itself takes the bare name, and passing the full form builds a doubled owner that fails late | `land.sh` and `merge.sh` header |
| A dual-engine test can be vacuous | A test that runs one assertion set against two engines proves nothing for a leg that never reaches its engine. The reviewer counts the real engine calls per leg | Review brief |
| Small laptops cannot carry Rust lanes | Build and test on a bigger host with one shared target directory and a throwaway database on a non-default port, with a disk floor | [`remote-test.sh`](../templates/remote-test.sh) |
| Per-tool instruction files drift | Agent rules live in `AGENTS.md` only; carry a rebase conflict in another file into it | Worker brief |
| A repo with no CI tests | Land with `LOCAL_TEST_CMD` (pytest or cargo summary) on the exact reviewed head in a detached worktree, never from the worker's tree | `land.sh` |
| An evidence PR is not the card | A PR that records evidence for a card (such as an SKRSI evaluation) merges without accepting the card; the board hook runs only for the PR that completes the card | `land.sh` board hook |
| Docs that contradict the code | A changelog or README line describing removed behaviour is a review defect, class `doc-drift` | Review brief |
| A criterion below the noise floor | Before writing a numeric acceptance criterion, measure the bench's run-to-run noise; a threshold smaller than the noise or than 1/N cannot be met or failed honestly | Target `noise` field; card template |

## Landing kit

| Script | Use |
|---|---|
| [`land.sh`](../templates/land.sh) | Land the PR that completes a card: gates, merge, base CI wait, board hook, readback |
| [`merge.sh`](../templates/merge.sh) | Merge a reviewed PR that completes no card, same gates, no board |
| [`remote-test.sh`](../templates/remote-test.sh) | Run cargo for a worktree on a build host with a throwaway database |

All three take their host, ports, image, state directory and hooks from environment variables and carry no host,
address, credential or personal path. Copy them into your orchestration directory and set the variables there.

## Adopting SKRSI in a repo

1. Add a target file (`skrsi.target.v2`), for example `skrsi/targets/<name>.json`. Declare metrics, invariants and
   stop rules before the change, not after.
2. Make the bench write a metrics JSON (shape in the [adopter guide](./ADOPTER-GUIDE.md)).
3. Add one caller workflow that uses `smilinTux/skrsi/.github/workflows/evaluate.yml`. Do not add it to required checks.

## What was cut

- A SKRSI-owned reviewer or approver: reviewers and the lifecycle gates already exist, and SKRSI must not approve.
- A dashboard, database or service for telemetry: a script over `gh` and board metadata, run on demand.
- A shared orchestration daemon and a policy engine: the loop above is a handful of shell and JSON steps.
- A parallel board for build quality: the board already holds the facts; telemetry only reads them.
- Auto-parsing prose invariants and stop rules: unparseable prose is listed as not machine-checked, never guessed.
