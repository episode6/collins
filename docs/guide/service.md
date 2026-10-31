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

::: tip Same machine, or not
Both halves run as the same user. They usually share a machine; a service
on another machine is reached over ssh, with the socket forwarded — see
[A service on another machine](./remote).
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
service's socket (`$XDG_RUNTIME_DIR/collins/<app id>/api.sock`) and,
finding none, starts one: `systemctl --user start
collins-service.service` where `systemctl` exists and the unit is
installed, else it spawns `collins-service` itself, detached. The unit
ships with the `.deb`, the RPM, the PPA and the AUR package and is written
by `collins --install-desktop` for a pip install. **The packages do not
enable it**: nothing runs until a window asks, and nothing starts at login.
To have it run from login, enable it yourself:

```bash
systemctl --user enable collins-service.service
```

When the socket takes longer than the window's wait, the window asks
systemd: a unit still starting (a slow cold start) or still stopping (a
graceful stop ahead of the start) is waited for, up to about 90 s in all;
one that is `failed` or stays `inactive` (a stale `ExecStart` from an
older install, a service exiting with an error), or is `active` with no
socket where the window looks (a unit running under another
`XDG_RUNTIME_DIR`), is not trusted again by that window, which spawns the
service itself from then on, says so once in a toast ("The Collins
service is running unmanaged"), and trusts the unit again the next time a
systemd-started service answers. `journalctl --user -u
collins-service.service` says why the unit failed.

A service spawned without the unit has no restart policy; one the unit
runs is restarted on failure. A source checkout (`data/install.sh`
installs the launcher and icons, no unit) spawns the service too: with no
`collins-service` on `PATH`, the window runs `python3 -m
collins.service.main` from its own checkout. The debug instance
(`./start-debug`, app id `com.episode6.Collins.Debug`) always spawns its
own, with no unit, beside your real one. It shares your config directory
and so your `state.json`: run the debug instance and the real one one at a
time.

A second `collins-service` for the same app id finds the first's lock and
exits 0.

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
  confirmations. A session you asked to stop (a tab's close, *Stop
  Sessions and Quit*) stops even if its window goes before the agent has
  left: the service runs the graceful exit and, when the agent ignores
  it, ends the session itself.
- **Quit** detaches every tab (unless *When quitting with running
  sessions* says otherwise). With sessions running, the quit dialog says
  so and offers *Quit*, *Stop Sessions and Quit* (each agent asked to exit
  first, as quitting always did before the service) and *Keep Running (Hide
  Window)*. The status icon belongs to the window: it goes with it, though
  the sessions keep running. A New Thread whose first prompt was never
  sent, and a tab whose agent has already exited, end with the window:
  nothing of theirs is left to keep running.
- **Reopening.** The window records the tabs it has open on this service
  (`open_tabs` in `ui-state.json`) and reopens them at the next launch and
  after a reconnect: a session the service still runs is attached, one that
  ended meanwhile (a restart, a crash of the service) is resumed with
  `claude --resume`.
- A crash or `kill -9` of the window ends nothing.

A session counts as running only while its CLI runs: a shell left behind
after the agent exited is not a running row, and opening that session
resumes it. A shell with no agent in it that no window shows — a launch
whose `claude` never started, or a detached session whose agent has quit
on its own — is closed by the service rather than kept: it is no running
session, and nothing would ever attach to it. A fresh launch is given
time first: a shell whose agent has never been seen running is held for
30 s from its spawn (the shell may still be running its rc before it
starts the typed command) and checked once more at the hold's end, so a
detach or a crash of the window right after Send does not close a healthy
launch; attaching within the hold cancels the check. The service refuses to
spawn a second CLI on a session that is already running ("This session is
already running in the Collins service"); the window attaches instead.

A finish or a message that arrives while no window is open is in the bell
when the next window connects, unread, not on the desktop: nothing was
there to announce it, and the window does not announce it late.
Set **Notification command** (`notify_command`, Preferences → *Service*) and
the service runs that program for each of them instead, with the
notification in `COLLINS_NOTIFY_*` environment variables (see
[Notifications](/guide/features#knowing-what-s-happening) and [A
service on another machine](./remote#notifications-while-you-are-away)).

If the connection drops while a window is open, the window shows a
"Reconnecting to the Collins service" banner, finds or restarts the
service with a growing backoff (1, 2, 5, 10, then every 30 s) and attaches
every tab again from the service's screen model. A window reconnecting
after a dropped link keeps its standing desktop notifications; only one
read elsewhere meanwhile comes down.

## Restarting and stopping

**Restart service** (the main menu) says what it costs (how many
foreground sessions run, how many are working, and that the `/bg`
background agents keep running: they belong to the CLI's daemon, not to
the service, see below), and ends every foreground session the way *Stop
Sessions and Quit* does. *Restart Now* does it at once; *Restart When
Idle* has the service wait until no session is busy (it looks every 2 s),
shown as "Restarting when idle" with a Cancel where the reconnect banner
goes. The service disconnects its windows first, so a window sees the
link go rather than each session end, reconnects to the new service and
resumes every open tab.

A crash of the service ends every foreground agent, as a crash of the old
single process did; the sessions are resumable, and the window's
reconnect brings them back. The `/bg` daemon and its agents hold no pty
of the service's and run on.

**The protocol mismatch dialog.** A service and a window speak the same
protocol or one apart; outside that the window refuses to connect and names
both versions. If the service is the older it offers *Restart Service* (the
service is sent SIGTERM) and *Quit*; if the service is newer, the dialog
asks you to upgrade Collins and the window quits. A restarted service that
still speaks another protocol gets the same ending.

To stop the service outright:

```bash
systemctl --user stop collins-service.service   # the unit
kill $(pgrep -f 'bin/collins-service')           # a spawned one
```

Either is SIGTERM. Stopping ends every foreground session the way *Stop
Sessions and Quit* does: each agent is asked to exit (the CLI's own exit
keystrokes, a worktree dialog answered, a bounded wait, then the process
ended for good), its session id is recorded (`resume_on_start` in
`state.json`), and the service exits 0, so the unit does not restart it by
itself; the next window starts it again and finds the sessions resumable.
*Restart service* runs the same stop sequence (`service.restart`) and
exits as cleanly; the window that asked reconnects and starts it again.

The unit is `KillMode=process`: systemd signals the service alone, never
the agents or the CLI's background daemon, so the graceful sequence above
is the service's own doing, and `/bg` agents and the daemon behind them
outlive a stop, a restart or a package upgrade of the service. Foreground
sessions do not, yet: their ptys close with the service that holds them,
until a later release's keeper holds them across a restart.

## Where things live

| What | Where |
| --- | --- |
| The API socket | `$XDG_RUNTIME_DIR/collins/<app id>/api.sock` (0600, directory 0700) |
| Single-instance lock | `service.lock` beside it, held for the service's life |
| The local proof | `local-proof` beside it: 32 random bytes, 0600, minted at each start |
| With no `$XDG_RUNTIME_DIR` | `~/.local/state/collins/<app id>/` for the three above; a path too long for a Unix socket falls back to a 0700 directory under the temp directory |
| The session tools' socket | `mcp.sock` beside `api.sock` when `$XDG_RUNTIME_DIR` is set; under the temp directory (`/tmp/collins/<app id>/`) otherwise |
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
shell:        /bin/bash (login profile and interactive rc read)
claude:       /home/you/.local/bin/claude
bwrap:        /usr/bin/bwrap
```

`shell:` names the shell the capture used and what it read: *login profile
and interactive rc read*; *login profile read; the interactive rc handed the
shell on* (with the fix, `~/.profile` or `~/.zprofile`, and "or did not
answer within 5 s" when the interactive shell was cut off at the limit);
*interactive rc read; the login profile did not answer*; or, when neither
answered, one of *no answer within 5 s*, *`shell` could not be run* (with
the reason: the shell is not installed) and *no PATH read from either
round*. In those three the service runs on its own environment. It never
makes `--check` exit 1.

It exits 1 when `$XDG_RUNTIME_DIR` is unset or `claude` is not on the
login shell's `PATH`.

- **Lingering.** Without `loginctl enable-linger`, the user manager, the
  service and `$XDG_RUNTIME_DIR` go away at logout and do not exist at
  boot. On a headless box, `loginctl enable-linger` is the first step.
- **The CLI on `PATH`.** Shells spawn with the service's environment
  overlaid by a login-shell capture, which is what finds
  `~/.local/bin/claude` and your `PATH` on a box with no desktop. Once at
  start the service runs two shells at once, `$SHELL -lc 'env -0'` (the
  login profile: `~/.profile`, `~/.bash_profile`, `~/.zprofile`) and
  `$SHELL -lic 'env -0'` (that and the interactive rc: a `PATH` that
  `pipx ensurepath` put in `~/.bashrc` behind Debian's non-interactive
  guard), to one 5 s deadline, and fails soft to its own environment. A
  shell counts only when what it printed holds a `PATH`; the interactive
  one's variables overlay the login one's. An rc that hands the shell on
  (`exec zsh`, `exec fish`, auto-tmux) prints nothing under `-i`, so the
  login profile's answer stands, but a `PATH` set only in such an rc is
  not seen: put it in `~/.profile` or `~/.zprofile`. `--check`'s `shell:`
  line says which answered. The capture wins (your `PATH` order too), except `COLLINS_*`, `XDG_*`, `PYTHONPATH`, `HOME`, `USER` and
  systemd's own variables, which stay as the service was started with,
  and the service's own leading `PATH` entries stay first. `SSH_AUTH_SOCK` is yours to provide there, as
  it is over ssh. Every `claude` the service itself looks up (what the
  window's welcome asks about, the command a session starts with, titles,
  icons) is found on that same `PATH`, so a service started by
  `--print-socket` over a non-interactive ssh command finds the CLI that
  `--check` reports.

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
