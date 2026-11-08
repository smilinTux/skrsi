#!/usr/bin/env bash
# remote-test.sh <worktree> [cargo args...]
#
# Builds and tests a Rust worktree on a bigger build host over ssh, so a lane does not fight a small laptop for
# cores. Nothing here touches anything on the host except its own build directory and one throwaway container.
#   - The worktree is rsynced (no .git, no target) into $REMOTE_ROOT/src/<worktree name>.
#   - ONE shared CARGO_TARGET_DIR ($REMOTE_ROOT/target) keeps rebuilds incremental across lanes.
#   - If PG_IMAGE is set, a THROWAWAY Postgres container on a non-default loopback port with tmpfs storage serves
#     the database tests. A real database on the host is never touched, and the default Postgres port is never used.
#   - A disk-space floor refuses to start a build on a nearly full host.
#
# Required env:
#   REMOTE_HOST       ssh target (user@host or an ssh config alias). No default: a host is never baked in.
# Optional env:
#   REMOTE_ROOT       build root relative to the remote home. Default: lane-build.
#   CARGO_ARGS_DEFAULT  words used when no cargo args are given. Default: "test --locked --all-targets".
#   DISK_FLOOR_GB     minimum free GB in the remote home. Default: 15. Exit 6 below it.
#   PG_IMAGE          container image for the throwaway database (pin by digest). Unset: no database is started.
#   PG_NAME           container name. Default: lane-pg-test.
#   PG_PORT           loopback port on the host. Default: 55433.
#   PG_TMPFS_SIZE     tmpfs size. Default: 2g.
#   PG_PRELOAD        value for shared_preload_libraries (for example "pg_search,age"). Default: none.
#   PG_PASSWORD       throwaway database password. Default: throwaway-test-only.
#   TEST_DSN_ENV      name of the env var that receives the throwaway DSN for the tests. Default: TEST_PG_DSN.
#   REMOTE_ENV        extra "NAME=value" words exported for the cargo run (for example flags that make the
#                     database tests mandatory instead of skipped).
#   REMOTE_UNSET_ENV  env var names to unset for the run so a real database on the host is never used.
#   NICE_LEVEL        nice level for cargo. Default: 5.
#   SSH_OPTS          extra ssh options. BatchMode is always on.
set -euo pipefail
: "${REMOTE_HOST:?set REMOTE_HOST to the ssh target of the build host}"
wt=$(readlink -f "${1:?worktree}"); shift
root=${REMOTE_ROOT:-lane-build}; name=$(basename "$wt"); dst=$root/src/$name
# shellcheck disable=SC2206
ssh_opts=(-o BatchMode=yes ${SSH_OPTS:-})
ssh "${ssh_opts[@]}" "$REMOTE_HOST" "mkdir -p ~/$dst ~/$root/target"
rsync -a --delete --exclude target --exclude .git -e "ssh ${ssh_opts[*]}" "$wt/" "$REMOTE_HOST:$dst/"
# Parameters travel as shell-quoted assignments ahead of the script on stdin: ssh joins argv into one string, which
# would drop empty values and split values that hold spaces.
q() { printf '%q' "$1"; }
{
  for kv in "dst=$dst" "root=$root" "floor=${DISK_FLOOR_GB:-15}" "img=${PG_IMAGE:-}" "pgname=${PG_NAME:-lane-pg-test}" \
    "pgport=${PG_PORT:-55433}" "tmpfs=${PG_TMPFS_SIZE:-2g}" "preload=${PG_PRELOAD:-}" "pgpw=${PG_PASSWORD:-throwaway-test-only}" \
    "dsnenv=${TEST_DSN_ENV:-TEST_PG_DSN}" "renv=${REMOTE_ENV:-}" "runset=${REMOTE_UNSET_ENV:-}" "nice_l=${NICE_LEVEL:-5}" \
    "defargs=${CARGO_ARGS_DEFAULT:-test --locked --all-targets}"; do
    printf '%s=%s\n' "${kv%%=*}" "$(q "${kv#*=}")"
  done
  printf 'set --'; for a in "$@"; do printf ' %s' "$(q "$a")"; done; printf '\n'
  cat <<'REMOTE'
set -euo pipefail
[ $# -gt 0 ] || set -- $defargs
avail=$(df --output=avail -BG ~ | tail -1 | tr -dc 0-9)
[ "$avail" -ge "$floor" ] || { echo "ABORT build host disk ${avail}G free (< ${floor}G floor)"; exit 6; }
envs=("CARGO_TARGET_DIR=$HOME/$root/target")
if [ -n "$img" ]; then
  if ! docker ps --format '{{.Names}}' | grep -qx "$pgname"; then
    docker rm -f "$pgname" >/dev/null 2>&1 || true
    args=(-d --name "$pgname" --tmpfs "/var/lib/postgresql/data:rw,size=$tmpfs" -e PGDATA=/var/lib/postgresql/data/pg
      -e "POSTGRES_PASSWORD=$pgpw" -p "127.0.0.1:$pgport:5432" "$img" postgres)
    [ -z "$preload" ] || args+=(-c "shared_preload_libraries=$preload")
    docker run "${args[@]}" >/dev/null
    for _ in $(seq 1 60); do docker exec "$pgname" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break; sleep 2; done
  fi
  envs+=("$dsnenv=postgresql://postgres:$pgpw@127.0.0.1:$pgport/postgres")
fi
for w in $renv; do envs+=("$w"); done
unset_args=(); for v in $runset; do unset_args+=(-u "$v"); done
cd ~/"$dst"
env ${unset_args[@]+"${unset_args[@]}"} "${envs[@]}" nice -n "$nice_l" cargo "$@"
REMOTE
} | ssh "${ssh_opts[@]}" "$REMOTE_HOST" bash -s
