#!/bin/bash
# Stage an isolated Collins data environment for the docs screenshot refresh.
# Usage: stage-docs-data.sh <dir>
# Richer than stage-demo-data.sh: three projects, MCP config, usage fixture,
# transcripts with models/token usage, a claude shim for pretty terminals.
set -e
E2E="${1:?usage: stage-docs-data.sh <dir>}"
mkdir -p "$E2E"
E2E="$(cd "$E2E" && pwd)"
rm -rf "$E2E/projects" "$E2E/config" "$E2E/state" "$E2E/bin" "$E2E/dev"
mkdir -p "$E2E/projects" "$E2E/config/collins" "$E2E/state/collins/panel_history" "$E2E/bin"

U1=11111111-1111-4111-8111-111111111111
U2=22222222-2222-4222-8222-222222222222
U3=33333333-3333-4333-8333-333333333333
U4=44444444-4444-4444-8444-444444444444
U5=55555555-5555-4555-8555-555555555555
U6=66666666-6666-4666-8666-666666666666
U7=77777777-7777-4777-8777-777777777777

# Real dirs so terminals/panels can spawn in them. HOME is set to $E2E during
# capture, so paths render as ~/dev/<project>.
mkdir -p "$E2E/dev/alpha-widgets" "$E2E/dev/beta-server" "$E2E/dev/collins"

enc() { echo "$1" | sed 's/[^a-zA-Z0-9]/-/g'; }

mk() { # cwd uuid t1 t2 prompt reply
  local dir="$E2E/projects/$(enc "$1")"
  mkdir -p "$dir"
  cat > "$dir/$2.jsonl" <<EOF
{"type":"user","timestamp":"$3","cwd":"$1","message":{"role":"user","content":"$5"}}
{"type":"assistant","timestamp":"$4","cwd":"$1","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"text","text":"$6"}],"usage":{"input_tokens":18420,"output_tokens":2210}}}
EOF
}

A="$E2E/dev/alpha-widgets"
B="$E2E/dev/beta-server"
C="$E2E/dev/collins"

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
  "Recaptured all eight screenshots with the staged demo scene and updated the image references."

# U1: richer transcript (session-details shot) — several turns, tool use incl. MCP
DIR1="$E2E/projects/$(enc "$A")"
cat > "$DIR1/$U1.jsonl" <<EOF
{"type":"user","timestamp":"2026-07-27T09:12:03Z","cwd":"$A","message":{"role":"user","content":"Fix the flaky spinner animation on the dashboard"}}
{"type":"assistant","timestamp":"2026-07-27T09:12:41Z","cwd":"$A","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"text","text":"Looking at the dashboard spinner now."},{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"src/dashboard/spinner.css"}}],"usage":{"input_tokens":9210,"output_tokens":410}}}
{"type":"user","timestamp":"2026-07-27T09:12:44Z","cwd":"$A","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"(file contents)"}]}}
{"type":"assistant","timestamp":"2026-07-27T09:13:22Z","cwd":"$A","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"tool_use","id":"t2","name":"mcp__playwright__browser_snapshot","input":{}}],"usage":{"input_tokens":11020,"output_tokens":180}}}
{"type":"user","timestamp":"2026-07-27T09:13:29Z","cwd":"$A","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t2","content":"(page snapshot)"}]}}
{"type":"assistant","timestamp":"2026-07-27T09:14:05Z","cwd":"$A","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"tool_use","id":"t3","name":"Edit","input":{"file_path":"src/dashboard/spinner.css"}}],"usage":{"input_tokens":12830,"output_tokens":640}}}
{"type":"user","timestamp":"2026-07-27T09:14:08Z","cwd":"$A","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t3","content":"ok"}]}}
{"type":"user","timestamp":"2026-07-27T09:16:40Z","cwd":"$A","message":{"role":"user","content":"Nice — add a regression test so it stays fixed"}}
{"type":"assistant","timestamp":"2026-07-27T09:18:12Z","cwd":"$A","message":{"role":"assistant","model":"claude-opus-5","content":[{"type":"text","text":"The flake came from animating width during layout; the animation is now transform-only and a regression test pins it."}],"usage":{"input_tokens":15110,"output_tokens":1890}}}
EOF

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
  "emojis": { "$U4": "🔥" },
  "favorites": ["$U1", "$U4"],
  "expanded_groups": ["fav:", "proj:alpha-widgets", "proj:beta-server", "proj:collins"],
  "settings": {
    "auto_title_sessions": true,
    "notify_idle": false,
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

# claude shim: the terminal types `claude --resume <id>` — render demo output
# instead of hitting the real CLI against fake session ids.
cat > "$E2E/bin/claude" <<'EOF'
#!/bin/bash
printf '\n'
printf ' \033[38;5;173m✻\033[0m \033[1mClaude Code\033[0m — resuming session\n'
printf '\n'
printf ' \033[2m> Fix the flaky spinner animation on the dashboard\033[0m\n'
printf '\n'
printf ' \033[38;5;173m●\033[0m The flake came from animating width during layout — the spinner\n'
printf '   animation is now transform-only, and a regression test pins it.\n'
printf '   Running the dashboard suite: \033[1m20 passed\033[0m in 0.31s.\n'
printf '\n'
printf ' \033[2m──────────────────────────────────────────────────────────────\033[0m\n'
printf ' \033[1m❯\033[0m \033[7m \033[0m\n'
sleep infinity
EOF
chmod +x "$E2E/bin/claude"

echo "staged in $E2E"
