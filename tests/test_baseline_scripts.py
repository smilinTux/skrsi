"""Baseline scripts (card 3611ea28, P0.1).

Every fixture is built here with hand-computed expected numbers. No real host, path or
credential appears: the planted markers prove the scripts never copy content.
"""

import datetime as dt
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import baseline_cardstore as cardstore
from scripts import baseline_cron as cron
from scripts import baseline_gateway as gateway
from scripts import baseline_github as github
from scripts import baseline_itil as itil
from scripts import baseline_sessions as sessions
from scripts import baseline_skmemory as skmemory
from scripts import baseline_skwhisper as skwhisper
from scripts.instance_text import host_shaped, text_findings

ROOT = Path(__file__).resolve().parents[1]
PLANTED = "PLANTED-CONTENT-MARKER"


def write_lines(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def assert_clean(case, doc):
    """No script output may carry prompt text, DSNs, tokens, addresses or host-shaped names."""
    text = json.dumps(doc)
    case.assertNotIn(PLANTED, text)
    case.assertEqual(text_findings(text), [])
    case.assertEqual(host_shaped(text, set()), [])
    for needle in ("postgres://", "postgresql://", "Bearer ", "ghp_", "sk-ant", "/home/"):
        case.assertNotIn(needle, text)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


def turn(uid, req, ts, model, inp, cw, cr, out, sid):
    usage = {"input_tokens": inp, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
             "output_tokens": out}
    return {"type": "assistant", "uuid": uid, "requestId": req, "sessionId": sid, "timestamp": ts,
            "message": {"id": "m" + req, "model": model, "usage": usage,
                        "content": [{"type": "text", "text": PLANTED}]}}


class Sessions(Fixture):
    def build(self):
        a = self.dir / "p1" / "s-a.jsonl"
        write_lines(a, [
            turn("u1", "r1", "2026-11-10T10:00:00Z", "claude-opus-5-5", 10, 90000, 0, 100, "s-a"),
            turn("u2", "r1", "2026-11-10T10:00:01Z", "claude-opus-5-5", 10, 90000, 0, 100, "s-a"),
            turn("u3", "r2", "2026-11-10T10:05:00Z", "claude-opus-5-5", 5, 1000, 249000, 200, "s-a"),
            turn("u4", "r3", "2026-11-10T10:10:00Z", "claude-sonnet-5-5", 5, 0, 99995, 300, "s-a"),
            {"type": "user", "message": {"content": PLANTED}},
            {"type": "assistant", "uuid": "nu", "timestamp": "2026-11-10T11:00:00Z", "message": {"content": PLANTED}},
            "{not json",
        ])
        write_lines(self.dir / "p2" / "s-b.jsonl", [
            turn("u5", "r4", "2026-11-11T23:59:59Z", "claude-opus-5-5", 0, 0, 50000, 400, "s-b"),
            turn("u6", "r5", "2026-11-12T00:00:00Z", "claude-opus-5-5", 1, 1, 1, 1, "s-b"),
            turn("u7", "r6", "2026-11-04T23:59:59Z", "claude-opus-5-5", 1, 1, 1, 1, "s-c"),
        ])
        write_lines(self.dir / "p2" / "sub" / "deep.jsonl", [turn("u8", "r7", "2026-11-10T10:00:00Z", "m", 9, 9, 9, 9, "s-d")])
        return sessions.compute(self.dir, "2026-11-11", 7, "opus")

    def test_record_basis_matches_hand_computed(self):
        r = self.build()["records"]
        self.assertEqual((r["turns"], r["sessions"]), (5, 2))
        self.assertEqual((r["cache_read_tokens"], r["cache_write_tokens"], r["output_tokens"]), (398995, 181000, 1100))
        self.assertEqual(r["output_share_pct"], 72.7)
        self.assertEqual((r["context_turn_median"], r["context_turn_p90"]), (90010, 250005))
        self.assertEqual((r["context_start_median"], r["context_start_p90"]), (70005, 90010))
        self.assertEqual(r["input_share_above_200k_pct"], 43.1)
        self.assertEqual((r["turns_per_session_median"], r["turns_per_session_p90"], r["turns_per_session_max"]), (2.5, 4, 4))

    def test_request_basis_dedupes_by_request_id(self):
        r = self.build()["requests"]
        self.assertEqual((r["turns"], r["sessions"], r["cache_write_tokens"], r["output_tokens"]), (4, 2, 91000, 1000))
        self.assertEqual(r["output_share_pct"], 70.0)
        self.assertEqual((r["context_turn_median"], r["context_turn_p90"]), (95005, 250005))
        self.assertEqual(r["input_share_above_200k_pct"], 51.0)
        self.assertEqual((r["turns_per_session_median"], r["turns_per_session_max"]), (2.0, 3))

    def test_output_never_carries_content(self):
        assert_clean(self, self.build())


class Gateway(Fixture):
    def build(self):
        db = self.dir / "metrics.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE request_log (id TEXT, model TEXT, status_code INTEGER, started_at INTEGER, "
                    "agent_id TEXT, session_id TEXT, client TEXT, cost_truth TEXT)")

        def ms(s):
            return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        rows = [("sk-default", 200, "2026-11-06T00:00:00", "unknown"), ("sk-default", 200, "2026-11-07T00:00:00", "unknown"),
                ("sk-default", 200, "2026-11-08T00:00:00", "unknown"), ("sk-default", 502, "2026-11-09T00:00:00", "unknown"),
                ("sk-default", 502, "2026-11-10T00:00:00", "unknown"), ("sk-default", 502, "2026-11-04T00:00:00", "unknown"),
                ("unknown", 404, "2026-11-10T00:00:00", "unknown"), (None, 404, "2026-11-10T01:00:00", "unknown"),
                ("sk-yt", 502, "2026-11-10T02:00:00", "estimated"), ("sk-default", 200, "2026-11-12T00:00:00", "unknown")]
        for i, (m, s, t, c) in enumerate(rows):
            con.execute("INSERT INTO request_log VALUES (?,?,?,?,?,?,?,?)",
                        (str(i), m, s, ms(t), "x" if i == 8 else None, None, None, c))
        con.commit()
        con.close()
        return gateway.compute(db, "2026-11-11", 7, "sk-default")

    def test_route_status_and_null_columns(self):
        r = self.build()
        self.assertEqual((r["route_requests"], r["route_502"], r["route_502_pct"]), (5, 2, 40.0))
        self.assertEqual(r["no_model_404"], 2)
        assert_clean(self, r)
        self.assertEqual((r["rows_total"], r["rows_cost_unknown"]), (9, 8))
        self.assertEqual((r["rows_agent_id_set"], r["rows_session_id_set"], r["rows_client_set"]), (1, 0, 0))

    def test_database_is_opened_read_only(self):
        self.build()
        con = gateway.open_ro(self.dir / "metrics.db")
        with self.assertRaises(sqlite3.OperationalError):
            con.execute("DELETE FROM request_log")

    def test_build_fact_from_git(self):
        repo = self.dir / "repo"
        repo.mkdir()
        env = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]

        def git(*a):
            subprocess.run(["git", *env, "-C", str(repo), *a], check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        for n in "123":
            (repo / "f").write_text(n)
            git("add", "f")
            git("commit", "-q", "-m", n)
        git("checkout", "-q", "-b", "deploy", "HEAD~2")
        (repo / "g").write_text("d")
        git("add", "g")
        git("commit", "-q", "-m", "d")
        fact = gateway.build_fact(repo, "main")
        self.assertEqual((fact["commits_behind_main"], fact["is_ancestor_of_main"]), (2, False))
        self.assertEqual(len(fact["build_sha"]), 7)


class Cron(Fixture):
    def build(self, host=None):
        def run(job, h, start, ok):
            return {"job": job, "host": h, "start": start, "ok": ok, "tail": PLANTED}
        rows = [run("a", "h1", f"2026-11-0{i + 1}T00:00:00+00:00", ok) for i, ok in
                enumerate([True, False, False, False, False, False, True, False])]
        rows += [run("b", "h1", f"2026-11-0{i + 1}T01:00:00+00:00", ok) for i, ok in
                 enumerate([True, True, True, False, False])]
        rows += [run("c", "h1", f"2026-11-0{i + 1}T02:00:00+00:00", False) for i in range(7)]
        rows += [run("c", "h1", "2026-10-12T23:59:59+00:00", False), run("d", "h2", "2026-11-05T05:00:00+00:00", True),
                 run("a", "h1", "2026-11-11T20:00:00-05:00", False), "{not json"]
        path = write_lines(self.dir / "ledger.jsonl", rows)
        return cron.compute(path, "2026-11-11", 30, host)

    def test_runs_failures_and_streaks(self):
        r = self.build()
        self.assertEqual((r["runs"], r["failed"], r["failed_pct"], r["jobs_total"]), (21, 15, 71.43, 4))
        self.assertEqual((r["jobs_streak_ge_5"], r["longest_streaks"]), (2, [7, 5, 2]))
        assert_clean(self, r)

    def test_host_filter(self):
        r = self.build("h1")
        self.assertEqual((r["runs"], r["failed"], r["failed_pct"]), (20, 15, 75.0))

    def test_cli_prints_one_json_document(self):
        self.build()
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "baseline_cron.py"), "--ledger",
                              str(self.dir / "ledger.jsonl"), "--end", "2026-11-11", "--days", "30"],
                             check=True, capture_output=True, text=True).stdout
        doc = json.loads(out)
        self.assertEqual((doc["window_days"], doc["window_end"], doc["runs"]), (30, "2026-11-11", 21))


class Github(Fixture):
    def build(self):
        def pr(title, merged):
            return {"number": 1, "title": title, "mergedAt": merged}
        alpha = [pr("fix(x): a", "2026-11-01T00:00:00Z"), pr("feat: b", "2026-11-02T00:00:00Z"),
                 pr("Fix: c", "2026-11-11T23:59:59Z"), pr("fixture cleanup", "2026-11-03T00:00:00Z"),
                 pr("fix: d", "2026-10-28T23:59:59Z"), pr("fix: open", None)]
        beta = [pr(f"fix: {i}", "2026-11-05T00:00:00Z") for i in range(8)]
        write_lines(self.dir / "alpha.json", [json.dumps(alpha)])
        write_lines(self.dir / "beta.json", [json.dumps(beta)])
        return github.compute({"alpha": self.dir / "alpha.json", "beta": self.dir / "beta.json"}, "2026-11-11", 14)

    def test_fix_share_per_repo(self):
        r = self.build()["repos"]
        assert_clean(self, {"repos": r})
        self.assertEqual((r["alpha"]["merged"], r["alpha"]["fix"], r["alpha"]["fix_pct"]), (4, 2, 50.0))
        self.assertEqual((r["beta"]["merged"], r["beta"]["fix"], r["beta"]["fix_pct"]), (8, 8, 100.0))


class Cardstore(Fixture):
    def test_verdicts_and_review_rounds(self):
        def ev(card, key, val, ts):
            return {"card_id": card, "action": "link", "ts": ts, "link_key": key, "link_value": val}
        write_lines(self.dir / "ev" / "n1.jsonl", [
            ev("c2", "review_round", f"2 ccc PASS", "2026-11-02T00:00:00+00:00"),
            ev("c1", "review_round", "1 aaa PASS", "2026-11-01T00:00:00+00:00"),
            ev("c1", "verdict", "PASS", "2026-11-01T00:00:00+00:00"),
            ev("c3", "review_round", "3 ddd PASS", "2026-11-03T00:00:00+00:00"),
            ev("c4", "review_round", "4 eee PASS", "2026-11-12T00:00:01+00:00"),
            {"card_id": "c1", "action": "move", "ts": "2026-11-01T00:00:00+00:00"}, "{not json"])
        write_lines(self.dir / "ev" / "n2.jsonl", [
            ev("c2", "review_round", f"2 bbb FAIL {PLANTED}", "2026-11-01T12:00:00+00:00"),
            ev("c2", "verdict", "PASS", "2026-11-02T00:00:00+00:00"), ev("c3", "verdict", "PASS", "2026-11-03T00:00:00+00:00"),
            ev("c3", "verdict", "FAIL", "2026-11-03T01:00:00+00:00")])
        r = cardstore.compute(self.dir / "ev", "2026-11-11")
        self.assertEqual((r["verdict_links"], r["verdict_non_pass"]), (4, 1))
        self.assertEqual((r["review_round_links"], r["cards_with_rounds"], r["first_pass_cards"]), (4, 3, 2))
        self.assertEqual(r["first_pass_pct"], 66.7)
        assert_clean(self, r)


class Itil(Fixture):
    def test_incident_links_and_kedb(self):
        def put(rel, doc):
            p = self.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(doc))
        put("incidents/inc-1/core.json", {"id": "inc-1", "detected_at": "2026-11-01T00:00:00+00:00", "title": PLANTED})
        put("incidents/inc-2/core.json", {"id": "inc-2", "detected_at": "2026-11-05T00:00:00+00:00", "related_problem_id": "prb-1"})
        put("incidents/inc-3/core.json", {"id": "inc-3", "detected_at": "2026-11-12T01:00:00+00:00"})
        put("incidents/inc-4/core.json", {"id": "inc-4", "detected_at": "2026-11-02T00:00:00+00:00"})
        put("incidents/inc-9/redirect.json", {"to": "inc-1"})
        put("problems/prb-1/core.json", {"id": "prb-1", "related_incident_ids": ["inc-2"]})
        put("problems/prb-2/core.json", {"id": "prb-2", "related_incident_ids": ["inc-4"]})
        for n in "123":
            put(f"kedb/ke-{n}.json", {"id": n})
        r = itil.compute(self.dir, "2026-11-11")
        self.assertEqual((r["incidents"], r["incidents_linked"], r["problems"], r["kedb_entries"]), (3, 2, 2, 3))
        assert_clean(self, r)


def epoch(day, hour=12):
    return dt.datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=dt.timezone.utc).timestamp()


class Skmemory(Fixture):
    def test_latency_and_arm_timeouts(self):
        def row(ts, ms, status="ok"):
            return {"ts": ts, "q": PLANTED, "elapsed_ms": ms, "arms": {"semantic": {"status": status}, "lexical": {"status": "ok"}}}
        rows = [row(epoch("2026-11-10"), 100 * i, "timeout" if i in (3, 9) else "ok") for i in range(1, 11)]
        rows += [row(epoch("2026-11-04"), 99999, "timeout"), row(epoch("2026-11-12", 0), 99999)]
        path = write_lines(self.dir / "retrieval.jsonl", rows + ["{not json"])
        r = skmemory.compute(path, "2026-11-11", 7)
        self.assertEqual((r["searches"], r["median_ms"], r["p95_ms"], r["arm_timeout_pct"]), (10, 550.0, 1000, 20.0))
        assert_clean(self, r)


class Skwhisper(Fixture):
    def test_writes_and_digests_per_day(self):
        line = "2026-11-{d} 00:0{n}:00 [skwhisper.{m}] INFO: {t}"
        rows = [line.format(d=11, n=i, m="curator", t="Wrote whisper context: W (10 chars)") for i in range(4)]
        rows += [line.format(d=11, n=i, m="daemon", t="✓ Digested session S1: 'Digest' ") for i in range(2)]
        rows += [line.format(d=10, n=i, m="curator", t="Wrote whisper context: W (10 chars)") for i in range(2)]
        rows += ["Nov 11 prefix: " + line.format(d=11, n=9, m="curator", t="Wrote whisper context: W (9 chars)")]
        path = write_lines(self.dir / "daemon.log", rows)
        one = skwhisper.compute(path, "2026-11-11", 1)
        self.assertEqual((one["whisper_writes"], one["digests"], one["writes_per_day"]), (5, 2, 5.0))
        two = skwhisper.compute(path, "2026-11-11", 2)
        self.assertEqual((two["whisper_writes"], two["writes_per_day"]), (7, 3.5))


if __name__ == "__main__":
    unittest.main()
