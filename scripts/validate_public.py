#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache"}
FORBIDDEN = (
    re.compile("/" + "home/", re.IGNORECASE),
    re.compile("/" + "mnt/", re.IGNORECASE),
    re.compile(r"chi" + r"ap[0-9]+", re.IGNORECASE),
    re.compile(r"[a-z]:\\" + r"users\\", re.IGNORECASE),
    re.compile("GH" + "_TOKEN", re.IGNORECASE),
    re.compile("api" + "-keys", re.IGNORECASE),
)


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in FORBIDDEN:
            if pattern.search(text):
                findings.append(f"{path.relative_to(root)}: {pattern.pattern}")
    return findings


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    findings = scan(root)
    if findings:
        print("public artifact sanitization failed", file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
        return 1
    print("public artifact sanitization passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
