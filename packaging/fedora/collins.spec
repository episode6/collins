# New in the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0).
#
# The Fedora / RHEL 10 package. COPR (episode6/stable) builds it in every
# enabled chroot from the SRPM that packaging/fedora/build-copr-srpm.sh
# assembles out of the committed git HEAD; %%{?dist} is what lets one spec
# cover every release. Mirrors the AUR PKGBUILD's shape (the wheel carries
# the action icons, so only the desktop entry, app icon and metainfo are
# installed on top of it) and debian/control's dependency floors.
#
# Two binary packages from the one wheel (split-service spec §3.24,
# PR-3.7), the split debian/control makes: collins-service is the headless
# half (the whole Python package -- one tree, so the window's modules ride
# along as files the service never imports -- the collins-service and
# collins-keeper commands and both systemd user units, never enabled), and
# installs on a box with no display; collins is the window (the launcher,
# desktop entry, icons and metainfo, and the GTK stack) and requires the
# collins-service of the same build, since the window's modules are in it.
#
# Version: is kept at the plain next version, like debian/changelog's top
# entry, and bumped in the same version-bump PRs (RELEASE_CHECKLIST.md);
# scripts/verify_versions.py checks it agrees with pyproject.toml and that
# %%changelog has an entry for it. Release: stays 1 -- a re-upload of a botched
# build bumps it to 2, and the next version bump resets it.

Name:           collins
Version:        0.1.5
Release:        1%{?dist}
Summary:        Native GTK4 desktop app to manage Claude Code sessions
License:        GPL-3.0-or-later AND CC0-1.0 AND MIT
URL:            https://github.com/episode6/collins
# The tag tarball's URL, for the record and for rpmlint; only the basename is
# looked up, and build-copr-srpm.sh writes that file from git HEAD -- the same
# tree the tag points at when the release workflow runs it.
Source0:        %{url}/archive/refs/tags/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
# %%check: the desktop entry and the AppStream metainfo the package installs.
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
# %%{_userunitdir}, for the systemd user units.
BuildRequires:  systemd-rpm-macros

# Mirrors debian/control. GTK 4.10 (Gtk.FileDialog / Gtk.FontDialog) and
# libadwaita 1.5 (Adw.Dialog and friends) are the measured floors; every
# maintained Fedora and RHEL 10 clear both, but a floor that is written down
# refuses a too-old stack instead of installing and dying at import.
# vte291-gtk4 and gtksourceview5 are deliberately unversioned: nothing has
# established their real minimum, and a guessed floor locks out users who
# would have been fine. The GIR typelibs ship inside these packages on Fedora
# and RHEL alike, so there is nothing else to name. libspelling is a weak
# dependency: the composer degrades to an unchecked text box without it, and
# dnf installs it by default anyway. So are gstreamer1 and its base plugins
# (the notification sound; the beep stands in without them): their GIR
# typelibs ship inside those two packages — Gst-1.0.typelib in gstreamer1,
# GstAudio/GstPbutils/GstVideo-1.0.typelib and libgstplayback.so in
# gstreamer1-plugins-base — so, again, nothing else to name. The claude CLI is a curl-installed
# binary with no package, so it cannot be a dependency at all; the app's own
# first-run check handles its absence. markdown-it-py and linkify-it-py
# (pure Python) render pull request bodies as real markdown blocks. The
# window reaches its service over libsoup 3 too (collins/api/client.py), so
# libsoup3 is named here as well as in collins-service.
Requires:       collins-service = %{version}-%{release}
Requires:       python3-gobject
Requires:       libsoup3
Requires:       gtk4 >= 4.10
Requires:       libadwaita >= 1.5
Requires:       vte291-gtk4
Requires:       gtksourceview5
Requires:       python3-markdown-it-py
Requires:       python3-linkify-it-py
Recommends:     libspelling
Recommends:     gstreamer1
Recommends:     gstreamer1-plugins-base

%description
Collins browses every Claude Code session on your machine in a sidebar
grouped by project, lets you name and star them, and resumes any of them in
embedded terminal tabs.

It is an unofficial community tool, not affiliated with Anthropic, and never
modifies the agents' own session data.

%package        service
Summary:        Headless session service for Collins
# What the service and the keeper import, and no more:
# tests/test_service_imports.py holds them to GLib, GObject and Gio, which
# python3-gobject-base brings with their typelibs (no cairo, which the
# service never draws with), and api/server.py's libsoup 3; the service
# imports no markdown library. bubblewrap builds a sandboxed session's box;
# without it Collins says so in Preferences and every session launches
# unsandboxed, as before the feature. bindfs and fuse3 (fusermount3) let a
# directory allowed to a sandboxed session reach it while it runs; without
# either the grant applies at the session's next restart. bindfs is in EPEL
# on RHEL 10, fuse3 in its BaseOS. All three run on the service's side.
Requires:       python3-gobject-base
Requires:       libsoup3
Recommends:     bubblewrap
Recommends:     bindfs
Recommends:     fuse3

%description    service
The half of Collins that runs Claude Code sessions: collins-service holds
every session's terminal, its sandbox and the repository and pull request
calls, and collins-keeper keeps the terminals alive across a service
restart. Both come with systemd user units the package never enables.
Installed alone, it is the service on a headless box that a Collins window
on another machine reaches over ssh.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l collins
# The wheel ships the package, the commands and the action icons (app.py
# puts the package's own icons directory on the icon search path, so
# nothing goes in the shared hicolor theme but the app icon, which the shell
# has to find by id). The desktop entry launches the installed command from
# no particular directory: the checkout's Exec/Path are for a source tree.
desktop-file-install \
  --dir=%{buildroot}%{_datadir}/applications \
  --set-key=Exec --set-value=collins \
  --remove-key=Path \
  data/com.episode6.Collins.desktop
install -Dm644 data/icons/com.episode6.Collins.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/com.episode6.Collins.svg
install -Dm644 data/com.episode6.Collins.metainfo.xml \
  %{buildroot}%{_metainfodir}/com.episode6.Collins.metainfo.xml
# The systemd user units: the service's and the keeper's (not enabled: the
# window starts the service, the service the keeper). No
# %%systemd_user_post / %%systemd_user_postun scriptlets either, so an
# upgrade restarts nothing.
install -Dm644 data/collins-service.service \
  %{buildroot}%{?_userunitdir}%{!?_userunitdir:/usr/lib/systemd/user}/collins-service.service
install -Dm644 data/collins-keeper.service \
  %{buildroot}%{?_userunitdir}%{!?_userunitdir:/usr/lib/systemd/user}/collins-keeper.service

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/com.episode6.Collins.desktop
# The metainfo lists the upstream agent-session-manager releases below the
# fork's own, so its versions don't descend end to end; that is history, not
# a mistake, and stays an info here rather than the warning that fails the
# check. The override's severity can go back up when the upstream entries do.
appstreamcli validate --no-net --override releases-not-in-order=info \
  %{buildroot}%{_metainfodir}/com.episode6.Collins.metainfo.xml

%files
%doc README.md
%license LICENSE
%{_bindir}/collins
%{_datadir}/applications/com.episode6.Collins.desktop
%{_datadir}/icons/hicolor/scalable/apps/com.episode6.Collins.svg
%{_metainfodir}/com.episode6.Collins.metainfo.xml

# The Python package (%%{pyproject_files}, with its license) is the
# service's: the window's own package holds its launcher and desktop files.
%files service -f %{pyproject_files}
%doc README.md
%{_bindir}/collins-service
%{_bindir}/collins-keeper
%{?_userunitdir}%{!?_userunitdir:/usr/lib/systemd/user}/collins-service.service
%{?_userunitdir}%{!?_userunitdir:/usr/lib/systemd/user}/collins-keeper.service

# One entry per release, added by the version-bump PRs; the full notes are in
# docs/releases.md. Dates are the release branch's cut date, like
# debian/changelog's.
%changelog
* Sun Sep 27 2026 Geoff Hackett <ghackett@episode6.com> - 0.1.5-1
- New requires libsoup3: the socket between the window and its session
  service. The service is its own process (collins-service, with a systemd
  user unit installed under %%{_userunitdir} and never enabled or started
  by the package): quitting or crashing the window ends no session, Detach
  leaves a session running, and Restart service runs new service code
  after an upgrade. App state is split into state.json and ui-state.json.
- The service can be on another machine: Connect to... (or collins
  --connect NAME) forwards a box's socket over ssh, with no port and no
  token; folders on the box are picked in Collins' own path picker, two
  windows on one session share the terminal's size, and a notification
  command runs on the box when no window is attached. A service restart
  keeps every session running (collins-keeper holds the terminals).
- New subpackage collins-service: the service, the pty keeper
  (collins-keeper, which keeps sessions running across a service restart)
  and both systemd user units, never enabled by the package. It installs
  on its own on a headless box; collins requires it.

* Sun Sep 27 2026 Geoff Hackett <ghackett@episode6.com> - 0.1.4-1
- Sandboxed sessions in a bubblewrap box (a home per session, settings and
  hooks read-only, a worktree session narrowed to its worktree, directories
  allowed live, six session tools by default), cloning a repository as a
  project, and git page additions (one-button gap unfolds, notes outside
  hunks, hide whitespace, resolve all conflicts, a narrow layout).
- Recommends bubblewrap: a session can be run inside a filesystem sandbox.
  Without it every session runs unsandboxed and Preferences says why.
- Recommends bindfs and fuse3: a directory allowed to a sandboxed session
  reaches it while it runs. Without them it applies at the next restart.

* Tue Sep 08 2026 Geoff Hackett <ghackett@episode6.com> - 0.1.3-1
- A native git page beside each session (diffs, staging, notes, commits and
  files panels, five agent tools), pull request bodies as real markdown
  (new python3-markdown-it-py and python3-linkify-it-py requires), worktree
  trash on archive, sound on desktop notifications, and finished runs
  counted off the transcript. A binary noarch RPM is attached to each
  GitHub release, beside the .deb.

* Sat Aug 22 2026 Geoff Hackett <ghackett@episode6.com> - 0.1.2-1
- Collins is now published to the episode6/stable COPR, for every maintained
  Fedora and for RHEL 10 and its rebuilds.
- In-app notifications (cards, a header bell, a history sheet, sounds via
  GStreamer), an effort picker beside the model picker, a new-chat screen
  with drafts, a daily update check, and a Token use preferences group.

* Tue Aug 18 2026 Geoff Hackett <ghackett@episode6.com> - 0.1.1-1
- Published to ppa:episode6/stable and PyPI; installs alongside
  agent-session-manager; libadwaita 1.5 and GTK 4.10 floors; libspelling
  optional; rebindable keyboard shortcuts; close-to-hide with a status icon.

* Sun Jul 26 2026 Geoff Hackett <ghackett@episode6.com> - 0.1.0-1
- Rebrand the app as Collins (fork of agent-session-manager): new package
  name, command, app id and config dir.
