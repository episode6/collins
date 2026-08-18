#!/usr/bin/env bash
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-18. Full change history: git log for this file.

# Install the desktop launcher and icon for the current user.
set -euo pipefail

DATA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="com.episode6.Collins"

ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
# App-private artwork on generic names (alert-symbolic, tab-close-symbolic, ...)
# stays out of the shared hicolor theme, where it would outrank the system's own
# icons for every application on the machine -- and, in the packaged case,
# collide with agent-session-manager's copies. app.py looks here.
ACTION_ICON_DIR="$HOME/.local/share/collins/icons/hicolor/scalable/actions"
OLD_ACTION_ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/actions"
APPS_DIR="$HOME/.local/share/applications"
METAINFO_DIR="$HOME/.local/share/metainfo"

mkdir -p "$ICON_DIR" "$ACTION_ICON_DIR" "$APPS_DIR" "$METAINFO_DIR"

# Remove launchers/icons/metainfo installed under the app's previous ids
# (upstream's io.github.r4nd3l.AgentSessionManager and this fork's short-lived
# io.github.ghackett.Collins), so stale entries don't linger in the app grid.
for old_id in io.github.r4nd3l.AgentSessionManager io.github.ghackett.Collins; do
  rm -f "$APPS_DIR/$old_id.desktop" "$APPS_DIR/$old_id.Debug.desktop" \
        "$ICON_DIR/$old_id.svg" "$METAINFO_DIR/$old_id.metainfo.xml"
done

# Earlier runs of this script copied the action icons into the shared user
# theme. Left behind they are searched ahead of the system's, so they would
# keep shadowing these names for every app -- including agent-session-manager,
# whose own agent-claude-symbolic and tab-close-symbolic they overwrote.
if [ -d "$OLD_ACTION_ICON_DIR" ]; then
  for svg in "$DATA_DIR/icons/hicolor/scalable/actions/"*.svg; do
    rm -f "$OLD_ACTION_ICON_DIR/$(basename "$svg")"
  done
  rmdir --ignore-fail-on-non-empty "$OLD_ACTION_ICON_DIR" 2>/dev/null || true
fi

# ...-panel.svg is the status icon's artwork, drawn for 22px (see statusicon.py)
cp "$DATA_DIR/icons/$APP_ID.svg" "$DATA_DIR/icons/$APP_ID.Debug.svg" \
   "$DATA_DIR/icons/$APP_ID-panel.svg" "$DATA_DIR/icons/$APP_ID.Debug-panel.svg" \
   "$ICON_DIR/"
cp "$DATA_DIR/icons/hicolor/scalable/actions/"*.svg "$ACTION_ICON_DIR/"
cp "$DATA_DIR/$APP_ID.metainfo.xml" "$METAINFO_DIR/"

# Point Path= at wherever this checkout lives
sed "s|^Path=.*|Path=$(dirname "$DATA_DIR")|" "$DATA_DIR/$APP_ID.desktop" > "$APPS_DIR/$APP_ID.desktop"

# Hidden desktop file for the debug instance (start-debug), named after its
# app id so GNOME matches the window and shows the real icon in the dock.
sed -e "s|^Path=.*|Path=$(dirname "$DATA_DIR")|" \
    -e "s|^Name=.*|Name=Collins (Debug)|" \
    -e "s|^Exec=.*|Exec=env COLLINS_APP_ID=$APP_ID.Debug python3 -m collins|" \
    -e "s|^Icon=.*|Icon=$APP_ID.Debug|" \
    -e "s|^StartupWMClass=.*|StartupWMClass=$APP_ID.Debug|" \
    "$DATA_DIR/$APP_ID.desktop" > "$APPS_DIR/$APP_ID.Debug.desktop"
echo "NoDisplay=true" >> "$APPS_DIR/$APP_ID.Debug.desktop"

update-desktop-database "$APPS_DIR" 2>/dev/null || true
gtk-update-icon-cache -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "Installed: $APPS_DIR/$APP_ID.desktop"
echo "Installed: $APPS_DIR/$APP_ID.Debug.desktop"
echo "Installed: $ICON_DIR/$APP_ID.svg"
echo "Installed: $ICON_DIR/$APP_ID.Debug.svg"
