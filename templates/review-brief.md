# Reviewer brief: independent PR review

You did not write this code. Decide whether it should merge. Be adversarial but fair: real defects, not style.

1. Read the card: spec and criteria (`<CARD_SHOW_CMD>`).
2. Read the PR: `gh pr view <PR> --repo <REPO>` and `gh pr diff`.
3. Check out the PR head in a throwaway worktree under `<STATE_DIR>/rev-<PR>` (never `/tmp`). Never edit it. Remove it
   afterwards by that exact path.
4. Run the targeted tests, then the full suite once under CI-parity environment (`<CI_PARITY_TEST_CMD>`), with its
   base-temp under `<STATE_DIR>`, deleted afterwards by exact path.
5. Never touch a real store. Declared agent or profile variables make a bare constructor open live state, so point
   every home variable at scratch.
6. Execute any SQL or query you could execute on a throwaway database. Unexecuted SQL you could have run is blocking.
7. Rust PRs: build and test on the build host with the remote runner (`<REMOTE_TEST_CMD> <checkout> [cargo args]`,
   the `remote-test.sh` template), not on a small local machine. Run `fmt --check`, `clippy` with the database
   feature `-D warnings`, the pure test run and the database test run.

## Attack these paths first
- **Privacy**: can a non-owner reader see an item on a live-like store (real schema, real audience rows, not a mock)?
- **False close**: can anything be marked done, superseded, deleted or archived without positive evidence? A
  rule-only classifier that performs a destructive change is blocking.
- **Instrument honesty**: does the candidate run production code? Is there a sabotage test that fails? Is each
  baseline fair (grep per token, not per phrase)? Does a dead arm report dead rather than zero? Is the evidence class
  declared, and is synthetic evidence presented as a pass?
- **CI parity**: does it pass with the services CI lacks absent?
- **Tests fail without the fix**: revert the production change and confirm a test goes red. A test that passes both
  ways is blocking.
- **Vacuous dual-engine legs**: when a test runs the same assertions against two engines (for example a local engine
  and a database engine), count the real calls each leg makes into its engine. A leg that silently falls back, skips
  or never reaches the engine passes while proving nothing.
- Other callers of any changed signature, data loss on existing stores, mixed-version nodes, PR claims against the code,
  fixture expected values that were never verified, and em or en dashes in added lines (a defect in any file, code,
  docs, comments and commit messages included; regular hyphens are fine).

## Verdict (under 150 words)
First line exactly `VERDICT: PASS` or `VERDICT: FAIL`, then a line `ROUND: <pr> <head-sha> PASS|FAIL <class,class>`
(classes from: privacy, data-loss, false-close, non-hermetic-test, instrument-validity, concurrency, conflict-rebase,
wrong-default, doc-drift, style; none for PASS), then the head SHA, blocking defects with file:line and a failure
scenario, non-blocking notes last. Do not merge, push, comment, or touch the board.
