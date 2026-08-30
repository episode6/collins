#!/bin/bash
# Recapture the docs site's screenshot set (and the README's) from one staged
# scene, headlessly.
#
# Usage: refresh-docs-screenshots.sh <repo-root> [scene ...]
#
# With no scene named, every scene is shot and the PNGs land in
# docs/public/img/ (plus data/screenshot.png for the README). Name scenes to
# redo only those. Each scene is its own app launch on a fresh headless
# display; the staged data tree is shared so the shots stay comparable.
set -e
ROOT="$(cd "${1:?usage: refresh-docs-screenshots.sh <repo-root> [scene ...]}" && pwd)"
shift
SCRIPTS="$ROOT/.agents/capture-screenshots/scripts"
IMG="$ROOT/docs/public/img"

E2E=$(mktemp -d)
RUN=r$(basename "$E2E" | tr -cd 'A-Za-z0-9')
bash "$SCRIPTS/stage-docs-data.sh" "$E2E"

export COLLINS_APP_ID=com.episode6.Collins.E2E.$RUN
export COLLINS_PROJECTS_DIR="$E2E/projects"
export COLLINS_CLAUDE_CONFIG="$E2E/claude.json"
export COLLINS_CHATS_DIR="$E2E/chats"
export COLLINS_USAGE_FIXTURE="$E2E/usage-fixture.json"
export XDG_CONFIG_HOME="$E2E/config"
export XDG_STATE_HOME="$E2E/state"
export HOME="$E2E"                # paths render as ~/dev/<project>
export PATH="$E2E/bin:$PATH"      # the typed `claude` is the shim

shoot() { # scene out.png [capture-docs args...]
  local scene="$1" out="$2"
  shift 2
  echo "== $scene -> $out"
  bash "$SCRIPTS/with-headless-display.sh" \
    python3 "$SCRIPTS/capture-docs.py" "$ROOT" "$out" --scene "$scene" "$@" \
    2>&1 | grep -v -e CRITICAL -e 'Gtk-WARNING' || true
  [ -s "$out" ] || { echo "no PNG for $scene" >&2; exit 1; }
}

crop_sidebar() { # in.png out.png — the sidebar column of a full-window shot
  python3 - "$1" "$2" <<'PY'
import sys
from PIL import Image
im = Image.open(sys.argv[1])
# The sidebar ends at the first column that is the sidebar/content border:
# scan the header row for the leftmost pixel darker than the header's own
# background past 250px.
px = im.convert("RGB").load()
y = 45
bg = px[5, y]
edge = next(x for x in range(250, im.width) if sum(abs(a - b) for a, b in zip(px[x, y], bg)) > 60)
im.crop((0, 0, edge, im.height)).save(sys.argv[2])
print(f"sidebar crop {edge}x{im.height}")
PY
}

want() { [ $# -eq 0 ] && return 0; for s in "$@"; do [ "$s" = "$SCENE" ] && return 0; done; return 1; }

run_scene() {
  SCENE="$1"
  want "${SCENES[@]}" || return 0
  case "$SCENE" in
    main-window)    shoot main-window "$IMG/main-window.png" --size 1280x860 ;;
    hero)           shoot hero "$IMG/hero.png" --size 1280x860
                    cp "$IMG/hero.png" "$ROOT/data/screenshot.png"
                    crop_sidebar "$IMG/hero.png" "$IMG/sidebar.png" ;;
    quick-switcher) shoot quick-switcher "$IMG/quick-switcher.png" --size 1280x860 ;;
    session-details) shoot session-details "$IMG/session-details.png" --size 1280x860 ;;
    mcp-servers)    shoot mcp-servers "$IMG/mcp-servers.png" --size 1280x860 ;;
    preferences)    shoot preferences "$IMG/preferences.png" --size 1280x860 ;;
    terminal-panel) shoot terminal-panel "$IMG/terminal-panel.png" --size 1280x860 ;;
    new-chat)       shoot new-chat "$IMG/new-chat.png" --size 1280x860 --set welcome_seen=true ;;
    composer)       shoot composer "$IMG/composer.png" --size 1280x860 ;;
    pr-page)        shoot pr-page "$IMG/pr-page.png" --size 1700x950 \
                      --set page_panel_size_right=700 --settle-ms 6000 ;;
    editor-panel)   shoot editor-panel "$IMG/editor-panel.png" --size 1600x950 \
                      --set editor_width=620 ;;
    notifications)  shoot notifications "$IMG/notifications.png" --size 1280x860 --settle-ms 4000 ;;
    attachments-panel) shoot attachments-panel "$IMG/attachments-panel.png" --size 1500x1100 \
                      --set page_panel_size_right=420 --settle-ms 5000 ;;
    welcome)        shoot welcome "$IMG/welcome.png" --size 1280x860 --set welcome_seen=false ;;
    welcome-cli)    shoot welcome-cli "$IMG/welcome-cli.png" --size 1280x860 --set welcome_seen=false ;;
  esac
}

# The welcome scenes set welcome_seen back to false in the shared state.json
# (the dialog only shows on an unseen install), so they go after every plain
# scene and new-chat, which sets it true again, goes last of all — last
# anyway, since the draft it writes to state.json would otherwise show up as
# a Draft row in every scene shot after it.
SCENES=("$@")
for s in main-window hero quick-switcher session-details mcp-servers preferences \
         terminal-panel composer pr-page editor-panel attachments-panel notifications \
         welcome welcome-cli new-chat; do
  run_scene "$s"
done
echo "staged data left in $E2E"
