# New in the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0).
#
# The CI image: every build/test dependency of the containered CI jobs,
# prebuilt, so a run pulls one image from GHCR and fetches nothing else. This
# file is the single canonical list of build dependencies; ci.yml and
# release.yml run their containered jobs inside it.
#
# Three stages, three tags, one content-addressed hash:
# .github/workflows/ci-image.yml names every image by the first 12 hex of
# hashFiles(this file) and only builds what GHCR does not already have —
#   ghcr.io/episode6/collins-ci:<hash>             --target full (resolute)
#   ghcr.io/episode6/collins-ci:<hash>-noble-pkg   --build-arg SERIES=24.04,
#                                                  --target packaging
#   ghcr.io/episode6/collins-ci:<hash>-fedora-pkg  --target fedora-pkg
# Edit this file and the next run rebuilds all three; revert it and the old
# tags are still there. Nothing rebuilds on its own, so bump the date below to
# pick up package updates.
#
# refreshed: 2026-08-28
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
    # build_deb.sh and debian/rules validate the desktop entry and the
    # AppStream metainfo (packaging job, and dpkg-checkbuilddeps in the
    # source builds once debian/control names them)
    desktop-file-utils appstream \
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

# The Fedora stage, for the RPM: rpm-build + rpmlint assemble and lint the
# SRPM (ci.yml's rpm job, release.yml's copr job, which also needs copr-cli);
# the spec's BuildRequires let the rpm job rebuild the SRPM into a binary
# package the way a COPR chroot would; and the package's runtime dependencies
# are preinstalled so `dnf install ./collins-*.rpm` proves the Requires
# resolve without downloading the GTK stack per run. fedora:latest is
# resolved when the image is built -- a `refreshed:` bump above moves it on.
# Its own FROM rather than a stage of the ubuntu chain, and root throughout:
# nothing here runs the unit suite, and installing the built RPM needs root.
FROM fedora:latest AS fedora-pkg
ENV LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1
# glibc-langpack-en: rpmlint runs the spec's scriptlets under en_US.UTF-8 and
# warns fourteen times when the locale is missing.
RUN dnf install -y \
    git rpm-build rpmlint copr-cli glibc-langpack-en \
    python3-devel pyproject-rpm-macros python3-setuptools python3-wheel python3-pip \
    desktop-file-utils appstream \
    python3-gobject gtk4 libadwaita vte291-gtk4 gtksourceview5 libspelling \
  && dnf clean all
# Root here, but the runner checks the workspace out as uid 1001, and git
# refuses to touch a repository someone else owns ("dubious ownership") --
# actions/checkout's own exception lives in a temporary HOME the later steps
# never see. The image exists only to run CI on that checkout.
RUN git config --system --add safe.directory '*'
