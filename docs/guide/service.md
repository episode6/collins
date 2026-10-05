# The service

Collins is two programs. **`collins-service`** runs your sessions: every
agent's pty, the terminal panel's shells, the sandboxes, the shared state
(`state.json`), the session tools' MCP socket, the GitHub CLI calls and
everything that spends tokens. **`collins`**, the window, is a client of
it: it connects over a Unix socket, renders each session in a terminal
and hosts the workbench around it. Quitting the window, or losing it to a
crash, ends nothing: the agents keep working on the service, and the next
window picks them up.

::: tip Where things are
This page describes the split as of this release: both halves still run
on the same machine. A service on another machine, reached over ssh, is
the next phase.
:::

## What runs where

| The service | The window |
| --- | --- |
| Agent processes, on ptys it holds | A terminal per session, painted from the service's stream |
| The screen model of record for every pty | The sidebar, composer, editor, git page, PR pages |
| `state.json`: names, favorites, archives, drafts, notifications, the pty table | `ui-state.json`: appearance, geometry, keybindings, panel layouts |
| The session tools' MCP socket, the sandboxes | Notifications, the status icon, Caffeine Mode |
| `gh`, `git`, titles, the usage panel's fetch, login repair | What you see and type |

## Starting and stopping

You never start the service by hand. When the window starts it looks for
the service's socket (`$XDG_RUNTIME_DIR/collins/<app id>/api.sock`) and,
finding none, starts one: through `systemctl --user start
collins-service.service` where the unit is installed, else by spawning
`collins-service` itself. The unit ships with the packages and is written
by `collins --install-desktop` for a pip install; it is never enabled by
a package, so nothing runs until a window asks.

The service keeps running after the window quits. To stop it:

```bash
systemctl --user stop collins-service.service   # the unit
kill $(pgrep -f collins-service)                 # a spawned one
```

Stopping ends every session the way *Stop sessions and quit* does: each
agent is asked to exit (the CLI's own exit keystrokes, a worktree dialog
answered, a bounded wait, then the process ended for good), its session
id is recorded, and the next window finds them resumable. A second
`collins-service` for the same app id finds the first's lock and exits
at once.

::: warning Until the next release's session lifecycle lands
A session that is still running in the service cannot be opened again
from the sidebar yet: the service refuses to start a second copy of it
("This session is already running in the Collins service"). Attaching to
the running one is the next piece of the split.
::: A client's `service.restart` does the same and exits
cleanly, so the unit does not restart it by itself; the window that
asked reconnects and starts it again.

If the connection drops while a window is open, the window shows a
"Reconnecting to the Collins service" banner, finds or restarts the
service with a growing backoff, and attaches every tab again from the
service's screen model.

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

- **Lingering.** Without `loginctl enable-linger`, the user manager, the
  service and `$XDG_RUNTIME_DIR` go away at logout and do not exist at
  boot.
- **The CLI on `PATH`.** Shells spawn with the service's environment
  overlaid by a login-shell capture (`$SHELL -lic 'env -0'`, once at
  start), which is what finds `~/.local/bin/claude` and your `PATH` on a
  box with no desktop. `SSH_AUTH_SOCK` is yours to provide there, as it is
  over ssh.

`--print-socket` starts the service if needed and prints the socket's
path; a second `collins-service` for the same app id finds the live
socket and exits.

## The debug instance

`./start-debug` runs the window under the app id
`com.episode6.Collins.Debug`, and that window spawns a service of its own
under the same id (`collins-service --app-id com.episode6.Collins.Debug`,
no unit) beside your real one: its own socket, but the same config
directory and so the same `state.json`. Two services writing one state
file is one too many: run the debug instance and the real one one at a
time.
