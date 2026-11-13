#!/usr/bin/env python3
"""Review rounds and verdict links from the coord card event log.

    baseline_cardstore.py --events DIR --end YYYY-MM-DD

Reads DIR/*.jsonl (the card_events directory), all history up to the end of the end day. Counts
`verdict` links (PASS and everything else) and `review_round` links ("<pr> <head> <VERDICT> ...").
A card passed first time when its earliest recorded round, by timestamp, is PASS. Link text is
parsed for its verdict word only and never copied out.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_common as bc  # noqa: E402


def compute(events, end: str) -> dict:
    _, stop = bc.window(end, None)
    verdicts = non_pass = 0
    rounds: dict[str, list[tuple]] = {}
    for path in sorted(Path(events).glob("*.jsonl")):
        for ev in bc.json_lines(path):
            when = bc.parse_time(ev.get("ts"))
            if ev.get("action") != "link" or when is None or when >= stop:
                continue
            value = str(ev.get("link_value") or "").split()
            if ev.get("link_key") == "verdict":
                verdicts += 1
                non_pass += 0 if value[:1] == ["PASS"] else 1
            elif ev.get("link_key") == "review_round":
                rounds.setdefault(str(ev.get("card_id")), []).append((when, value[2] if len(value) > 2 else ""))
    first = sum(1 for r in rounds.values() if min(r)[1] == "PASS")
    return bc.emit("cardstore", end, None, {
        "verdict_links": verdicts, "verdict_non_pass": non_pass,
        "review_round_links": sum(len(r) for r in rounds.values()), "cards_with_rounds": len(rounds),
        "first_pass_cards": first, "first_pass_pct": bc.pct(first, len(rounds))})


def main() -> int:
    a = bc.cli(__doc__.splitlines()[0], lambda p: p.add_argument("--events", required=True))
    return bc.run(compute(a.events, a.end))


if __name__ == "__main__":
    sys.exit(main())
