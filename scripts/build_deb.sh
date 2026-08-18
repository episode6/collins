#!/usr/bin/env bash
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-18. Full change history: git log for this file.
# Build a Debian package: dist/collins_<version>_all.deb
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="collins"
APP_ID="com.episode6.Collins"
VERSION="$(grep -m1 '^version' "$ROOT/pyproject.toml" | cut -d'"' -f2)"
BUILD="$ROOT/dist/deb-build"

rm -rf "$BUILD"
mkdir -p "$ROOT/dist"

# -- python package -----------------------------------------------------------
SITE="$BUILD/usr/lib/python3/dist-packages/collins"
mkdir -p "$SITE"
cp "$ROOT"/collins/*.py "$SITE/"
if [ -d "$ROOT/collins/locale" ]; then
  cp -r "$ROOT/collins/locale" "$SITE/"
fi

# -- executable ---------------------------------------------------------------
mkdir -p "$BUILD/usr/bin"
cat > "$BUILD/usr/bin/$PKG" <<'EOF'
#!/usr/bin/python3
import sys

from collins.app import main

sys.exit(main())
EOF
chmod 755 "$BUILD/usr/bin/$PKG"

# -- desktop file / icon / metainfo --------------------------------------------
mkdir -p "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/icons/hicolor/scalable/apps" \
         "$BUILD/usr/share/$PKG/icons/hicolor/scalable/actions" \
         "$BUILD/usr/share/metainfo" \
         "$BUILD/usr/share/doc/$PKG"
# system-wide desktop entry: binary on PATH, no hardcoded working directory
sed -e "s|^Exec=.*|Exec=$PKG|" -e "/^Path=/d" \
    "$ROOT/data/$APP_ID.desktop" > "$BUILD/usr/share/applications/$APP_ID.desktop"
# ...-panel.svg is the status icon's artwork, drawn for 22px (see statusicon.py);
# the Debug variants are a source-checkout thing and stay out of the package.
cp "$ROOT/data/icons/$APP_ID.svg" \
   "$BUILD/usr/share/icons/hicolor/scalable/apps/"
# The action icons are app-private artwork on generic names, so they go in a
# Collins-owned search-path root rather than the shared hicolor theme: two
# packages cannot both own hicolor/.../tab-close-symbolic.svg, and
# agent-session-manager does. app.py finds this root; the tray gets it as
# IconThemePath, which is why -panel.svg belongs here rather than in apps/.
cp "$ROOT/data/icons/$APP_ID.svg" "$ROOT/data/icons/$APP_ID-panel.svg" \
   "$BUILD/usr/share/$PKG/icons/"
cp "$ROOT/data/icons/hicolor/scalable/actions/"*.svg \
    "$BUILD/usr/share/$PKG/icons/hicolor/scalable/actions/"
cp "$ROOT/data/$APP_ID.metainfo.xml" "$BUILD/usr/share/metainfo/"
cp "$ROOT/LICENSE" "$BUILD/usr/share/doc/$PKG/copyright"

# -- control ------------------------------------------------------------------
mkdir -p "$BUILD/DEBIAN"
INSTALLED_SIZE="$(du -sk "$BUILD" --exclude=DEBIAN | cut -f1)"
# Keep Depends in step with debian/control -- this package is the only channel
# Debian has, and gtksourceview5 is a hard requirement (editor.py exits with
# an install hint when it is missing, which apt should have made unnecessary).
# libspelling is only recommended: composer.py degrades to an unchecked text
# box without it. The two version floors are the measured ones: GTK 4.10 for
# Gtk.FileDialog/FontDialog, libadwaita 1.5 for Adw.AlertDialog and friends.
cat > "$BUILD/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0 (>= 4.10), gir1.2-adw-1 (>= 1.5), gir1.2-vte-3.91, gir1.2-gtksource-5
Recommends: gir1.2-glib-2.0, gir1.2-spelling-1
Installed-Size: $INSTALLED_SIZE
Maintainer: episode6 <support@episode6.com>
Homepage: https://github.com/episode6/collins
Description: Manage and resume AI coding agent sessions (GTK4 GUI)
 Native GTK4/libadwaita desktop app for the Claude Code CLI: browse all
 sessions grouped by project, name and star them, and resume any session in
 embedded terminal tabs. Unofficial community tool; the agent's own session
 data is never modified.
EOF

dpkg-deb --build --root-owner-group "$BUILD" "$ROOT/dist/${PKG}_${VERSION}_all.deb"
rm -rf "$BUILD"
echo "Built: dist/${PKG}_${VERSION}_all.deb"
