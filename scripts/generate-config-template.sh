#!/usr/bin/env bash
#
# generate-config-template.sh: write the contents of the public
# skrsi-config-template repo from templates/instance-repo/, the single source
# of truth. Mirrors the skstacks and skgateway generators.
#
# Usage:
#   scripts/generate-config-template.sh <output-dir>
#
# Copies templates/instance-repo/ verbatim into <output-dir>, after clearing it,
# so a file removed from the source does not survive. No substitution happens:
# the fixture holds only synthetic names. Used to publish the template repo by
# hand and by the drift check in .github/workflows/instance-template.yml, which
# compares the output with the in-repo fixture (and with the published template
# repo once it exists). It never pushes anything.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/../templates/instance-repo"
OUT="${1:?usage: generate-config-template.sh <output-dir>}"

[ -d "$SRC" ] || { echo "generate-config-template.sh: error: missing source: $SRC" >&2; exit 2; }

mkdir -p "$OUT"
find "$OUT" -mindepth 1 -delete
cp -a "$SRC"/. "$OUT"/
