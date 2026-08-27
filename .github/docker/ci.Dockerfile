# New in the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0).
#
# The CI image: every build/test dependency of the containered CI jobs,
# prebuilt, so a run pulls one image from GHCR and fetches nothing else. This
# file is the single canonical list of build dependencies (the aur stage
# excepted: its list is the PKGBUILD's own); ci.yml and release.yml run their
# containered jobs inside it.
#
# Three stages, three tags, content-addressed:
# .github/workflows/ci-image.yml names each image by the first 12 hex of a
# hashFiles() over its inputs and only builds what GHCR does not already have —
#   ghcr.io/episode6/collins-ci:<hash>            --target full (resolute)
#   ghcr.io/episode6/collins-ci:<hash>-noble-pkg  --build-arg SERIES=24.04,
#                                                 --target packaging
#   ghcr.io/episode6/collins-ci:<hash2>-aur       --target aur (Arch)
# <hash> is over this file; <hash2> over this file plus packaging/aur/PKGBUILD,
# whose dependency arrays the aur stage installs. Edit this file and the next
# run rebuilds all three; revert it and the old tags are still there. Nothing
# rebuilds on its own, so bump the date below to pick up package updates.
#
# refreshed: 2026-08-27
#
# ubuntu:26.04 (resolute) is the default base: the containered jobs run on the
# newest supported stack (GTK 4.22, adw 1.9, Python 3.14) — the one
# development machines run — while Launchpad still builds and installs the
# noble binary against noble's real dependencies at release time. The
# `packaging` stage's package list is identical on both series, which is what
# lets this one Dockerfile also produce the noble packaging image (for the
# noble PPA source build) with no conditionals: the only cross-series rename
# in the whole set is libspelling's soname package (libspelling-1-2 on
# resolute, -1-1 on noble), and it lives in the resolute-only `full` stage.
ARG SERIES=26.04
FROM ubuntu:${SERIES} AS packaging
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git python3 \
    # packaging: wheel + sdist (python3 -m build --no-isolation) and the .deb
    python3-build python3-setuptools python3-wheel twine python3-pip \
    # ppa-source, and release.yml's ppa job (gnupg: key import, curl: the
    # Launchpad duplicate check). Names identical on both series.
    # build-essential is not optional: dpkg-checkbuilddeps aborts without it,
    # which is how v0.1.1's upload died.
    build-essential devscripts dput debhelper dh-python \
    pybuild-plugin-pyproject python3-all gnupg curl \
  && rm -rf /var/lib/apt/lists/*
# Container jobs run as root unless the image says otherwise, and root ignores
# file modes — one unit test chmods a directory 0o500 and expects the copy into
# it to fail. uid 1001 is the hosted runner's own uid, so the bind-mounted
# workspace is writable without a chown.
RUN useradd --uid 1001 --create-home runner
USER runner
WORKDIR /home/runner

# Everything else, resolute-only (this stage is never built for noble, so the
# resolute package names are hardcoded).
FROM packaging AS full
USER root
# GTK_A11Y: no a11y bus exists under Xvfb; stop GTK warning about it.
ENV GTK_A11Y=none
RUN apt-get update && apt-get install -y --no-install-recommends \
    # test: system python + apt PyGObject, matching how the app runs
    python3-gi python3-pytest \
    # e2e: the full gir stack, a display (Xvfb) and a session bus (dbus)
    python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtksource-5 \
    gir1.2-spelling-1 libspelling-1-2 gir1.2-gdkpixbuf-2.0 gir1.2-vte-3.91 \
    xvfb xauth dbus fonts-dejavu-core \
  && rm -rf /var/lib/apt/lists/*
USER runner

# The Arch image: what release.yml's aur job publishes the PKGBUILD from and
# ci.yml's aur-build job test-builds it in. Unrelated to the Ubuntu stages
# above (a separate FROM), and the one stage whose dependency list is not
# written here: Arch's already lives in the PKGBUILD's depends/makedepends
# arrays, which every AUR user's makepkg installs from, so this stage sources
# the PKGBUILD and installs the same. That is why this tag's hash covers the
# PKGBUILD too -- a PR that edits the recipe rebuilds this image, and a
# typo'd package name fails that build ("target not found") on the PR, long
# before a release. The packages age between rebuilds like everything else
# here; the test build is a recipe check, not a build against today's Arch.
FROM archlinux:base-devel AS aur
COPY packaging/aur/PKGBUILD /tmp/PKGBUILD
# archlinux-keyring first, on its own: the base image can predate the keys
# that signed today's packages. git: the checkouts and aur-build's archive of
# HEAD; openssh: the push to the AUR; curl: the tag tarball. Version
# constraints (foo>=1.2) are stripped for pacman -S, which takes bare names;
# makepkg enforces them itself at build time.
RUN pacman -Sy --noconfirm archlinux-keyring \
  && pacman -Su --noconfirm --needed git openssh curl \
  && bash -c 'source /tmp/PKGBUILD && pacman -S --noconfirm --needed \
       "${depends[@]%%[<>=]*}" "${makedepends[@]%%[<>=]*}"' \
  && rm -rf /var/cache/pacman/pkg/* /tmp/PKGBUILD
# makepkg refuses to run as root; same uid as the Ubuntu stages, same reason.
RUN useradd --uid 1001 --create-home runner
USER runner
WORKDIR /home/runner
