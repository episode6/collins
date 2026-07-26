#!/usr/bin/env bash
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

# Install the desktop launcher and icon for the current user.
set -euo pipefail

DATA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="io.github.r4nd3l.AgentSessionManager"

ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
ACTION_ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/actions"
APPS_DIR="$HOME/.local/share/applications"
METAINFO_DIR="$HOME/.local/share/metainfo"

mkdir -p "$ICON_DIR" "$ACTION_ICON_DIR" "$APPS_DIR" "$METAINFO_DIR"
cp "$DATA_DIR/icons/$APP_ID.svg" "$ICON_DIR/"
cp "$DATA_DIR/icons/hicolor/scalable/actions/"*.svg "$ACTION_ICON_DIR/"
cp "$DATA_DIR/$APP_ID.metainfo.xml" "$METAINFO_DIR/"

# Point Path= at wherever this checkout lives
sed "s|^Path=.*|Path=$(dirname "$DATA_DIR")|" "$DATA_DIR/$APP_ID.desktop" > "$APPS_DIR/$APP_ID.desktop"

# Hidden desktop file for the debug instance (start-debug), named after its
# app id so GNOME matches the window and shows the real icon in the dock.
sed -e "s|^Path=.*|Path=$(dirname "$DATA_DIR")|" \
    -e "s|^Name=.*|Name=Agent Session Manager (Debug)|" \
    -e "s|^Exec=.*|Exec=env CSM_APP_ID=$APP_ID.Debug python3 -m claude_session_manager|" \
    -e "s|^StartupWMClass=.*|StartupWMClass=$APP_ID.Debug|" \
    "$DATA_DIR/$APP_ID.desktop" > "$APPS_DIR/$APP_ID.Debug.desktop"
echo "NoDisplay=true" >> "$APPS_DIR/$APP_ID.Debug.desktop"

update-desktop-database "$APPS_DIR" 2>/dev/null || true
gtk-update-icon-cache -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "Installed: $APPS_DIR/$APP_ID.desktop"
echo "Installed: $APPS_DIR/$APP_ID.Debug.desktop"
echo "Installed: $ICON_DIR/$APP_ID.svg"
