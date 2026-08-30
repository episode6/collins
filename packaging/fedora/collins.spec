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
# Version: is kept at the plain next version, like debian/changelog's top
# entry, and bumped in the same version-bump PRs (RELEASE_CHECKLIST.md);
# scripts/verify_versions.py checks it agrees with pyproject.toml and that
# %%changelog has an entry for it. Release: stays 1 -- a re-upload of a botched
# build bumps it to 2, and the next version bump resets it.

Name:           collins
Version:        0.1.3
Release:        1%{?dist}
Summary:        Native GTK4 desktop app to manage Claude Code sessions
License:        GPL-3.0-or-later AND CC0-1.0
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
# first-run check handles its absence.
Requires:       python3-gobject
Requires:       gtk4 >= 4.10
Requires:       libadwaita >= 1.5
Requires:       vte291-gtk4
Requires:       gtksourceview5
Recommends:     libspelling
Recommends:     gstreamer1
Recommends:     gstreamer1-plugins-base

%description
Collins browses every Claude Code session on your machine in a sidebar
grouped by project, lets you name and star them, and resumes any of them in
embedded terminal tabs.

It is an unofficial community tool, not affiliated with Anthropic, and never
modifies the agents' own session data.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l collins
# The wheel ships the package, the collins command and the action icons
# (app.py puts the package's own icons directory on the icon search path, so
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

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/com.episode6.Collins.desktop
# The metainfo lists the upstream agent-session-manager releases below the
# fork's own, so its versions don't descend end to end; that is history, not
# a mistake, and stays an info here rather than the warning that fails the
# check. The override's severity can go back up when the upstream entries do.
appstreamcli validate --no-net --override releases-not-in-order=info \
  %{buildroot}%{_metainfodir}/com.episode6.Collins.metainfo.xml

%files -f %{pyproject_files}
%doc README.md
%{_bindir}/collins
%{_datadir}/applications/com.episode6.Collins.desktop
%{_datadir}/icons/hicolor/scalable/apps/com.episode6.Collins.svg
%{_metainfodir}/com.episode6.Collins.metainfo.xml

# One entry per release, added by the version-bump PRs; the full notes are in
# docs/releases.md. Dates are the release branch's cut date, like
# debian/changelog's.
%changelog
* Sun Aug 30 2026 Geoff Hackett <ghackett@episode6.com> - 0.1.3-1
- Development series after the 0.1.2 release.

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
