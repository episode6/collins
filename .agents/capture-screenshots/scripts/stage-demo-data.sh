#!/bin/bash
# Stage an isolated Collins data environment for screenshots.
#
# Usage: stage-demo-data.sh <dir>
# Writes <dir>/projects, <dir>/config/collins/state.json, <dir>/claude.json,
# and <dir>/state (XDG_STATE_HOME: saved panel-terminal history).
# Scene: project "alpha-widgets" with 3 sessions, project "beta-server" whose
# only session is favorited (so its header renders with a session count of 0).
# The first alpha-widgets session has saved panel history, so capturing it
# with capture.py --open-session $U1 --panel demos the restored panel.
set -e
E2E="${1:?usage: stage-demo-data.sh <dir>}"
mkdir -p "$E2E"
E2E="$(cd "$E2E" && pwd)"
rm -rf "$E2E/projects" "$E2E/config" "$E2E/state"
mkdir -p "$E2E/projects" "$E2E/config/collins" "$E2E/state/collins/panel_history"

U1=11111111-1111-4111-8111-111111111111
U2=22222222-2222-4222-8222-222222222222
U3=33333333-3333-4333-8333-333333333333
U4=44444444-4444-4444-8444-444444444444

mk() { # encoded-project-dir cwd uuid prompt
  local dir="$E2E/projects/$1"
  mkdir -p "$dir"
  cat > "$dir/$3.jsonl" <<EOF
{"type":"user","timestamp":"2026-07-25T14:12:03Z","cwd":"$2","message":{"role":"user","content":"$4"}}
{"type":"assistant","timestamp":"2026-07-25T14:12:09Z","cwd":"$2","message":{"role":"assistant","content":"Done."}}
EOF
}

mk -home-ghackett-dev-alpha-widgets /home/ghackett/dev/alpha-widgets $U1 "Fix the flaky spinner animation on the dashboard"
mk -home-ghackett-dev-alpha-widgets /home/ghackett/dev/alpha-widgets $U2 "Add dark mode support to the settings page"
mk -home-ghackett-dev-alpha-widgets /home/ghackett/dev/alpha-widgets $U3 "Write tests for the export flow"
mk -home-ghackett-dev-beta-server   /home/ghackett/dev/beta-server   $U4 "Profile the request router and remove the hot allocation"

cat > "$E2E/config/collins/state.json" <<EOF
{
  "names": {
    "$U1": "Fix spinner animation",
    "$U2": "Dark mode settings",
    "$U3": "Export flow tests",
    "$U4": "Router profiling"
  },
  "favorites": ["$U4"],
  "expanded_groups": ["fav:", "proj:alpha-widgets", "proj:beta-server"],
  "settings": {
    "auto_title_sessions": false,
    "notify_idle": false,
    "window_width": 1100,
    "window_height": 720
  }
}
EOF

cat > "$E2E/state/collins/panel_history/$U1.txt" <<EOF
\$ git status
On branch main
nothing to commit, working tree clean
\$ python3 -m pytest tests/ -q
........................................................  [100%]
102 passed in 0.18s
EOF

echo '{}' > "$E2E/claude.json"
echo "staged in $E2E"
