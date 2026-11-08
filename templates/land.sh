#!/usr/bin/env bash
# land.sh <repo-owner/name> <pr> <reviewed_head_sha> [worktree]
#
# Lands ONE reviewed PR. It never reviews and never overrides a failing check.
#   1. Waits (HEAD_WAIT_POLLS) for GitHub to report the reviewed head after a push, then aborts if the PR head is
#      still not exactly the head the reviewer saw. The API lags a push by seconds; a rebase you just pushed would
#      otherwise look like a moved head.
#   2. Waits for every check on that head. The rollup is deduplicated to the LATEST run per (workflow, check name),
#      so a cancelled duplicate left by a re-run or a retarget does not abort the land. A check with an EMPTY
#      conclusion is still pending (an in-progress check has no conclusion yet), never success.
#   3. Requires test evidence from successful runs on that head (pytest summary or cargo `test result: ok. N passed;
#      0 failed`). A PR with merge conflicts runs no tests, so all-green checks prove nothing.
#   4. Squash-merges with --match-head-commit, so a push between the last poll and the merge cannot land an
#      unreviewed head.
#   5. Waits for CI on the merged commit on the base branch (exit 7 if it is red; the hook does not run).
#   6. Runs the optional LAND_ACCEPT_CMD hook, then LAND_READBACK_CMD to read the accepted state back, and cleans up.
#
# The repo argument is OWNER/NAME. A bare NAME is accepted when LANE_REPO_OWNER is set (it is prefixed).
#
# Env (all optional):
#   LANE_STATE_DIR    evidence/log dir. Default: $XDG_STATE_HOME/lane-land or ~/.local/state/lane-land.
#                     Never /tmp: it can be wiped mid-run.
#   EVIDENCE_REGEX    ERE (matched with grep -o) that proves tests ran. Default accepts a pytest summary line with at least
#                     one pass and a cargo `test result: ok. N passed; 0 failed` line (N >= 1).
#   LANE_REPO_OWNER   owner prefixed to a bare repo name.
#   HEAD_WAIT_POLLS   polls to wait for the PR head to equal the reviewed head. Default 10.
#   LAND_ROUND_CMD    command that records the landing round on your board, run with LAND_ROUND set to
#                     "<pr> <head> PASS" (for example `coord link <card> review_round "$LAND_ROUND"`).
#                     FAIL rounds are recorded by the orchestrator when the review returns, see the playbook.
#   LAND_ACCEPT_CMD   command run after merge with LAND_REPO, LAND_PR, LAND_HEAD, LAND_MERGED
#                     and LAND_LOG exported (record the landing on your board here). If it fails and the change lines of
#                     the reviewed head and the merged commit are identical (a squash can change patch ids), it is run
#                     once more with LAND_HEAD set to the merged commit. Otherwise exit 8, card left open.
#   LAND_READBACK_CMD command run last; its output is printed as READBACK. It should read the accepted status back
#                     from the board by a different path than the one that wrote it. Non-zero exits 9.
#   POST_MERGE_CI     set to 0 to skip waiting for CI on the merged commit. POST_MERGE_POLLS default 60.
#   LOCAL_TEST_CMD   opt-in, for repos with no CI test workflow. Only when CI produced no test evidence: the EXACT reviewed
#                     head is checked out into a detached worktree under $LANE_STATE_DIR (never the PR worktree, so local
#                     edits cannot leak in), the command runs there, and its pytest summary line (`=== N passed ... ===`,
#                     no failed/error; or a cargo `test result: ok. N passed; 0 failed` line) becomes evidence marked
#                     "(local run)". The command is logged. A failing run exits 5.
#                     The worktree is removed afterwards. Unset: behaviour unchanged (exit 5).
#   MAX_WAIT_POLLS    polls before giving up. Default 120. POLL_SECONDS between polls, default 30.
# Exit: 0 landed, 2 head moved, 3 CI failed, 4 CI timeout, 5 no test evidence, 6 merge failed, 7 base CI red after merge,
#       8 accept hook failed, 9 readback failed.
set -euo pipefail
repo=${1:?repo owner/name} pr=${2:?pr number} head=${3:?reviewed head sha} wt=${4:-}
case $repo in */*) ;; *) repo=${LANE_REPO_OWNER:?bare repo name needs LANE_REPO_OWNER}/$repo ;; esac
state=${LANE_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/lane-land}
regex=${EVIDENCE_REGEX:-'=+ .*[1-9][0-9]* passed.* =+|test result: ok\. [1-9][0-9]* passed; 0 failed'}
mkdir -p "$state"
log="$state/${repo//\//_}-pr$pr-tests.log"

# Prints the non-green states on the current head; empty output means all green. The rollup keeps every run of a
# check, including cancelled duplicates from re-runs, so it is reduced to the latest run per (workflow, check name).
# A check with no name gets a unique key, so it is never merged into another.
pending() {
  gh pr view "$pr" --repo "$repo" --json statusCheckRollup -q '
    [.statusCheckRollup | to_entries[] | .key as $i | .value + {k: [(.value.workflowName // ""), (.value.name // .value.context // "#\($i)")]}]
    | group_by(.k) | map(max_by(.startedAt // .completedAt // ""))
    | map(if (.conclusion // "") == "" then (.status // "PENDING") else .conclusion end)
    | map(select(. != "SUCCESS" and . != "SKIPPED" and . != "NEUTRAL")) | join(",")'
}

# Wait for the reported head to equal the reviewed head. GitHub lags a push, so give it time before calling it moved.
wait_head() {
  local cur="" n
  for n in $(seq 1 "${HEAD_WAIT_POLLS:-10}"); do
    cur=$(gh pr view "$pr" --repo "$repo" --json headRefOid -q .headRefOid)
    [ "$cur" = "$head" ] && return 0
    sleep "${POLL_SECONDS:-30}"
  done
  echo "ABORT head moved: $cur != reviewed $head"; exit 2
}

st=pending
wait_head
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
  gh run view "$run" --repo "$repo" --log 2>/dev/null | { grep -oE "$regex" || true; } >>"$log.part"
done
localnote=""
if [ ! -s "$log.part" ] && [ -n "${LOCAL_TEST_CMD:-}" ]; then
  ldir="$state/${repo//\//_}-pr$pr-${head:0:12}-$$-localrun"  # per PR, head and PID: concurrent lands never share a tree
  gitc=${wt:-.}
  drop_local() { git -C "$gitc" worktree remove --force "$ldir" 2>/dev/null || true; rm -rf "$ldir"; git -C "$gitc" worktree prune 2>/dev/null || true; }
  trap drop_local EXIT
  git -C "$gitc" worktree prune 2>/dev/null || true  # a killed earlier land leaves a stale registration
  for old in "$state/${repo//\//_}-pr$pr-${head:0:12}"-*-localrun; do  # trees of dead lands of this PR and head only
    [ -e "$old" ] || continue
    opid=${old%-localrun}; opid=${opid##*-}
    kill -0 "$opid" 2>/dev/null || { git -C "$gitc" worktree remove --force "$old" 2>/dev/null || true; rm -rf "$old"; }
  done
  git -C "$gitc" worktree prune 2>/dev/null || true
  drop_local
  git -C "$gitc" cat-file -e "$head^{commit}" 2>/dev/null || git -C "$gitc" fetch -q origin "$head" 2>/dev/null || true
  if aerr=$(git -C "$gitc" worktree add -q --detach "$ldir" "$head" 2>&1); then
    lout=$( cd "$ldir" && bash -c "$LOCAL_TEST_CMD" 2>&1 ) && lrc=0 || lrc=$?
    drop_local
    if [ "$lrc" -eq 0 ]; then
      { grep -E '^(=+ .*[0-9]+ passed.* =+|test result: ok\. [0-9]+ passed; 0 failed.*)$' <<<"$lout" || true; } \
        | { grep -E '(^|[^0-9])[1-9][0-9]* passed' || true; } \
        | { grep -viE '[1-9][0-9]* failed|error' || true; } | sed 's/$/ (local run)/' >"$log.part"
      localnote="local_test_cmd=$LOCAL_TEST_CMD"
    fi
  else
    echo "ABORT local run: could not check out $head into $ldir: $aerr"
  fi
  drop_local
  trap - EXIT
fi
[ -s "$log.part" ] || { rm -f "$log.part"; echo "ABORT no test evidence for $head (a conflicting PR runs no tests)"; exit 5; }
{ echo "repo=$repo pr=$pr commit=$head"; [ -z "$localnote" ] || echo "$localnote"; sort -u "$log.part"; } >"$log"
rm -f "$log.part"

gh pr merge "$pr" --repo "$repo" --squash --match-head-commit "$head" || { echo "ABORT merge failed"; exit 6; }
merged=$(gh pr view "$pr" --repo "$repo" --json mergeCommit -q .mergeCommit.oid)
if [ -n "${LAND_ROUND_CMD:-}" ]; then
  LAND_ROUND="${pr} ${head} PASS" bash -c "$LAND_ROUND_CMD"
fi

# CI on the merged commit. A few polls of grace first: the runs for a fresh commit appear a moment after the merge.
if [ "${POST_MERGE_CI:-1}" != 0 ]; then
  bad=1
  for n in $(seq 1 "${POST_MERGE_POLLS:-60}"); do
    nruns=$(gh run list --repo "$repo" --commit "$merged" --limit 20 --json status -q 'length')
    bad=$(gh run list --repo "$repo" --commit "$merged" --limit 20 --json status,conclusion \
      -q '[.[] | select(.status != "completed" or (.conclusion != "success" and .conclusion != "skipped"))] | length')
    if [ "$bad" = 0 ] && { [ "$nruns" != 0 ] || [ "$n" -ge 4 ]; }; then break; fi
    sleep "${POLL_SECONDS:-30}"
  done
  [ "$bad" = 0 ] || { echo "ABORT base CI not green for $merged (merged; card left open)"; exit 7; }
fi

changelines() { git -C "${wt:-.}" diff "$1" "$2" 2>/dev/null | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' | sort; }
if [ -n "${LAND_ACCEPT_CMD:-}" ]; then
  export LAND_REPO=$repo LAND_PR=$pr LAND_HEAD=$head LAND_MERGED=$merged LAND_LOG=$log
  if ! bash -c "$LAND_ACCEPT_CMD"; then
    # A squash can change patch ids while the change lines stay identical: retry once against the merged commit.
    git -C "${wt:-.}" fetch -q origin "$merged" 2>/dev/null || true
    base=$(git -C "${wt:-.}" merge-base "$head" "$merged" 2>/dev/null || true)
    if [ -n "$base" ] && [ "$(diff <(changelines "$base" "$head") <(changelines "$merged^" "$merged") | wc -l)" = 0 ]; then
      echo "accept refused; change lines identical to merged commit, retrying against it"
      LAND_HEAD=$merged bash -c "$LAND_ACCEPT_CMD" || { echo "ABORT accept failed (merged; card left open)"; exit 8; }
    else
      echo "ABORT accept failed and merged differs from reviewed (merged; card left open)"; exit 8
    fi
  fi
fi
if [ -n "${LAND_READBACK_CMD:-}" ]; then
  rb=$(bash -c "$LAND_READBACK_CMD") || { echo "ABORT readback failed"; exit 9; }
  echo "READBACK $rb"
fi
# Cleanup by exact path only, never a glob.
if [ -n "$wt" ] && [ -d "$wt" ]; then git worktree remove --force "$wt"; fi
echo "LANDED $repo#$pr merged=$merged evidence=$log"
