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
    def run_land(self, rollup, head="abc", log="== 12 passed in 3s ==", reviewed="abc"):
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
                   "LAND_ACCEPT_CMD": f'echo "$LAND_MERGED" > "{d}/accepted"'}
            proc = subprocess.run(["bash", str(ROOT / "templates/land.sh"), "o/r", "7", reviewed],
                                  env=env, capture_output=True, text=True)
            calls = (d / "calls").read_text() if (d / "calls").exists() else ""
            self.merge_call = next((c for c in calls.splitlines() if c.startswith("pr merge")), "")
            self.round = (d / "round").read_text().strip() if (d / "round").exists() else None
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


if __name__ == "__main__":
    unittest.main()
