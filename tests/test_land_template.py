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
  "run list") f=$d/runs.json ;;
  "run view") cat "$d/log.txt"; exit 0 ;;
  "pr merge") echo merged >>"$d/merged"; exit 0 ;;
esac
if [[ "$*" == *mergeCommit* ]]; then echo deadbeef; exit 0; fi
jq -r "$expr" "$f"
"""


class LandTemplate(unittest.TestCase):
    def run_land(self, rollup, head="abc", log="== 12 passed in 3s ==", reviewed="abc", extra=None, wt=None, cwd=None):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "view.json").write_text(json.dumps({"headRefOid": head, "statusCheckRollup": rollup}))
            (d / "runs.json").write_text(json.dumps([{"databaseId": 1, "conclusion": "success"}]))
            (d / "log.txt").write_text(log)
            gh = d / "bin" / "gh"
            gh.parent.mkdir()
            gh.write_text(FAKE_GH)
            gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
            env = {**os.environ, "PATH": f"{gh.parent}:{os.environ['PATH']}", "FAKE_DIR": str(d),
                   "LANE_STATE_DIR": str(d / "state"), "MAX_WAIT_POLLS": "2", "POLL_SECONDS": "0",
                   "LAND_ROUND_CMD": f'echo "$LAND_ROUND" > "{d}/round"',
                   "LAND_ACCEPT_CMD": f'echo "$LAND_MERGED" > "{d}/accepted"', **(extra or {})}
            proc = subprocess.run(["bash", str(ROOT / "templates/land.sh"), "o/r", "7", reviewed, *([wt] if wt else [])],
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

        # a stale registration (tree deleted without worktree remove) must not break the next land
        stale = Path(tempfile.mkdtemp()) / "stale-localrun"
        subprocess.run(["git", "-C", str(r), "worktree", "add", "-q", "--detach", str(stale), sha], check=True)
        shutil.rmtree(stale)
        proc, merged, *_ = run('echo "=== 2 passed in 1s ==="')
        self.assertEqual((proc.returncode, merged), (0, True), proc.stdout + proc.stderr)

        # an unusable head gives a clear reason, not a silent exit 5
        proc, merged, *_ = self.run_land(done, log="nothing ran", reviewed="f" * 40, head="f" * 40,
                                         extra={"LOCAL_TEST_CMD": "true"}, wt=str(self.make_pr_worktree(r, 99)), cwd=str(r))
        self.assertEqual((proc.returncode, merged), (5, False))
        self.assertIn("could not check out", proc.stdout)
        self.assertEqual([n for n in self.state_left if "localrun" in n], [])

        proc, merged, *_ = run(None)  # unset: unchanged
        self.assertEqual((proc.returncode, merged), (5, False))


if __name__ == "__main__":
    unittest.main()
