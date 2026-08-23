# New in the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0).
#
# The CI image: every build/test dependency of every CI job, prebuilt, so a
# run pulls one image from GHCR and fetches nothing else. This file is the
# single canonical list of build dependencies; ci.yml, e2e.yml and (once it
# moves over) release.yml all run inside it.
#
# Tagged by content: .github/workflows/ci-image.yml names the image
# ghcr.io/episode6/collins-ci:<first 12 hex of hashFiles(this file)> and only
# builds when that tag is missing. Edit this file and the next run rebuilds;
# revert it and the old tag is still there. Nothing rebuilds on its own, so
# bump the date below to pick up noble's package updates (or ruff).
#
# refreshed: 2026-08-23
#
# ubuntu:24.04 (noble) rather than ubuntu:latest: noble is the older of the
# two PPA series and the floor debian/control declares (gir1.2-gtk-4.0 >= 4.10,
# gir1.2-adw-1 >= 1.5), so it is the useful release to test on. Package names
# that differ from newer releases: twine (not python3-twine), gir1.2-vte-3.91
# (not -gtk4), libspelling-1-1.
FROM ubuntu:24.04
# GTK_A11Y: no a11y bus exists under Xvfb; stop GTK warning about it.
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 GTK_A11Y=none
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git python3 \
    # test: system python + apt PyGObject, matching how the app runs
    python3-gi python3-pytest \
    # e2e: the full gir stack, a display (Xvfb) and a session bus (dbus)
    python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtksource-5 \
    gir1.2-spelling-1 libspelling-1-1 gir1.2-gdkpixbuf-2.0 gir1.2-vte-3.91 \
    xvfb xauth dbus fonts-dejavu-core \
    # packaging: wheel + sdist (python3 -m build --no-isolation) and the .deb
    python3-build python3-setuptools python3-wheel twine python3-pip \
    # ppa-source, and release.yml's ppa job (gnupg: key import, curl: the
    # Launchpad duplicate check). build-essential is not optional:
    # dpkg-checkbuilddeps aborts without it, which is how v0.1.1's upload died.
    build-essential devscripts dput debhelper dh-python \
    pybuild-plugin-pyproject python3-all gnupg curl \
  && rm -rf /var/lib/apt/lists/*
# Pinned: an unpinned `pip install ruff` changes the rule set under the lint
# job on any given day. Bumping it is a deliberate change to this file.
RUN pip install --quiet --break-system-packages ruff==0.16.4
# Container jobs run as root unless the image says otherwise, and root ignores
# file modes — one unit test chmods a directory 0o500 and expects the copy into
# it to fail. uid 1001 is the hosted runner's own uid, so the bind-mounted
# workspace is writable without a chown.
RUN useradd --uid 1001 --create-home runner
USER runner
WORKDIR /home/runner
