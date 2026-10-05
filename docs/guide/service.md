# The service

Collins is two programs. **`collins-service`** runs your sessions: every
agent's pty, the terminal panel's shells, the sandboxes, the shared state
(`state.json`), the session tools' MCP socket, every `gh` and `git` call
Collins makes for you, and everything that spends tokens. **`collins`**,
the window, is a client of it: it connects over a Unix socket, renders each
session in a terminal and hosts the workbench around it. Quitting the
window, or losing it to a crash, ends nothing: the agents keep working on
the service, and the next window picks them up.

Nothing about the window looks different because of the split, by design.
What changed is what *quit* and *close* mean, below.

::: tip Same machine, for now
Both halves run on the same machine and the same user. A service on another
machine, reached over ssh, is the next phase; the socket and the protocol
are built for it.
:::

## What runs where

| The service | The window |
| --- | --- |
| Agent processes, on ptys it holds; the screen model of record for each | A terminal per session, painted from the service's stream |
| `~/.claude`: transcripts, the CLI's trust entries, its login | The sidebar, composer, editor, git page, PR pages |
| `state.json`: names, favorites, archives, drafts, PR records, notifications, the pty table | `ui-state.json`: appearance, geometry, keybindings, panel layouts, `open_tabs` |
| Busy and finished-run detection, `/bg` handoffs, the agent list | Notification cards, sounds, the status icon, Caffeine Mode |
| The session tools' MCP socket, the sandboxes and their grants | Dialogs and what you type |
| `gh`, `git` reads for PRs, titles, usage, icon generation, login repair | The update check (it describes the window's package) |

The window never reads a session's state off its own terminal: busy, the
session a terminal belongs to, whether a prompt would land are the
service's facts, sent as events. The window keeps a mirror of the store and
of the settings that the service keeps current.

## Starting

You never start the service by hand. When the window starts it looks for the
service's socket and, finding none, starts one: `systemctl --user start
collins-service.service` where `systemctl` exists and the unit is
installed, else it spawns `collins-service` itself, detached. The unit
ships with the `.deb`, the RPM, the PPA and the AUR package and is written
by `collins --install-desktop` for a pip install. **The packages do not
enable it**: nothing runs until a window asks, and nothing starts at login.
To have it run from login, enable it yourself:

```bash
systemctl --user enable collins-service.service
```

A service spawned without the unit has no restart policy; one the unit
runs is restarted on failure. The debug instance (`./start-debug`, app id
`com.episode6.Collins.Debug`) always spawns its own, with no unit, beside
your real one. It shares your config directory and so your `state.json`:
run the debug instance and the real one one at a time.

A second `collins-service` for the same app id finds the first and exits 0.

A package upgrade never restarts a running service: the new window keeps
talking to the old service for as long as their protocols overlap. To
run the new service code, use **Restart service** (below).

## Quitting, detaching and reopening

A tab is a view over a session's terminal on the service.

- **Detach** (the tab menu, a session row's menu) closes the view and
  leaves the session running. The row becomes a *running row* (yellow, with
  the barber pole while the agent works); opening it attaches to the
  session as it stands, mid-turn included.
- **Close** is unchanged: the agent is asked to exit, with the same
  confirmations.
- **Quit** detaches every tab. With sessions running, the quit dialog says
  so and offers *Quit*, *Stop Sessions and Quit* (each agent asked to exit
  first, as quitting always did before the service) and *Keep Running (Hide
  Window)*. The status icon belongs to the window: it goes with it, though
  the sessions keep running.
- **Reopening.** The window records the tabs it has open on this service
  (`open_tabs` in `ui-state.json`) and reopens them at the next launch and
  after a reconnect: a session the service still runs is attached, one that
  ended meanwhile (a restart, a crash of the service) is resumed with
  `claude --resume`.
- A crash or `kill -9` of the window ends nothing.

A session counts as running only while its CLI runs: a shell left behind
after the agent exited is not a running row, and opening that session
resumes it. The service refuses to spawn a second CLI on a session that is
already running ("This session is already running in the Collins service");
the window attaches instead.

If the connection drops while a window is open, the window shows a
"Reconnecting to the Collins service" banner, finds or restarts the
service with a growing backoff (1, 2, 5, 10, then every 30 s) and attaches
every tab again from the service's screen model.

## Restarting and stopping

**Restart service** (the main menu) says what it costs (how many sessions
run, how many are working), and ends every session the way *Stop Sessions
and Quit* does. *Restart Now* does it at once; *Restart When Idle* has the
service wait until no session is busy, shown as "Restarting when idle"
with a Cancel where the reconnect banner goes. The service disconnects its
windows first, so a window sees the link go rather than each session end,
reconnects to the new service and resumes every open tab.

A crash of the service ends every agent, as a crash of the old single
process did; the sessions are resumable, and the window's reconnect brings
them back.

**The protocol mismatch dialog.** A service and a window speak the same
protocol or one apart; outside that the window refuses to connect and names
both versions. If the service is the older it offers *Restart Service* (the
service is sent SIGTERM) and *Quit*; if the service is newer, the dialog
asks you to upgrade Collins and the window quits. A restarted service that
still speaks another protocol gets the same ending.

To stop the service outright:

```bash
systemctl --user stop collins-service.service   # the unit
kill $(pgrep -f collins-service)                 # a spawned one
```

Either is SIGTERM: every session is ended as above and its id is recorded
(`resume_on_start` in `state.json`), and the service exits 0, so the unit
does not restart it by itself. The next window starts it again.

## Where things live

| What | Where |
| --- | --- |
| The API socket | `$XDG_RUNTIME_DIR/collins/<app id>/api.sock` (0600, directory 0700) |
| Single-instance lock | `service.lock` beside it, held for the service's life |
| The local proof | `local-proof` beside it: 32 random bytes, 0600, minted at each start |
| The session tools' socket | `mcp.sock` beside it |
| With no `$XDG_RUNTIME_DIR` | `~/.local/state/collins/<app id>/` for all of the above; a path too long for a Unix socket falls back to a 0700 directory under the temp directory |
| Service state | `~/.config/collins/state.json` |
| This device's state, `open_tabs` | `~/.config/collins/ui-state.json` |

The proof is how the window finds out it shares the service's filesystem:
it computes the proof's path for its own app id, opens it without following
a symlink, requires a regular 0600 file of its own uid holding exactly 32
bytes, and sends the contents back. A service that merely *names* a path
gets nothing read. Nothing in the window is gated on it yet; it is the
check later phases hide local-only actions behind. The socket is not a
security boundary: every client is already trusted with a shell.

## `collins-service --check`

On a desktop the user manager has the desktop's environment and the
service inherits it. On a headless box two things need doing first, and
`--check` says which:

```
$ collins-service --check
service:      0.1.5, app id com.episode6.Collins
lingering:    off (the service ends at logout on a headless box; `loginctl enable-linger` keeps it)
runtime dir:  /run/user/1000
socket:       /run/user/1000/collins/com.episode6.Collins/api.sock (none)
claude:       /home/you/.local/bin/claude
bwrap:        /usr/bin/bwrap
```

It exits 1 when `$XDG_RUNTIME_DIR` is unset or `claude` is not on the
login shell's `PATH`.

- **Lingering.** Without `loginctl enable-linger`, the user manager, the
  service and `$XDG_RUNTIME_DIR` go away at logout and do not exist at
  boot. On a headless box, `loginctl enable-linger` is the first step.
- **The CLI on `PATH`.** Shells spawn with the service's environment
  overlaid by a login-shell capture (`$SHELL -lic 'env -0'`, once at start,
  5 s, failing soft), which is what finds `~/.local/bin/claude` and your
  `PATH` on a box with no desktop. The capture wins (your `PATH` order
  too), except `COLLINS_*`, `XDG_*`, `PYTHONPATH`, `HOME`, `USER` and
  systemd's own variables, which stay as the service was started with,
  and the service's own leading `PATH` entries stay first. `SSH_AUTH_SOCK` is yours to provide there, as
  it is over ssh.

`collins-service --print-socket` starts the service if needed and prints the
socket's path.

## Logging

`COLLINS_LOG=INFO` (or `DEBUG`) turns on the service's logging, to stderr:
under the unit that is the journal (`journalctl --user -u
collins-service`). A service spawned by the window sends its output to
`/dev/null` unless `COLLINS_LOG` is set in the window's environment, in
which case it inherits the window's stdout and stderr.

## Requirements

`libsoup` 3 (`gir1.2-soup-3.0`, `libsoup3`) carries the socket and is a
dependency of every package. The service imports no GTK: it runs on a box
with no display.
