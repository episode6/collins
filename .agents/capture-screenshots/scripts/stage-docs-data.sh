#!/bin/bash
# Stage an isolated Collins data environment for the docs screenshot refresh.
# Usage: stage-docs-data.sh <dir>
# Richer than stage-demo-data.sh: three projects (each a git checkout with a
# project-icon.svg), MCP config, a usage fixture, transcripts with models and
# token usage, pull requests on the sidebar rows, an attachments gallery, and
# a claude shim that draws demo output for `--resume`.
#
# The scene assumes HOME=<dir> at capture time, so paths render as
# ~/dev/<project> and the projects' git state is read from inside the tree.
set -e
E2E="${1:?usage: stage-docs-data.sh <dir>}"
mkdir -p "$E2E"
E2E="$(cd "$E2E" && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
rm -rf "$E2E/projects" "$E2E/config" "$E2E/state" "$E2E/bin" "$E2E/dev" "$E2E/chats"
mkdir -p "$E2E/projects" "$E2E/config/collins" "$E2E/state/collins/panel_history" \
  "$E2E/bin" "$E2E/chats"

U1=11111111-1111-4111-8111-111111111111
U2=22222222-2222-4222-8222-222222222222
U3=33333333-3333-4333-8333-333333333333
U4=44444444-4444-4444-8444-444444444444
U5=55555555-5555-4555-8555-555555555555
U6=66666666-6666-4666-8666-666666666666

A="$E2E/dev/alpha-widgets"
B="$E2E/dev/beta-server"
C="$E2E/dev/collins"
mkdir -p "$A/src/dashboard" "$A/tests" "$A/design" "$A/reports" "$B/server" "$C"

# --- project files: what the editor's tree, the agent-files list and the
# attachments gallery point at ------------------------------------------------
cat > "$A/src/dashboard/spinner.css" <<'EOF'
/* Dashboard loading spinner. */

.spinner {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 3px solid var(--accent-dim);
  border-top-color: var(--accent);
  animation: spin 800ms linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .spinner {
    animation: none;
    opacity: 0.6;
  }
}
EOF
cat > "$A/src/dashboard/index.js" <<'EOF'
import "./spinner.css";

export function mountDashboard(root) {
  const spinner = document.createElement("div");
  spinner.className = "spinner";
  root.append(spinner);
  return () => spinner.remove();
}
EOF
cat > "$A/tests/test_spinner.py" <<'EOF'
from dashboard import spinner


def test_spinner_animates_transform_only():
    rules = spinner.animated_properties()
    assert rules == {"transform"}


def test_spinner_respects_reduced_motion():
    assert spinner.reduced_motion_rule().animation == "none"
EOF
cat > "$A/package.json" <<'EOF'
{
  "name": "alpha-widgets",
  "version": "2.4.1",
  "scripts": { "test": "pytest tests -q", "build": "vite build" }
}
EOF
printf '# alpha-widgets\n\nDashboard widgets for the alpha console.\n' > "$A/README.md"
printf 'run,ms\n1,312\n2,298\n3,305\n4,301\n5,299\n' > "$A/reports/spinner-timings.csv"
printf '<!doctype html><title>Flake report</title><h1>Spinner flake: 0/50 runs</h1>\n' \
  > "$A/reports/flake-report.html"
cp "$HERE/assets/nap-time.jpg" "$HERE/assets/bag-hat.jpg" "$HERE/assets/tabby-closeup.jpg" \
  "$A/design/"

printf 'package server\n\nfunc Route(path string) Handler { return routes[path] }\n' > "$B/server/router.go"
printf '# beta-server\n' > "$B/README.md"
printf '# collins\n' > "$C/README.md"

# --- project icons: the sidebar wears these in place of the folder icon ----
cat > "$A/project-icon.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">
  <rect x="1" y="1" width="6" height="6" rx="1.5" fill="#c01c28"/>
  <rect x="9" y="1" width="6" height="6" rx="1.5" fill="#f66151"/>
  <rect x="1" y="9" width="6" height="6" rx="1.5" fill="#f66151"/>
  <rect x="9" y="9" width="6" height="6" rx="1.5" fill="#c01c28"/>
</svg>
EOF
cat > "$B/project-icon.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">
  <rect x="1.5" y="1.5" width="13" height="5.5" rx="1.5" fill="#3d3846"/>
  <rect x="1.5" y="9" width="13" height="5.5" rx="1.5" fill="#3d3846"/>
  <circle cx="4.5" cy="4.25" r="1.1" fill="#57e389"/>
  <circle cx="4.5" cy="11.75" r="1.1" fill="#57e389"/>
  <rect x="7" y="3.5" width="5.5" height="1.5" rx=".75" fill="#9a9996"/>
  <rect x="7" y="11" width="5.5" height="1.5" rx=".75" fill="#9a9996"/>
</svg>
EOF
cp "$ROOT/project-icon.svg" "$C/project-icon.svg"

# --- git state: the footer's branch, the new-chat screen's worktree box ----
mkgit() { # dir branch
  git -C "$1" init -q -b main
  git -C "$1" -c user.name=demo -c user.email=demo@example.com add -A
  git -C "$1" -c user.name=demo -c user.email=demo@example.com commit -q -m "Initial import"
  [ "$2" = main ] || git -C "$1" checkout -q -b "$2"
}
mkgit "$A" fix/spinner-flake
mkgit "$B" main
mkgit "$C" main

enc() { echo "$1" | sed 's/[^a-zA-Z0-9]/-/g'; }

# The prompt and reply land in the JSON verbatim, unescaped: keep them free
# of double quotes and backslashes (a session title reads them back).
mk() { # cwd uuid t1 t2 prompt reply
  local dir="$E2E/projects/$(enc "$1")"
  mkdir -p "$dir"
  cat > "$dir/$2.jsonl" <<EOF
{"type":"user","timestamp":"$3","cwd":"$1","message":{"role":"user","content":"$5"}}
{"type":"assistant","timestamp":"$4","cwd":"$1","effort":"high","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"text","text":"$6"}],"usage":{"input_tokens":18420,"output_tokens":2210}}}
EOF
}

# alpha-widgets — U1 gets a longer transcript below
mk "$A" $U2 2026-07-27T09:41:03Z 2026-07-27T09:44:19Z \
  "Add dark mode support to the settings page" \
  "Settings page now follows the system color scheme, with a manual override stored per user."
mk "$A" $U3 2026-07-26T16:20:11Z 2026-07-26T16:31:40Z \
  "Write tests for the export flow" \
  "Added 14 tests covering CSV and JSON export, including the empty-selection edge case."

# beta-server
mk "$B" $U4 2026-07-27T08:05:44Z 2026-07-27T08:19:02Z \
  "Profile the request router and remove the hot allocation" \
  "The per-request header map was the hot allocation; it is now pooled. p99 latency dropped 18%."
mk "$B" $U5 2026-07-25T11:12:33Z 2026-07-25T11:30:57Z \
  "Add request tracing behind a feature flag" \
  "Tracing spans are emitted for every handler when TRACE_REQUESTS=1; overhead is negligible when off."

# collins
mk "$C" $U6 2026-07-27T10:02:18Z 2026-07-27T10:09:46Z \
  "Refresh the docs screenshots for the next release" \
  "Recaptured every screenshot with the staged demo scene and updated the image references."

# U1: richer transcript (session-details shot) — several turns, tool use incl.
# MCP, and edits whose paths feed the editor's "Agent files" list.
DIR1="$E2E/projects/$(enc "$A")"
cat > "$DIR1/$U1.jsonl" <<EOF
{"type":"user","timestamp":"2026-07-27T09:12:03Z","cwd":"$A","message":{"role":"user","content":"Fix the flaky spinner animation on the dashboard"}}
{"type":"assistant","timestamp":"2026-07-27T09:12:41Z","cwd":"$A","effort":"high","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"text","text":"Looking at the dashboard spinner now."},{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"$A/src/dashboard/spinner.css"}}],"usage":{"input_tokens":9210,"output_tokens":410}}}
{"type":"user","timestamp":"2026-07-27T09:12:44Z","cwd":"$A","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"(file contents)"}]}}
{"type":"assistant","timestamp":"2026-07-27T09:13:22Z","cwd":"$A","effort":"high","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"tool_use","id":"t2","name":"mcp__playwright__browser_snapshot","input":{}}],"usage":{"input_tokens":11020,"output_tokens":180}}}
{"type":"user","timestamp":"2026-07-27T09:13:29Z","cwd":"$A","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t2","content":"(page snapshot)"}]}}
{"type":"assistant","timestamp":"2026-07-27T09:14:05Z","cwd":"$A","effort":"high","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"tool_use","id":"t3","name":"Edit","input":{"file_path":"$A/src/dashboard/spinner.css"}}],"usage":{"input_tokens":12830,"output_tokens":640}}}
{"type":"user","timestamp":"2026-07-27T09:14:08Z","cwd":"$A","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t3","content":"ok"}]}}
{"type":"user","timestamp":"2026-07-27T09:16:40Z","cwd":"$A","message":{"role":"user","content":"Nice — add a regression test so it stays fixed"}}
{"type":"assistant","timestamp":"2026-07-27T09:17:30Z","cwd":"$A","effort":"high","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"tool_use","id":"t4","name":"Write","input":{"file_path":"$A/tests/test_spinner.py"}}],"usage":{"input_tokens":13900,"output_tokens":520}}}
{"type":"user","timestamp":"2026-07-27T09:17:33Z","cwd":"$A","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t4","content":"ok"}]}}
{"type":"assistant","timestamp":"2026-07-27T09:18:12Z","cwd":"$A","effort":"high","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"text","text":"The flake came from animating width during layout; the animation is now transform-only and a regression test pins it."}],"usage":{"input_tokens":15110,"output_tokens":1890}}}
EOF

# Attachment timestamps: a few minutes apart, all earlier today.
NOW=$(date +%s)
T1=$((NOW - 3200)); T2=$((NOW - 2900)); T3=$((NOW - 2500)); T4=$((NOW - 2100)); T5=$((NOW - 1700))

cat > "$E2E/config/collins/state.json" <<EOF
{
  "names": {
    "$U1": "Fix spinner animation",
    "$U2": "Dark mode settings",
    "$U3": "Export flow tests",
    "$U4": "Router profiling",
    "$U5": "Request tracing",
    "$U6": "Docs screenshot refresh"
  },
  "favorites": ["$U1", "$U4"],
  "expanded_groups": ["fav:", "proj:alpha-widgets", "proj:beta-server", "proj:collins"],
  "session_prs": {
    "$U1": [{"number": 214, "url": "https://github.com/episode6/alpha-widgets/pull/214",
             "repository": "episode6/alpha-widgets",
             "title": "Fix the flaky spinner animation on the dashboard",
             "state": "OPEN", "checks": {"passed": 3}, "mergeable": "MERGEABLE"}],
    "$U2": [{"number": 221, "url": "https://github.com/episode6/alpha-widgets/pull/221",
             "repository": "episode6/alpha-widgets",
             "title": "Dark mode for the settings page",
             "state": "DRAFT", "checks": {"passed": 1, "pending": 2}, "mergeable": "MERGEABLE"}],
    "$U4": [{"number": 198, "url": "https://github.com/episode6/beta-server/pull/198",
             "repository": "episode6/beta-server",
             "title": "Pool the per-request header map",
             "state": "OPEN", "checks": {"passed": 2, "failed": 1}, "mergeable": "MERGEABLE"}],
    "$U5": [{"number": 176, "url": "https://github.com/episode6/beta-server/pull/176",
             "repository": "episode6/beta-server",
             "title": "Request tracing behind a feature flag",
             "state": "OPEN", "checks": {"passed": 3}, "mergeable": "MERGEABLE", "unresolved": true}]
  },
  "session_attachments": {
    "$U1": [
      {"key": "$A/design/tabby-closeup.jpg", "kind": "image", "source": "lightbox",
       "caption": "Close-up of the tabby, for the hero banner", "at": $T5, "last": $T5},
      {"key": "$A/reports/flake-report.html", "kind": "file", "source": "transcript",
       "at": $T4, "last": $T4},
      {"key": "$A/design/bag-hat.jpg", "kind": "image", "source": "transcript",
       "context": "…the second cat photo, bag-hat.jpg, is the one I'd crop square for the empty state…",
       "at": $T3, "last": $T3},
      {"key": "$A/reports/spinner-timings.csv", "kind": "file", "source": "transcript",
       "at": $T2, "last": $T2},
      {"key": "$A/design/nap-time.jpg", "kind": "image", "source": "lightbox",
       "caption": "Nap time — the loading-state placeholder", "at": $T1, "last": $T1}
    ]
  },
  "settings": {
    "title_model": "none",
    "welcome_seen": true,
    "window_width": 1280,
    "window_height": 860
  }
}
EOF

cat > "$E2E/state/collins/panel_history/$U1.txt" <<EOF
\$ git status
On branch fix/spinner-flake
nothing to commit, working tree clean
\$ python3 -m pytest tests/dashboard -q
....................                                      [100%]
20 passed in 0.31s
EOF

# MCP config: one global server, one scoped to alpha-widgets
cat > "$E2E/claude.json" <<EOF
{
  "mcpServers": {
    "github": { "type": "http", "url": "https://api.githubcopilot.com/mcp/" }
  },
  "projects": {
    "/home/ghackett/dev/alpha-widgets": {
      "mcpServers": {
        "playwright": { "type": "stdio", "command": "npx", "args": ["@playwright/mcp@latest"] }
      }
    }
  }
}
EOF

# Usage fixture: resets_at computed relative to now so countdowns look live
python3 - "$E2E/usage-fixture.json" <<'PYEOF'
import json, sys
from datetime import datetime, timedelta, timezone
now = datetime.now(timezone.utc)
iso = lambda d: d.isoformat().replace("+00:00", "Z")
json.dump({
    "limits": [
        {"kind": "session", "percent": 34, "severity": "normal",
         "resets_at": iso(now + timedelta(hours=2, minutes=10))},
        {"kind": "weekly_all", "percent": 52, "severity": "normal",
         "resets_at": iso(now + timedelta(days=3, hours=5))},
        {"kind": "weekly_scoped", "percent": 18, "severity": "normal",
         "resets_at": iso(now + timedelta(days=3, hours=5)),
         "scope": {"model": {"display_name": "Opus"}}},
    ],
    "extra_usage": {"is_enabled": True, "decimal_places": 2, "currency": "USD",
                    "used_credits": 1250, "monthly_limit": 5000,
                    "spend_limit_reached": False},
}, open(sys.argv[1], "w"))
PYEOF

# claude shim: the terminal types `claude --resume <id> …` — clear the screen
# the way the real CLI does and render demo output instead of hitting the real
# CLI against fake session ids. The idle prompt is ❯ + no-break space, which is
# what Collins reads as "the agent is at its input box".
cat > "$E2E/bin/claude" <<'EOF'
#!/bin/bash
printf '\033[H\033[2J'
printf '\n'
printf ' \033[38;5;173m✻\033[0m \033[1mClaude Code\033[0m — resuming session\n'
printf '\n'
printf ' \033[2m> Fix the flaky spinner animation on the dashboard\033[0m\n'
printf '\n'
printf ' \033[38;5;173m●\033[0m The flake came from animating width during layout —\n'
printf '   the spinner animation is now transform-only, and a\n'
printf '   regression test pins it. Running the dashboard suite:\n'
printf '   \033[1m20 passed\033[0m in 0.31s.\n'
printf '\n'
printf ' \033[2m────────────────────────────────────────────────────────\033[0m\n'
printf '\033[1m❯\033[0m \033[7m \033[0m'
sleep infinity
EOF
chmod +x "$E2E/bin/claude"

echo "staged in $E2E"

# The CLI settings the new-chat screen reads its pickers' defaults from:
# an effort pinned by /effort, so the effort picker reads Default (High).
mkdir -p "$E2E/.claude"
echo '{"effortLevel": "high"}' > "$E2E/.claude/settings.json"
