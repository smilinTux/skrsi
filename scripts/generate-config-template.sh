#!/usr/bin/env bash
#
# generate-config-template.sh: write the contents of the public
# skrsi-config-template repo from templates/instance-repo/, the single source
# of truth. Mirrors the skstacks and skgateway generators.
#
# Usage:
#   scripts/generate-config-template.sh <output-dir>
#
# <output-dir> must be missing, empty, or already a template or instance (it
# has a CONFIG_SCHEMA file at its root, such as a clone of the template repo).
# There everything except .git is replaced, so a file removed from the source
# does not survive. It refuses any other non-empty directory (an unrelated git
# checkout included), the filesystem root, $HOME, and any directory that is or
# contains this framework repo. No substitution happens: the fixture holds
# only synthetic names. It never commits or pushes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd -P)"
SRC="$REPO/templates/instance-repo"
OUT="${1:?usage: generate-config-template.sh <output-dir>}"

die() { echo "generate-config-template.sh: refusing: $*" >&2; exit 2; }

[ -d "$SRC" ] || die "missing source: $SRC"
mkdir -p "$OUT"
OUT_ABS="$(cd "$OUT" && pwd -P)"
HOME_ABS="$(cd "${HOME:-/}" 2>/dev/null && pwd -P || echo /)"

[ "$OUT_ABS" != "/" ] || die "output is the filesystem root"
[ "$OUT_ABS" != "$HOME_ABS" ] || die "output is \$HOME"
case "$REPO/" in "$OUT_ABS"/*) die "output is or contains the framework repo ($OUT_ABS)" ;; esac
case "$OUT_ABS/" in "$REPO"/*) die "output is inside the framework repo ($OUT_ABS)" ;; esac

if [ -n "$(ls -A "$OUT_ABS")" ]; then
  [ -f "$OUT_ABS/CONFIG_SCHEMA" ] || die "$OUT_ABS is not empty and has no CONFIG_SCHEMA (not a template or instance)"
  find "$OUT_ABS" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf -- {} +
fi
cp -a "$SRC"/. "$OUT_ABS"/
