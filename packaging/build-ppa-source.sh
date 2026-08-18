#!/usr/bin/env bash
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-17. Full change history: git log for this file.
# Build a signed PPA source package from the committed git HEAD.
# Exports tracked files only (no node_modules / build artifacts / local config)
# into a correctly-named dir, stamps the changelog for one Ubuntu series, then
# runs debuild -S.
#
# debian/changelog is kept at a plain version targeted at UNRELEASED, so an
# accidental `debuild -S` in a checkout produces something Launchpad rejects
# rather than something that silently burns a version number. The upload
# version is built here instead, in the temp tree only:
#
#     <base>~<series><revision>        e.g. 0.1.0~noble1
#
# "~" sorts below a plain <base>, so the unsuffixed version stays free. One
# upload per series, because Launchpad never permits reusing a version string
# for a source package -- across all series, forever. A rejected upload or a
# failed build burns that version, so bump --revision rather than retrying.
#
# Usage:  packaging/build-ppa-source.sh --series noble [--revision 1] [-k KEYID]
# Then:   dput ppa:episode6/stable \
#           /tmp/collins-ppa/<series>/collins_<VER>_source.changes
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: packaging/build-ppa-source.sh --series <name> [--revision <n>] [-k <keyid>]

  --series    Ubuntu series to target: noble or resolute. Required; it names
              both the changelog distribution and the version suffix, and
              scopes the output directory.
  --revision  Per-series upload revision, default 1. Bump it when a version
              has been burned by a rejection or a failed build.
  -k, --key   GPG key to sign with, passed through to debuild. Defaults to
              whatever debuild picks from the keyring.
  --          Everything after this is passed to debuild verbatim, ahead of
              its own options. CI uses it for non-interactive signing:
              --prepend-path=<dir> -p<wrapper>, because debuild normalizes
              PATH and debsign takes the gpg program by name.
EOF
}

series=""
revision=1
keyid=""
extra=()

while [ $# -gt 0 ]; do
    case "$1" in
        --series)   series="${2-}"; shift 2 ;;
        --revision) revision="${2-}"; shift 2 ;;
        -k|--key)   keyid="${2-}"; shift 2 ;;
        -h|--help)  usage; exit 0 ;;
        --)         shift; extra=("$@"); break ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$series" ]; then
    echo "error: --series is required (e.g. --series noble)" >&2
    usage >&2
    exit 2
fi

# Only series whose libadwaita/GTK versions have actually been checked against
# Collins' API use. This is deliberately a hardcoded policy list rather than
# "any real Ubuntu series": it catches a typo (--series noeble) locally and
# instantly, and it catches a real-but-unsupported series (jammy) too. Adding
# one belongs here and in packaging/README.md together, after checking the
# stack -- not on a command line.
case "$series" in
    noble|resolute) ;;
    *)  echo "error: unsupported series '$series'; expected noble or resolute." >&2
        echo "       Adding a series means checking its libadwaita and GTK" >&2
        echo "       versions against Collins' API use first -- see the" >&2
        echo "       'Supported series' section of packaging/README.md." >&2
        exit 2 ;;
esac

case "$revision" in
    [1-9]|[1-9][0-9]*) ;;
    *)  echo "error: --revision must be a positive integer (got '$revision')" >&2
        exit 2 ;;
esac

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
# Per-series, so that building the second series does not wipe the first one's
# artifacts out from under an upload that has not happened yet.
out="/tmp/collins-ppa/$series"
stage="$out/.stage"

rm -rf "$out"
mkdir -p "$stage"
git -C "$ROOT" archive HEAD | tar -x -C "$stage"

changelog="$stage/debian/changelog"
base="$(dpkg-parsechangelog --file "$changelog" --show-field Version)"
dist="$(dpkg-parsechangelog --file "$changelog" --show-field Distribution)"

# Guard the two invariants the version scheme rests on. Both mean the tracked
# changelog has drifted, and stamping on top of that drift is how a version
# number gets burned by accident.
case "$base" in
    *'~'*)
        echo "error: debian/changelog version '$base' already carries a ~series" >&2
        echo "       suffix. Only this script may add one; git must hold the base." >&2
        exit 1 ;;
esac
if [ "$dist" != "UNRELEASED" ]; then
    echo "error: debian/changelog targets '$dist'; expected UNRELEASED." >&2
    echo "       The tracked changelog must not name a series -- this script" >&2
    echo "       stamps one per upload." >&2
    exit 1
fi

ver="${base}~${series}${revision}"

# Stamp the top entry, preserving whatever urgency it declares.
first="$(head -n 1 "$changelog")"
urgency="$(printf '%s' "$first" | sed -n 's/.*;[[:space:]]*\(urgency=[^[:space:]]*\).*/\1/p')"
[ -n "$urgency" ] || urgency="urgency=medium"
pkg="$(dpkg-parsechangelog --file "$changelog" --show-field Source)"
printf '%s (%s) %s; %s\n' "$pkg" "$ver" "$series" "$urgency" > "$changelog.new"
tail -n +2 "$changelog" >> "$changelog.new"
mv "$changelog.new" "$changelog"

# debuild takes its version from debian/changelog, so the source directory has
# to be named from the stamped changelog -- not from pyproject.toml, which
# never carries the ~series suffix.
stamped="$(dpkg-parsechangelog --file "$changelog" --show-field Version)"
[ "$stamped" = "$ver" ] || { echo "error: changelog stamp failed ($stamped != $ver)" >&2; exit 1; }
src="$out/$pkg-$stamped"
mv "$stage" "$src"

cd "$src"
# debuild's own options go ahead of the dpkg-buildpackage ones, per its
# synopsis; -k is a debsign option and works at the end.
debuild_args=(${extra[@]+"${extra[@]}"} -S -sa)
[ -n "$keyid" ] && debuild_args+=(-k"$keyid")
debuild "${debuild_args[@]}"

echo
echo "Built in $out:"
ls -1 "$out"/*.changes
echo
echo "Upload with:"
echo "  dput ppa:episode6/stable $out/${pkg}_${stamped}_source.changes"
