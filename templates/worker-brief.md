# Worker brief: one card, one worktree, one PR

Copy this file, fill every `<PLACEHOLDER>`, and hand it to a worker agent together with ONE card id. The orchestrator
merges and updates the board. The worker never does either.

## Your job
Implement exactly the card `<CARD_ID>` in `<REPO>`. Read the card first (`<CARD_SHOW_CMD>`): its description and
acceptance criteria are the spec.

## Verify before you build
The card came from an audit. Confirm its claims against the code on your branch before writing anything. If a claim is
wrong, or the fix needs a decision outside the card, finish what is valid and put the discrepancy at the top of your
report. A fixture may be wrong: say so with reasoning, and regenerate it through its generator, never by hand.
**Never store a fixture's expected value you did not verify against the code that produces it.**

## Rules
- Work only in `<WORKTREE>` on branch `<BRANCH>` cut from `origin/main`. Never edit a shared checkout. Never a bare
  `git stash` (the stash is shared across worktrees).
- TDD: write the failing test, see it fail, implement, see it pass.
- Scratch and test base-temp go only under `<STATE_DIR>/<CARD_ID>/`, never `/tmp` (it was wiped mid-run, taking
  orchestration state with it). Delete only that exact path, never a glob.
- **Commit before the long verification.** Once the code and the targeted test pass: commit (message may say
  "verification pending"), then run the full suite, then amend or add a fix commit. A turn can end during a long run and
  uncommitted work is one pull from gone.
- Before pushing, run the FULL suite from the worktree root under CI-parity environment: `<CI_PARITY_TEST_CMD>`. CI
  lacks services that exist on your machine (database, embedding endpoint, local config), so unset or point those away
  exactly as CI does. A suite that is green only because a local service answered is a false green.
- Never touch a real store. If an agent or profile is declared in your shell, a bare store constructor opens LIVE state.
  Always pass an explicit temporary backend or set the home variables to scratch. If you touch live state, stop and
  report it first.
- Tests must be hermetic and must not break fakes in other test files when a signature changes.
- A classifier that decides by rules alone (no human, no model check) must never perform a destructive state change
  (delete, close, supersede, archive). It may propose; a destructive step needs positive evidence from the store.
- If you add an instrument (a bench, a metric, a gate), see "Instruments" below.
- Run the formatter and linter on changed files, rebase on `origin/main` before pushing, add a changelog fragment when
  the repo convention asks for one, no new dependencies, keep to the card's scope.
- Agent rules live in `AGENTS.md` only. Never create or edit a `CLAUDE.md` (or any other per-tool instruction file);
  if a rebase hits an edit of one, carry that edit into `AGENTS.md`.
- Rebase on `origin/main` first and again before pushing: sibling lanes edit nearby code. If your base PR was
  squash-merged, do not merge main into the branch: `git rebase --onto origin/main <last-squashed-commit>`, then
  retarget the PR to main.
- Only regular hyphens in prose, no em or en dashes. Commit trailer names the agent that did the work.
- Open the PR with a conventional-commit title carrying the card code. Do not wait for CI. Do not merge.

## Rust lanes
- Build and test on the build host, not on a small local machine: `<REMOTE_TEST_CMD> <worktree> [cargo args]`
  (the [`remote-test.sh`](./remote-test.sh) template). Edit locally; the runner rsyncs the worktree (no `.git`, no
  `target`), shares one target directory, and serves database tests from a THROWAWAY container on a non-default port.
  Never use the runner to touch anything else on the host, a real database included.
- Before pushing, all four must pass: `fmt --check`, `clippy --locked --all-targets` with the database feature
  `-- -D warnings`, `test` pure (no database feature), and `test` with the database feature. The database tests must
  be required to run, not skipped, in the last one.
- CI also builds the minimum supported Rust version (`<MSRV>`): do not use language or library features newer than it.
- Parity is checked against the reference implementation's `origin/main` (read only), `<PARITY_REPO_PATH>`.

## Instruments (benchmarks, metrics, gates)
1. The candidate arm must run PRODUCTION code, not a reimplementation. Show a sabotage test: break the production path
   on purpose and watch the instrument fail.
2. Baselines must be fair, not strawmen. For a lexical baseline, grep per token, not for the whole phrase.
3. A dead arm (crashed, empty, unreachable) reports `dead`, never `0`. Missing is explicit missingness.
4. Declare the evidence class: `synthetic`, `live_self_retrieval` or `live_labelled`. Synthetic never supports pass.
5. If a card criterion is smaller than the bench's measured run-to-run noise (or 1/N), report that instead of chasing it.

## Report (under 200 words)
PR number, head SHA, tests and counts, each criterion met / not met / needs live, and any discrepancy.
