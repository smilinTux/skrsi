#!/usr/bin/env bash
# merge.sh <repo-owner/name> <pr> <reviewed_head_sha>
#
# Merges ONE reviewed PR WITHOUT touching a board. Use land.sh when the PR completes a card; use this for an
# evidence PR, a docs PR or a stacked step that must merge but must not accept anything.
#   1. Waits (HEAD_WAIT_POLLS) for GitHub to report the reviewed head after a push, then aborts if it still differs.
#   2. Waits until every check on that head is concluded. The rollup is deduplicated to the latest run per
#      (workflow, check name), so a cancelled duplicate from a re-run or a retarget does not abort. An EMPTY
#      conclusion is pending, never success.
#   3. Squash-merges with --match-head-commit.
#   4. Waits for CI on the merged commit (POST_MERGE_CI=0 skips) and exits 7 if it is red.
#
# A repo whose CI never runs (no checks at all) needs ALLOW_NO_CI=1 plus local test evidence, and a note saying so.
# The repo argument is OWNER/NAME; a bare NAME is accepted when LANE_REPO_OWNER is set.
#
# Env (all optional): HEAD_WAIT_POLLS (10), MAX_WAIT_POLLS (120), POLL_SECONDS (30), ALLOW_NO_CI, LANE_REPO_OWNER,
#   POST_MERGE_CI, POST_MERGE_POLLS (60).
# Exit: 0 merged, 2 head moved, 3 CI failed, 4 CI timeout, 5 no checks ran, 6 merge failed, 7 base CI red after merge.
set -euo pipefail
repo=${1:?repo owner/name} pr=${2:?pr number} head=${3:?reviewed head sha}
case $repo in */*) ;; *) repo=${LANE_REPO_OWNER:?bare repo name needs LANE_REPO_OWNER}/$repo ;; esac
sleep_s=${POLL_SECONDS:-30}

rollup() {  # JSON list of the latest state per (workflow, check name)
  gh pr view "$pr" --repo "$repo" --json statusCheckRollup -q '
    [.statusCheckRollup | to_entries[] | .key as $i | .value + {k: [(.value.workflowName // ""), (.value.name // .value.context // "#\($i)")]}]
    | group_by(.k) | map(max_by(.startedAt // .completedAt // ""))
    | map(if (.conclusion // "") == "" then (.status // "PENDING") else .conclusion end)'
}

cur=""
for n in $(seq 1 "${HEAD_WAIT_POLLS:-10}"); do
  cur=$(gh pr view "$pr" --repo "$repo" --json headRefOid -q .headRefOid)
  [ "$cur" = "$head" ] && break
  sleep "$sleep_s"
done
[ "$cur" = "$head" ] || { echo "ABORT head moved: $cur != reviewed $head"; exit 2; }

n=0 bad=""
for i in $(seq 1 "${MAX_WAIT_POLLS:-120}"); do
  cur=$(gh pr view "$pr" --repo "$repo" --json headRefOid -q .headRefOid)
  [ "$cur" = "$head" ] || { echo "ABORT head moved: $cur != reviewed $head"; exit 2; }
  roll=$(rollup)
  n=$(python3 -c 'import json,sys;print(len(json.load(sys.stdin)))' <<<"$roll")
  bad=$(python3 -c 'import json,sys;print(",".join(x for x in json.load(sys.stdin) if x not in ("SUCCESS","SKIPPED","NEUTRAL")))' <<<"$roll")
  if [ "$n" = 0 ]; then
    [ "${ALLOW_NO_CI:-}" = 1 ] && [ "$i" -ge 20 ] && break
  elif [ -z "$bad" ]; then break
  elif grep -qE 'FAILURE|CANCELLED|TIMED_OUT|ACTION_REQUIRED|ERROR' <<<"$bad"; then echo "ABORT CI: $bad"; exit 3
  fi
  sleep "$sleep_s"
done
[ "$n" != 0 ] && [ -n "$bad" ] && { echo "ABORT CI timeout: $bad"; exit 4; }
[ "$n" = 0 ] && [ "${ALLOW_NO_CI:-}" != 1 ] && { echo "ABORT no checks ran; set ALLOW_NO_CI=1 with local evidence"; exit 5; }

gh pr merge "$pr" --repo "$repo" --squash --match-head-commit "$head" || { echo "ABORT merge failed"; exit 6; }
merged=$(gh pr view "$pr" --repo "$repo" --json mergeCommit -q .mergeCommit.oid)
if [ "${POST_MERGE_CI:-1}" != 0 ]; then
  bad=1
  for i in $(seq 1 "${POST_MERGE_POLLS:-60}"); do
    nruns=$(gh run list --repo "$repo" --commit "$merged" --limit 20 --json status -q 'length')
    bad=$(gh run list --repo "$repo" --commit "$merged" --limit 20 --json status,conclusion \
      -q '[.[] | select(.status != "completed" or (.conclusion != "success" and .conclusion != "skipped"))] | length')
    if [ "$bad" = 0 ] && { [ "$nruns" != 0 ] || [ "$i" -ge 4 ]; }; then break; fi
    sleep "$sleep_s"
  done
  [ "$bad" = 0 ] || { echo "ABORT base CI not green for $merged (merged)"; exit 7; }
fi
echo "MERGED $repo#$pr merged=$merged"
