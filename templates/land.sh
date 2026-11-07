#!/usr/bin/env bash
# land.sh <repo-owner/name> <pr> <reviewed_head_sha> [worktree]
#
# Lands ONE reviewed PR. It never reviews and never overrides a failing check.
#   1. Aborts if the PR head is not exactly the head the reviewer saw.
#   2. Waits for every check on that head. A check with an EMPTY conclusion is
#      still pending (an in-progress check has no conclusion yet), never success.
#   3. Requires pytest-style evidence from successful runs on that head. A PR
#      with merge conflicts runs no tests, so all-green checks prove nothing.
#   4. Squash-merges, then runs the optional LAND_ACCEPT_CMD hook and cleans up.
#
# Env (all optional):
#   LANE_STATE_DIR    evidence/log dir. Default: $XDG_STATE_HOME/lane-land or ~/.local/state/lane-land.
#                     Never /tmp: it can be wiped mid-run.
#   EVIDENCE_REGEX    ERE that proves tests ran. Default: '[0-9]+ passed'.
#   LAND_ROUND_CMD    command that records the landing round on your board, run with LAND_ROUND set to
#                     "<pr> <head> PASS" (for example `coord link <card> review_round "$LAND_ROUND"`).
#                     FAIL rounds are recorded by the orchestrator when the review returns, see the playbook.
#   LAND_ACCEPT_CMD   command run after merge with LAND_REPO, LAND_PR, LAND_HEAD, LAND_MERGED
#                     and LAND_LOG exported (record the landing on your board here).
#   MAX_WAIT_POLLS    polls before giving up. Default 120. POLL_SECONDS between polls, default 30.
# Exit: 0 landed, 2 head moved, 3 CI failed, 4 CI timeout, 5 no test evidence, 6 merge failed.
set -euo pipefail
repo=${1:?repo owner/name} pr=${2:?pr number} head=${3:?reviewed head sha} wt=${4:-}
state=${LANE_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/lane-land}
regex=${EVIDENCE_REGEX:-'[0-9]+ passed'}
mkdir -p "$state"
log="$state/${repo//\//_}-pr$pr-tests.log"

pending() {  # prints the non-green states on the current head; empty output means all green
  gh pr view "$pr" --repo "$repo" --json statusCheckRollup -q '
    [.statusCheckRollup[] | (if (.conclusion // "") == "" then (.status // "PENDING") else .conclusion end)]
    | map(select(. != "SUCCESS" and . != "SKIPPED" and . != "NEUTRAL")) | join(",")'
}

st=pending
for _ in $(seq 1 "${MAX_WAIT_POLLS:-120}"); do
  cur=$(gh pr view "$pr" --repo "$repo" --json headRefOid -q .headRefOid)
  [ "$cur" = "$head" ] || { echo "ABORT head moved: $cur != reviewed $head"; exit 2; }
  st=$(pending)
  [ -z "$st" ] && break
  if grep -qE 'FAILURE|CANCELLED|TIMED_OUT|ACTION_REQUIRED' <<<"$st"; then echo "ABORT CI: $st"; exit 3; fi
  sleep "${POLL_SECONDS:-30}"
done
[ -z "$st" ] || { echo "ABORT CI timeout: $st"; exit 4; }

: >"$log.part"
for run in $(gh run list --repo "$repo" --commit "$head" --limit 40 --json databaseId,conclusion \
    -q '.[] | select(.conclusion == "success") | .databaseId'); do
  gh run view "$run" --repo "$repo" --log 2>/dev/null | { grep -E "$regex" || true; } >>"$log.part"
done
[ -s "$log.part" ] || { rm -f "$log.part"; echo "ABORT no test evidence for $head (a conflicting PR runs no tests)"; exit 5; }
{ echo "repo=$repo pr=$pr commit=$head"; sort -u "$log.part"; } >"$log"
rm -f "$log.part"

gh pr merge "$pr" --repo "$repo" --squash || { echo "ABORT merge failed"; exit 6; }
merged=$(gh pr view "$pr" --repo "$repo" --json mergeCommit -q .mergeCommit.oid)
if [ -n "${LAND_ROUND_CMD:-}" ]; then
  LAND_ROUND="${pr} ${head} PASS" bash -c "$LAND_ROUND_CMD"
fi
if [ -n "${LAND_ACCEPT_CMD:-}" ]; then
  LAND_REPO=$repo LAND_PR=$pr LAND_HEAD=$head LAND_MERGED=$merged LAND_LOG=$log bash -c "$LAND_ACCEPT_CMD"
fi
# Cleanup by exact path only, never a glob.
if [ -n "$wt" ] && [ -d "$wt" ]; then git worktree remove --force "$wt"; fi
echo "LANDED $repo#$pr merged=$merged evidence=$log"
