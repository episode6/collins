#!/usr/bin/env bash
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.
# Build a signed PPA source package from the committed git HEAD.
# Exports tracked files only (no node_modules / build artifacts / local config)
# into a correctly-named dir, then runs debuild -S.
#
# Usage:  packaging/build-ppa-source.sh
# Then:   dput ppa:<you>/<ppa> /tmp/collins-ppa/collins_<ver>_source.changes
set -euo pipefail

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
ver="$(grep -m1 '^version' "$ROOT/pyproject.toml" | cut -d'"' -f2)"
out="/tmp/collins-ppa"
src="$out/collins-$ver"

rm -rf "$out"
mkdir -p "$src"
git -C "$ROOT" archive HEAD | tar -x -C "$src"

cd "$src"
debuild -S -sa

echo
echo "Built in $out:"
ls -1 "$out"/*.changes
