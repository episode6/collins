#!/usr/bin/env bash
# New in the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0).
# Build the COPR source RPM from the committed git HEAD.
#
# The build-ppa-source.sh analog: exports tracked files only into the
# %{name}-%{version} tarball the spec's Source0 names, then runs rpmbuild -bs
# against packaging/fedora/collins.spec. COPR's builders turn the SRPM into
# a binary RPM per chroot; nothing is signed here (COPR signs the repository
# itself). %{?dist} is left empty in the SRPM's own name -- COPR re-expands
# it per chroot, and a fixed name is what CI's checks glob for.
#
# Refuses when the spec's Version: disagrees with pyproject.toml: the two are
# bumped together (RELEASE_CHECKLIST.md) and scripts/verify_versions.py
# enforces it in CI, but the SRPM's version is what COPR publishes, so it is
# checked again right here. rpmlint runs on the spec and the SRPM; its
# warnings are the reason to run this on a PR rather than on a tag.
#
# Needs rpmbuild and rpmlint (Fedora: dnf install rpm-build rpmlint), so on
# another distro run it in the CI image's Fedora stage:
#   docker run --rm -v "$PWD:/src" -w /src \
#     ghcr.io/episode6/collins-ci:<tag>-fedora-pkg packaging/fedora/build-copr-srpm.sh
#
# Usage:  packaging/fedora/build-copr-srpm.sh [--rebuild]
# Then:   copr-cli build episode6/stable /tmp/collins-copr/collins-<VER>-<REL>.src.rpm
#
# --rebuild also builds the binary RPM from the SRPM, the way a COPR chroot
# would (rpmbuild --rebuild, with the BuildRequires installed locally) --
# what CI's rpm job does to prove the package installs and %check passes.
set -euo pipefail

rebuild=false
while [ $# -gt 0 ]; do
    case "$1" in
        --rebuild) rebuild=true; shift ;;
        -h|--help)
            sed -n '/^# Usage:/,/^set -/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

for tool in rpmbuild rpmlint; do
    if ! command -v "$tool" > /dev/null; then
        echo "error: $tool is not installed. On Fedora: dnf install rpm-build rpmlint;" >&2
        echo "       elsewhere, run this script inside the CI image's Fedora stage" >&2
        echo "       (see the comment at the top of the script)." >&2
        exit 2
    fi
done

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
spec="$ROOT/packaging/fedora/collins.spec"
out="/tmp/collins-copr"

name="$(rpmspec -q --srpm --qf '%{name}' "$spec")"
ver="$(rpmspec -q --srpm --qf '%{version}' "$spec")"
pyver="$(grep -m1 '^version' "$ROOT/pyproject.toml" | cut -d'"' -f2)"
if [ "$ver" != "$pyver" ]; then
    echo "error: packaging/fedora/collins.spec is at $ver but pyproject.toml says $pyver." >&2
    echo "       The spec's Version: is bumped with the rest (RELEASE_CHECKLIST.md);" >&2
    echo "       this is the version COPR would publish, so fix the spec first." >&2
    exit 1
fi

rm -rf "$out"
mkdir -p "$out/SOURCES"
git -C "$ROOT" archive --format=tar.gz --prefix="$name-$ver/" \
    -o "$out/SOURCES/$name-$ver.tar.gz" HEAD

# _topdir keeps every rpmbuild side effect (BUILD, SRPMS, ...) under $out;
# the SRPM itself lands directly in $out.
rpmbuild -bs \
    --define "_topdir $out" \
    --define "_sourcedir $out/SOURCES" \
    --define "_srcrpmdir $out" \
    --define 'dist %{nil}' \
    "$spec"

rpmlint "$spec" "$out"/*.src.rpm

if $rebuild; then
    rpmbuild --rebuild \
        --define "_topdir $out" \
        --define "_rpmdir $out" \
        --define '_build_name_fmt %%{NAME}-%%{VERSION}-%%{RELEASE}.%%{ARCH}.rpm' \
        "$out"/*.src.rpm
    rpmlint "$out"/*.noarch.rpm
fi

echo
echo "Built in $out:"
ls -1 "$out"/*.rpm
echo
echo "Upload with:"
echo "  copr-cli build episode6/stable $out/$name-$ver-*.src.rpm"
