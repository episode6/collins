# A service on another machine

The window and the service are two programs joined by a Unix socket ([The
service](./service)). That socket can be on another machine: a box that
stays on while your laptop sleeps, a workstation you reach over ssh. The
window connects to it the way you would — `ssh` — and nothing else is
opened: no port, no token, no listener on the network. Collins forwards
the service's socket over ssh and talks to it as if it were local.

::: tip What you need on the box
`collins-service` installed and on the login shell's `PATH`, `claude`
logged in there, and an ssh login that works without a prompt (a key in
your agent). `collins-service --check` on the box says what is missing.
:::

## Connecting

**Connect to…** in the app menu lists the services this computer knows:
*This computer* first, then every ssh connection you have added. **Add
ssh connection** takes a name and the host as `user@host` or
`user@host:port`; **Test** runs

```
ssh -o BatchMode=yes user@host collins-service --print-socket
```

and shows the socket's path it printed (which also starts the service on
the box when none is running), or ssh's own last line when it could not
connect. **Connect** switches the window to that service: every open tab
detaches with no question (its session keeps running where it is), the
sidebar refills from the other machine's sessions, and the tabs you last
had open there come back. The banner says *Reconnecting to \<name\>*
while the forward is being set up. Switching back is the same gesture
with *This computer* selected.

From a terminal, `collins --connect NAME` opens the window on a saved
connection (`local` names this computer); if a window is already running,
it switches. The connection you used last is the one the next launch
connects to.

## What ssh does for you

Collins runs two ssh commands and nothing else:

```
ssh -o BatchMode=yes TARGET collins-service --print-socket
ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -o BatchMode=yes -N -L <runtime dir>/collins/remote/<id>.sock:<remote api.sock> TARGET
```

Authentication, encryption, host verification and key management are all
ssh's: whatever `~/.ssh/config` says about the host applies (a jump host,
a key file, a user name). **`BatchMode=yes`** means ssh never asks you
anything, so three things must already be in place:

- **The key** is in your ssh agent (`ssh-add`), or has no passphrase.
- **The host's key** is already in `~/.ssh/known_hosts`: connect once by
  hand (`ssh user@host`) and accept it.
- **No password login.** A host that only takes passwords cannot be used.

When any of these is missing, ssh exits 255 and *Test* shows its last line
("Permission denied (publickey)", "Host key verification failed") with a
reminder of what batch mode means.

The second command holds the forward for as long as you stay connected.
Its `ServerAliveInterval` is ssh's own liveness check (45 s to give up);
Collins' socket has keepalives of its own on both ends, so a link that
dies is noticed within about 20 s. When the forward ends — the network
went away, the box rebooted, the laptop slept — the window shows
*Reconnecting to \<name\>* with ssh's last line, and tries again at 1, 2,
5, 10 and then every 30 s: a fresh `--print-socket` (which starts the
service again if it had stopped) and a fresh forward. Nothing is lost
meanwhile: the sessions run on the box, and the terminal is redrawn from
the service's screen when the link is back.

## On the box

`collins-service` on a headless box has two needs of its own, and
[`collins-service --check`](./service#collins-service-check) reports both:

- **Lingering.** Without `loginctl enable-linger`, the service, its
  `$XDG_RUNTIME_DIR` and everything under it end when your last ssh
  session closes. With no runtime directory the socket lands under
  `~/.local/state/collins/<app id>/api.sock` instead and the service
  lives as long as the process does; `--print-socket` starts it detached
  from the ssh command that asked, so it outlives that command, but not
  a logout that tears the session down. `loginctl enable-linger` is the
  first step on a box with no desktop.
- **`claude` on the login shell's `PATH`.** The service starts terminals
  with your login shell's environment, so `~/.local/bin/claude` is found
  the way your shell finds it. `SSH_AUTH_SOCK` for the sessions' own git
  pushes is yours to provide there, as it is over any ssh login.

The claude login on the box is the box's: log in once there
(`ssh -t user@host claude`). Nothing of your laptop's `~/.claude` is
copied over.

## What is different when the service is elsewhere

Everything you see is the box's: its sessions, its files, its git
checkouts. A few things hand a path to a program on *your* computer, and
those stay hidden when your computer is not the service's machine — they
would open the wrong file on the wrong machine:

- *Open in Ghostty*, *Open in File Manager*, *Open in Terminal*, every
  *Open In…* app and *Reveal transcript*.
- `file:` URIs on the clipboard (the editor's file clipboard still works
  within Collins; other apps get the paths as text).
- A dropped file mentioned by its own path (it is uploaded instead).
- A clicked link that is not `http`, `https` or `mailto`.

*Open With…* on a picture or a plain document (pdf, txt, md, csv, json,
log) still works: it hands your desktop app the window's cached copy.
The file choosers that pick a folder on the service (where a new session
starts, a project to add) are being replaced by a picker that lists the
box's folders; until then they open on your own filesystem.

Collins decides "is this the service's machine" by a proof it reads from
its own disk, not by anything the service claims: a window reaching a
service over ssh never gets the local extras, whatever the far side says.
