#!/usr/bin/env bash
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-10-30. Full change history: git log for this file.
# Build the two Debian packages (split-service spec §3.24, PR-3.7), the same
# split as debian/control:
#   dist/collins-service_<version>_all.deb  the headless half: the Python
#       package, the collins-service and collins-keeper commands, both
#       systemd user units (never enabled); installs on a box with no display
#   dist/collins_<version>_all.deb          the window: the collins launcher,
#       desktop entry, icons and metainfo; depends on the collins-service of
#       the same version, since the window's modules are in it
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="collins"
SVC="collins-service"
APP_ID="com.episode6.Collins"
VERSION="$(grep -m1 '^version' "$ROOT/pyproject.toml" | cut -d'"' -f2)"
BUILD="$ROOT/dist/deb-build"
APP="$BUILD/$PKG"
SERVICE="$BUILD/$SVC"

rm -rf "$BUILD"
mkdir -p "$ROOT/dist"

# == collins-service ===========================================================

# -- python package -----------------------------------------------------------
SITE="$SERVICE/usr/lib/python3/dist-packages/collins"
mkdir -p "$SITE"
# The whole package, every subpackage (service/, api/, and any later one):
# a flat collins/*.py copy once shipped a .deb that died at app.py's first
# import. It is one tree, so the window's modules ride along in this package
# as files the service never imports. locale/ rides along when present.
# ci.yml's packaging job extracts the built .debs and imports collins.app
# and collins.service.main from them.
tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' -cf - collins \
  | tar -C "$SERVICE/usr/lib/python3/dist-packages" -xf -
# collins/icons, sounds, the desktop/metainfo names and the units are
# symlinks into data/ for the wheel; here they would dangle, and the icons
# and units ship below.
find "$SITE" -type l -delete

# -- commands -----------------------------------------------------------------
# The session service and the pty keeper (the wheel's console scripts,
# written by hand here like the launcher).
mkdir -p "$SERVICE/usr/bin"
cat > "$SERVICE/usr/bin/$SVC" <<'EOF'
#!/usr/bin/python3
import sys

from collins.service.main import main

sys.exit(main())
EOF
cat > "$SERVICE/usr/bin/$PKG-keeper" <<'EOF'
#!/usr/bin/python3
import sys

from collins.service.keeper import main

sys.exit(main())
EOF
chmod 755 "$SERVICE/usr/bin/$SVC" "$SERVICE/usr/bin/$PKG-keeper"

# -- systemd user units, not enabled -------------------------------------------
mkdir -p "$SERVICE/usr/lib/systemd/user" "$SERVICE/usr/share/doc/$SVC"
cp "$ROOT/data/collins-service.service" "$ROOT/data/collins-keeper.service" \
   "$SERVICE/usr/lib/systemd/user/"
cp "$ROOT/LICENSE" "$SERVICE/usr/share/doc/$SVC/copyright"

# == collins ===================================================================

# -- executable ---------------------------------------------------------------
mkdir -p "$APP/usr/bin"
cat > "$APP/usr/bin/$PKG" <<'EOF'
#!/usr/bin/python3
import sys

from collins.app import main

sys.exit(main())
EOF
chmod 755 "$APP/usr/bin/$PKG"

# -- desktop file / icon / metainfo --------------------------------------------
mkdir -p "$APP/usr/share/applications" \
         "$APP/usr/share/icons/hicolor/scalable/apps" \
         "$APP/usr/share/$PKG/icons/hicolor/scalable/actions" \
         "$APP/usr/share/metainfo" \
         "$APP/usr/share/doc/$PKG"
# system-wide desktop entry: binary on PATH, no hardcoded working directory
sed -e "s|^Exec=.*|Exec=$PKG|" -e "/^Path=/d" \
    "$ROOT/data/$APP_ID.desktop" > "$APP/usr/share/applications/$APP_ID.desktop"
# ...-panel.svg is the status icon's artwork, drawn for 22px, and
# ...-panel-working.svg the same glass while a session is busy (see
# statusicon.py); the Debug variants are a source-checkout thing and stay out
# of the package.
cp "$ROOT/data/icons/$APP_ID.svg" \
   "$APP/usr/share/icons/hicolor/scalable/apps/"
# The action icons are app-private artwork on generic names, so they go in a
# Collins-owned search-path root rather than the shared hicolor theme: two
# packages cannot both own hicolor/.../tab-close-symbolic.svg, and
# agent-session-manager does. app.py finds this root; the tray gets it as
# IconThemePath, which is why -panel.svg belongs here rather than in apps/.
cp "$ROOT/data/icons/$APP_ID.svg" "$ROOT/data/icons/$APP_ID-panel.svg" \
   "$ROOT/data/icons/$APP_ID-panel-working.svg" \
   "$ROOT/data/icons/$APP_ID-panel-empty.svg" \
   "$APP/usr/share/$PKG/icons/"
cp "$ROOT/data/icons/hicolor/scalable/actions/"*.svg \
    "$APP/usr/share/$PKG/icons/hicolor/scalable/actions/"
cp "$ROOT/data/$APP_ID.metainfo.xml" "$APP/usr/share/metainfo/"
cp "$ROOT/LICENSE" "$APP/usr/share/doc/$PKG/copyright"

# Validate the staged desktop entry and metainfo, mirroring the RPM spec's
# %check (same releases-not-in-order override: the metainfo lists the
# upstream agent-session-manager releases below the fork's own, so its
# versions don't descend end to end -- history, not a mistake). This runs on
# every PR via ci.yml's packaging job, so a broken metainfo fails there, not
# in a software center after a release.
desktop-file-validate "$APP/usr/share/applications/$APP_ID.desktop"
appstreamcli validate --no-net --override releases-not-in-order=info \
  "$APP/usr/share/metainfo/$APP_ID.metainfo.xml"

# -- control ------------------------------------------------------------------
mkdir -p "$SERVICE/DEBIAN" "$APP/DEBIAN"
# Keep both Depends lists in step with debian/control -- these packages are
# the only channel Debian has. collins-service needs what the service and the
# keeper import (GLib, GObject, Gio and libsoup 3: gir1.2-soup-3.0 is the
# socket between the window and collins-service) and recommends what a
# sandboxed session's box is built and grown with. collins needs the GTK
# stack: gtksourceview5 is a hard requirement (editor.py exits with an
# install hint when it is missing, which apt should have made unnecessary);
# libspelling is only recommended (composer.py degrades to an unchecked text
# box without it). The two version floors are the measured ones: GTK 4.10
# for Gtk.FileDialog/FontDialog, libadwaita 1.5 for Adw.AlertDialog and
# friends. Source: names the source package where it differs from the binary
# package's name, so only collins-service carries it. Breaks/Replaces is the
# transition debian/control explains: collins-service takes the Python tree
# over from a pre-split collins (<< 0.1.5~, a marker that never moves), or
# installing this pair over collins 0.1.4 fails on the first shared file.
cat > "$SERVICE/DEBIAN/control" <<EOF
Package: $SVC
Source: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-glib-2.0, gir1.2-soup-3.0
Breaks: $PKG (<< 0.1.5~)
Replaces: $PKG (<< 0.1.5~)
Recommends: bubblewrap, bindfs, fuse3
Installed-Size: $(du -sk "$SERVICE" --exclude=DEBIAN | cut -f1)
Maintainer: episode6 <support@episode6.com>
Homepage: https://github.com/episode6/collins
Description: headless session service for Collins
 The half of Collins that runs Claude Code sessions: collins-service holds
 every session's terminal, its sandbox and the repository and pull request
 calls, and collins-keeper keeps the terminals alive across a service
 restart. Both come with systemd user units the package never enables.
 Installed alone, it is the service on a headless box that a Collins
 window on another machine reaches over ssh.
EOF
cat > "$APP/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), $SVC (= $VERSION), python3-gi, gir1.2-gtk-4.0 (>= 4.10), gir1.2-adw-1 (>= 1.5), gir1.2-vte-3.91, gir1.2-gtksource-5, gir1.2-glib-2.0, gir1.2-soup-3.0, python3-markdown-it, python3-linkify-it
Recommends: gir1.2-spelling-1, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0
Installed-Size: $(du -sk "$APP" --exclude=DEBIAN | cut -f1)
Maintainer: episode6 <support@episode6.com>
Homepage: https://github.com/episode6/collins
Description: Manage and resume AI coding agent sessions (GTK4 GUI)
 Native GTK4/libadwaita desktop app for the Claude Code CLI: browse all
 sessions grouped by project, name and star them, and resume any session in
 embedded terminal tabs. Unofficial community tool; the agent's own session
 data is never modified.
EOF

dpkg-deb --build --root-owner-group "$SERVICE" "$ROOT/dist/${SVC}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$APP" "$ROOT/dist/${PKG}_${VERSION}_all.deb"
rm -rf "$BUILD"
echo "Built: dist/${SVC}_${VERSION}_all.deb dist/${PKG}_${VERSION}_all.deb"
