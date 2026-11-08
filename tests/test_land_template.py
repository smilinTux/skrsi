import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_GH = r"""#!/usr/bin/env bash
# Minimal gh: serves canned JSON through the real jq expression the script passes with -q.
d=$FAKE_DIR; echo "$*" >>"$d/calls"
expr=""; for ((i=1;i<=$#;i++)); do [ "${!i}" = "-q" ] && { j=$((i+1)); expr=${!j}; }; done
case "$1 $2" in
  "pr view") f=$d/view.json ;;
  "run list") f=$d/runs.json; [[ "$*" == *"--commit deadbeef"* && -f $d/runs-merged.json ]] && f=$d/runs-merged.json ;;
  "run view") cat "$d/log.txt"; exit 0 ;;
  "pr merge") echo merged >>"$d/merged"; exit 0 ;;
esac
if [[ "$*" == *mergeCommit* ]]; then echo deadbeef; exit 0; fi
jq -r "$expr" "$f"
"""


class LandTemplate(unittest.TestCase):
    def run_land(self, rollup, head="abc", log="== 12 passed in 3s ==", reviewed="abc", extra=None, wt=None, cwd=None, keep=None, runs=None, merged_runs=None, script="land.sh", args=None):
        with tempfile.TemporaryDirectory() as d:
            d = Path(keep or d)
            d.mkdir(exist_ok=True)
            (d / "view.json").write_text(json.dumps({"headRefOid": head, "statusCheckRollup": rollup}))
            (d / "runs.json").write_text(json.dumps(runs or [{"databaseId": 1, "status": "completed", "conclusion": "success"}]))
            (d / "log.txt").write_text(log)
            if merged_runs is not None:
                (d / "runs-merged.json").write_text(json.dumps(merged_runs))
            gh = d / "bin" / "gh"
            gh.parent.mkdir(exist_ok=True)
            gh.write_text(FAKE_GH)
            gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
            env = {**os.environ, "PATH": f"{gh.parent}:{os.environ['PATH']}", "FAKE_DIR": str(d),
                   "LANE_STATE_DIR": str(d / "state"), "MAX_WAIT_POLLS": "2", "POLL_SECONDS": "0", "HEAD_WAIT_POLLS": "2", "POST_MERGE_POLLS": "3",
                   "LAND_ROUND_CMD": f'echo "$LAND_ROUND" > "{d}/round"',
                   "LAND_ACCEPT_CMD": f'echo "$LAND_MERGED" > "{d}/accepted"', "LAND_READBACK_CMD": "echo status=accepted", **(extra or {})}
            proc = subprocess.run(["bash", os.environ.get("LAND_SH_UNDER_TEST", str(ROOT / "templates" / script)), *(args or ["o/r"]), "7", reviewed, *([wt] if wt else [])],
                                  env=env, capture_output=True, text=True, cwd=cwd)
            calls = (d / "calls").read_text() if (d / "calls").exists() else ""
            self.merge_call = next((c for c in calls.splitlines() if c.startswith("pr merge")), "")
            self.round = (d / "round").read_text().strip() if (d / "round").exists() else None
            self.evidence = next((p.read_text() for p in (d / "state").glob("*-tests.log")), "")
            self.state_left = sorted(p.name for p in (d / "state").iterdir()) if (d / "state").exists() else []
            return proc, (d / "merged").exists(), (d / "accepted").exists(), (d / "state").exists()

    @unittest.skipUnless(shutil.which("jq"), "jq needed to exercise the real status expression")
    def test_land_gates(self):
        done = [{"status": "COMPLETED", "conclusion": "SUCCESS"}, {"status": "COMPLETED", "conclusion": "SKIPPED"}]
        proc, merged, accepted, _ = self.run_land(done)
        self.assertEqual((proc.returncode, merged, accepted), (0, True, True), proc.stdout + proc.stderr)
        self.assertIn("--match-head-commit abc", self.merge_call)
        self.assertEqual(self.round, "7 abc PASS")  # the landing round is recorded in the board format

        # an in-progress check has an empty conclusion: pending, never success
        proc, merged, *_ = self.run_land(done + [{"status": "IN_PROGRESS", "conclusion": ""}])
        self.assertEqual((proc.returncode, merged), (4, False), proc.stdout)

        proc, merged, *_ = self.run_land(done + [{"status": "COMPLETED", "conclusion": "FAILURE"}])
        self.assertEqual((proc.returncode, merged), (3, False))

        # green checks without test output: a conflicting PR runs no tests
        proc, merged, *_ = self.run_land(done, log="nothing ran")
        self.assertEqual((proc.returncode, merged), (5, False))

        proc, merged, *_ = self.run_land(done, head="moved")
        self.assertEqual((proc.returncode, merged), (2, False))

    def make_repo(self):
        r = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, r, True)
        g = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, text=True).stdout.strip()
        g("init", "-q")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
        return r, g("rev-parse", "HEAD")

    def make_pr_worktree(self, r, n):
        w = r.parent / f"{r.name}-pr{n}"
        subprocess.run(["git", "-C", str(r), "worktree", "add", "-q", "-b", f"pr{n}", str(w)], check=True)
        self.addCleanup(shutil.rmtree, w, True)
        return w

    @unittest.skipUnless(shutil.which("jq"), "jq needed")
    def test_local_evidence(self):
        done = [{"status": "COMPLETED", "conclusion": "SUCCESS"}]
        r, sha = self.make_repo()
        cmd = 'test ! -e untracked-edit && echo "=== 4 passed in 1s ==="'
        def run(c):
            w = self.make_pr_worktree(r, next(self.n))
            (w / "untracked-edit").write_text("worker edit")  # must not leak into the detached checkout
            return self.run_land(done, log="nothing ran", reviewed=sha, head=sha,
                                 extra={"LOCAL_TEST_CMD": c} if c else None, wt=str(w), cwd=str(r))
        self.n = iter(range(100))
        proc, merged, *_ = run(cmd)
        self.assertEqual((proc.returncode, merged), (0, True), proc.stdout + proc.stderr)
        self.assertIn("4 passed in 1s ===" + " (local run)", self.evidence)
        self.assertIn("local_test_cmd=", self.evidence)
        self.assertEqual([n for n in self.state_left if "localrun" in n], [])  # detached worktree removed
        self.assertEqual(subprocess.run(["git", "-C", str(r), "worktree", "list"], capture_output=True, text=True).stdout.count("\n"), 1)  # only the main repo remains

        proc, merged, *_ = run('echo "=== 1 failed, 3 passed in 1s ==="; exit 1')
        self.assertEqual((proc.returncode, merged), (5, False), proc.stdout)
        proc, merged, *_ = run('echo "=== 1 failed, 3 passed in 1s ==="')  # failed line refused even on exit 0
        self.assertEqual((proc.returncode, merged), (5, False), proc.stdout)
        self.assertEqual([n for n in self.state_left if "localrun" in n], [])

        proc, merged, *_ = run('echo "=== 0 passed, 3 skipped in 1s ==="')  # zero passed is no evidence
        self.assertEqual((proc.returncode, merged), (5, False), proc.stdout)
        proc, merged, *_ = run('echo "=== 10 passed, 3 skipped in 1s ==="')
        self.assertEqual((proc.returncode, merged), (0, True), proc.stdout + proc.stderr)

        # round-1 defect: land.sh SIGKILLed mid-run leaves its detached worktree registered, trap never runs
        keep = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, keep, True)
        w1 = self.make_pr_worktree(r, 50)
        kw = dict(log="nothing ran", reviewed=sha, head=sha, cwd=str(r), keep=str(keep))
        # the command's parent is land.sh's command-substitution subshell; its parent is land.sh itself
        killer = 'kill -9 $(ps -o ppid= -p $PPID | tr -d " ")'
        proc, merged, *_ = self.run_land(done, extra={"LOCAL_TEST_CMD": killer}, wt=str(w1), **kw)
        self.assertEqual((proc.returncode, merged), (-9, False), proc.stdout + proc.stderr)
        listed = subprocess.run(["git", "-C", str(r), "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout
        self.assertIn("localrun", listed)  # the killed land really left its registration behind
        w2 = self.make_pr_worktree(r, 51)
        proc, merged, *_ = self.run_land(done, extra={"LOCAL_TEST_CMD": 'echo "=== 2 passed in 1s ==="'}, wt=str(w2), **kw)
        self.assertEqual((proc.returncode, merged), (0, True), proc.stdout + proc.stderr)
        self.assertIn("(local run)", self.evidence)
        listed = subprocess.run(["git", "-C", str(r), "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout
        self.assertNotIn("prunable", listed)
        self.assertNotIn("localrun", listed)

        # an unusable head gives a clear reason, not a silent exit 5
        proc, merged, *_ = self.run_land(done, log="nothing ran", reviewed="f" * 40, head="f" * 40,
                                         extra={"LOCAL_TEST_CMD": "true"}, wt=str(self.make_pr_worktree(r, 99)), cwd=str(r))
        self.assertEqual((proc.returncode, merged), (5, False))
        self.assertIn("could not check out", proc.stdout)
        self.assertEqual([n for n in self.state_left if "localrun" in n], [])

        proc, merged, *_ = run(None)  # unset: unchanged
        self.assertEqual((proc.returncode, merged), (5, False))

    @unittest.skipUnless(shutil.which("jq"), "jq needed")
    def test_dedupe_and_new_gates(self):
        ok = {"workflowName": "ci", "name": "t", "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": "2026-01-02"}
        # a cancelled duplicate of the same check, older than the green re-run, does not abort the land
        stale = {**ok, "conclusion": "CANCELLED", "startedAt": "2026-01-01"}
        proc, merged, accepted, _ = self.run_land([stale, ok])
        self.assertEqual((proc.returncode, merged, accepted), (0, True, True), proc.stdout + proc.stderr)
        self.assertIn("READBACK status=accepted", proc.stdout)
        # the LATEST run being cancelled still aborts
        proc, merged, *_ = self.run_land([ok, {**stale, "startedAt": "2026-01-03"}])
        self.assertEqual((proc.returncode, merged), (3, False), proc.stdout)
        # different checks that merely share no name are not collapsed into one
        proc, merged, *_ = self.run_land([{"status": "COMPLETED", "conclusion": "SUCCESS"}, {"status": "COMPLETED", "conclusion": "FAILURE"}])
        self.assertEqual((proc.returncode, merged), (3, False))
        done = [ok]
        # cargo evidence is accepted, zero passed is not
        proc, merged, *_ = self.run_land(done, log="2026 test result: ok. 31 passed; 0 failed; 0 ignored")
        self.assertEqual((proc.returncode, merged), (0, True), proc.stdout)
        self.assertIn("test result: ok. 31 passed; 0 failed", self.evidence)
        proc, merged, *_ = self.run_land(done, log="test result: ok. 0 passed; 0 failed")
        self.assertEqual((proc.returncode, merged), (5, False))
        # base CI red after the merge: merged, hook not run, exit 7
        proc, merged, accepted, _ = self.run_land(done, merged_runs=[{"status": "completed", "conclusion": "failure"}])
        self.assertEqual((proc.returncode, merged, accepted), (7, True, False), proc.stdout)
        proc, merged, accepted, _ = self.run_land(done, merged_runs=[{"status": "in_progress", "conclusion": None}])
        self.assertEqual((proc.returncode, accepted), (7, False), proc.stdout)  # still running at the deadline
        proc, merged, accepted, _ = self.run_land(done, merged_runs=[{"status": "completed", "conclusion": "failure"}], extra={"POST_MERGE_CI": "0"})
        self.assertEqual((proc.returncode, accepted), (0, True))
        # a bare repo name needs the owner variable
        proc, merged, *_ = self.run_land(done, args=["r"])
        self.assertNotEqual(proc.returncode, 0)
        proc, merged, *_ = self.run_land(done, args=["r"], extra={"LANE_REPO_OWNER": "o"})
        self.assertEqual((proc.returncode, merged), (0, True), proc.stdout + proc.stderr)
        # readback failure is reported
        proc, merged, *_ = self.run_land(done, extra={"LAND_READBACK_CMD": "exit 1"})
        self.assertEqual(proc.returncode, 9)
        # accept hook failing with no repo to compare against stops with 8 and leaves the card open
        proc, merged, *_ = self.run_land(done, extra={"LAND_ACCEPT_CMD": "exit 1"})
        self.assertEqual((proc.returncode, merged), (8, True), proc.stdout)

    @unittest.skipUnless(shutil.which("jq"), "jq needed")
    def test_merge_script(self):
        ok = {"workflowName": "ci", "name": "t", "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": "2026-01-02"}
        stale = {**ok, "conclusion": "CANCELLED", "startedAt": "2026-01-01"}
        proc, merged, *_ = self.run_land([stale, ok], script="merge.sh")
        self.assertEqual((proc.returncode, merged), (0, True), proc.stdout + proc.stderr)
        self.assertIn("--match-head-commit abc", self.merge_call)
        self.assertIn("MERGED o/r#7", proc.stdout)
        proc, merged, *_ = self.run_land([ok, {"status": "IN_PROGRESS", "conclusion": ""}], script="merge.sh")
        self.assertEqual((proc.returncode, merged), (4, False), proc.stdout)
        proc, merged, *_ = self.run_land([ok, {**ok, "name": "u", "conclusion": "FAILURE"}], script="merge.sh")
        self.assertEqual((proc.returncode, merged), (3, False))
        proc, merged, *_ = self.run_land([ok], head="moved", script="merge.sh")
        self.assertEqual((proc.returncode, merged), (2, False))
        proc, merged, *_ = self.run_land([], script="merge.sh")  # no checks at all
        self.assertEqual((proc.returncode, merged), (5, False))
        proc, merged, *_ = self.run_land([ok], script="merge.sh", merged_runs=[{"status": "completed", "conclusion": "failure"}])
        self.assertEqual((proc.returncode, merged), (7, True))


class RemoteRunner(unittest.TestCase):
    def test_remote_runner_parameters(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bin_ = d / "bin"
            bin_.mkdir()
            home = d / "home"
            home.mkdir()
            # ssh fake: ignores options and host, runs the remote command locally as the "remote" shell
            (bin_ / "ssh").write_text('#!/usr/bin/env bash\nwhile [ "${1:0:1}" = "-" ]; do shift; [ "$1" = "BatchMode=yes" ] && shift; done\nshift\nexec bash -c "$*"\n')
            (bin_ / "rsync").write_text('#!/usr/bin/env bash\necho "rsync $*" >>"$FAKE_DIR/calls"\n')
            (bin_ / "cargo").write_text('#!/usr/bin/env bash\necho "cargo $* DSN=${MYDSN:-} T=${CARGO_TARGET_DIR} FLAG=${FLAG:-} SECRET=${SECRET:-unset}" >>"$FAKE_DIR/calls"\n')
            (bin_ / "docker").write_text('#!/usr/bin/env bash\necho "docker $*" >>"$FAKE_DIR/calls"\n')
            for f in bin_.iterdir():
                f.chmod(0o755)
            wt = d / "wt"
            wt.mkdir()
            env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "FAKE_DIR": str(d), "HOME": str(home), "REMOTE_HOST": "buildhost"}
            run = lambda extra, *a: subprocess.run(["bash", str(ROOT / "templates/remote-test.sh"), str(wt), *a], env={**env, **extra},
                                                   capture_output=True, text=True)
            self.assertNotEqual(run({"REMOTE_HOST": ""}).returncode, 0)  # no baked-in host
            p = run({"PG_IMAGE": "img@sha256:x", "PG_PORT": "55999", "TEST_DSN_ENV": "MYDSN", "REMOTE_ENV": "FLAG=1",
                     "REMOTE_UNSET_ENV": "SECRET", "SECRET": "live", "DISK_FLOOR_GB": "0"}, "clippy", "--", "-D", "warnings")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            calls = (d / "calls").read_text()
            self.assertIn("127.0.0.1:55999:5432", calls)
            self.assertIn("cargo clippy -- -D warnings", calls)
            self.assertIn("postgresql://postgres:throwaway-test-only@127.0.0.1:55999/postgres", calls)
            self.assertIn("FLAG=1 SECRET=unset", calls)  # the real value is unset for the run
            self.assertIn(f"T={home}/lane-build/target", calls)
            self.assertNotIn("5432:5432", calls)
            (d / "calls").unlink()
            p = run({"DISK_FLOOR_GB": "0"})  # default args, no database started
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            calls = (d / "calls").read_text()
            self.assertIn("cargo test --locked --all-targets", calls)
            self.assertNotIn("docker", calls)
            p = run({"DISK_FLOOR_GB": "99999999"})  # the floor refuses
            self.assertEqual(p.returncode, 6, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
