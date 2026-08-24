# New in the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0).
#
# The CI image: every build/test dependency of the containered CI jobs,
# prebuilt, so a run pulls one image from GHCR and fetches nothing else. This
# file is the single canonical list of build dependencies; ci.yml and
# release.yml run their containered jobs inside it.
#
# Two stages, two tags, one content-addressed hash:
# .github/workflows/ci-image.yml names both images by the first 12 hex of
# hashFiles(this file) and only builds what GHCR does not already have —
#   ghcr.io/episode6/collins-ci:<hash>            --target full (resolute)
#   ghcr.io/episode6/collins-ci:<hash>-noble-pkg  --build-arg SERIES=24.04,
#                                                 --target packaging
# Edit this file and the next run rebuilds both; revert it and the old tags
# are still there. Nothing rebuilds on its own, so bump the date below to pick
# up package updates.
#
# refreshed: 2026-08-23
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
