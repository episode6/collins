<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-26. Full change history: git log for this file.
-->

# Getting Started

## Requirements

Collins is a GTK4 app. You'll need:

- **Python ≥ 3.10**
- **GTK 4**, **libadwaita ≥ 1.5**, **VTE** (the GTK 4 build), and **PyGObject**
- The [`claude` CLI](https://claude.com/claude-code) on your `PATH`

Optional, but worth having: the [**GitHub CLI**](https://cli.github.com/)
(`gh`), signed in. It is what Collins asks about the pull requests your
sessions open — state, CI, conflicts, unanswered comments — and what carries
out everything the PR menus offer. Without it a pull request is a number and
nothing else, and Collins says so on every launch that finds `gh` missing or
signed out — until you install it, or tick *Don't show this again*.

Install the system libraries with your distro's package manager:

::: code-group

```bash [Ubuntu / Debian]
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 gir1.2-gtksource-5 gir1.2-spelling-1
```

```bash [Fedora]
sudo dnf install python3-gobject gtk4 libadwaita vte291-gtk4 gtksourceview5 libspelling
```

```bash [Arch]
sudo pacman -S python-gobject gtk4 libadwaita vte4 gtksourceview5 libspelling
```

:::

The spelling package at the end of each line (`gir1.2-spelling-1` /
`libspelling`) is optional — it adds spell-check to the prompt composer, and
Collins runs fine without it.

## Install

### Ubuntu — the episode6 PPA

The maintained channel on Ubuntu — Collins upgrades with the rest of your
system from here:

```bash
sudo add-apt-repository ppa:episode6/stable
sudo apt install collins
```

The PPA covers **Ubuntu 24.04 (noble)** and **26.04 (resolute)**, and the
derivatives that share them — Linux Mint, Pop!_OS, elementary OS, Zorin.
Ubuntu 22.04 (jammy) is out of scope: it ships libadwaita 1.1 and GTK 4.6,
and Collins uses APIs from libadwaita 1.5 and GTK 4.10.

It appears in your app grid as **Collins**, and the installed command is
`collins`.

Already running Collins on Ubuntu from the `.deb`, PyPI or a checkout? The
sidebar's ☰ menu offers **Add the Ubuntu PPA…** until the PPA is configured:
it shows the commands above and runs them in a terminal of the current
session, where `sudo` can ask for your password.

### Debian — `.deb`

A Launchpad PPA can only ever serve Ubuntu, so on Debian — and the
Debian-family distros that don't build on Ubuntu — the `.deb` is the way in.
(On distros outside the Debian family entirely, use PyPI below.) Grab the
latest from the
[releases page](https://github.com/episode6/collins/releases/latest), or
build it with `./scripts/build_deb.sh`, then install it — dependencies are
pulled in automatically:

```bash
sudo apt install ./collins_*_all.deb
```

A `.deb` installed this way adds no apt source, so it does not update
itself — watch the releases page. Debian 13 (trixie) and newer have
everything Collins needs; Debian 12 (bookworm) does not (libadwaita 1.2
against the 1.5 APIs).

### PyPI — pipx or pip

Available everywhere, and the way in on a distro with no package of its
own — Arch, Fedora, and anything else outside the Debian family:

```bash
pipx install --system-site-packages collins   # or: pip install --user collins
collins --install-desktop                     # optional: add it to the app grid
```

`--system-site-packages` is not optional: Collins declares no PyPI
dependencies on purpose, because PyGObject, GTK, VTE and GtkSourceView come
from your distro's packages (above). An environment that cannot see them
exits on `import gi` the first time you run the app.

`collins --install-desktop` writes the launcher, app icon and metainfo under
`~/.local/share` for your user — the same three files the `.deb` installs
system-wide. The sidebar menu offers the same thing as **Install desktop
icon**, shown only when nothing has put Collins in your app grid yet. It is
the only extra step: the toolbar and sidebar artwork ships inside the
package.

### From source

```bash
git clone https://github.com/episode6/collins.git
cd collins
python3 -m collins
```

To add a desktop launcher and icon for your user:

```bash
./data/install.sh
```

## Updating

Each channel updates its own way; none of them touches your sessions or
your `~/.config/collins/` state, so an update is always safe mid-stream.

| Installed via | To update |
| --- | --- |
| **Ubuntu PPA** | Nothing special — `sudo apt update && sudo apt upgrade` (or your desktop's software updater) picks Collins up with everything else. |
| **Debian `.deb`** | Download the new `.deb` from the [releases page](https://github.com/episode6/collins/releases/latest) and install it over the old one the same way: `sudo apt install ./collins_*_all.deb`. apt treats it as an upgrade; settings stay put. |
| **pipx** | `pipx upgrade collins` — the `--system-site-packages` flag you installed with is remembered by the venv, so it needn't be repeated. |
| **pip** | `pip install --user --upgrade collins` |
| **From source** | `git pull` in the checkout. The launcher from `./data/install.sh` points at the checkout, so it needs no re-run. |

Restart Collins afterwards — a running instance keeps the old code until it
is relaunched. If any sessions are still working, close the window with
**Keep Running (Hide Window)** and relaunch: the hidden window comes back,
but on the old code; use the menu's **Quit** (sessions can be backgrounded
first) for a real restart.

## First run

On first launch the sidebar lists every session found under
`~/.claude/projects/`, with all groups collapsed. Each session is given an
auto-generated title (locally, from its first prompt), so you see names
instead of UUIDs right away. Expand a project, click a session, and it opens
in a terminal tab that resumes it. If you haven't used Claude Code yet, start
a session right from the app — the **New Session** button (`Ctrl+Shift+T`)
asks for a project folder and opens a new-chat screen where you write the
first prompt; Send launches `claude` there — or run `claude` in a project
yourself and the session will show up automatically.

Later launches start with no session open by default. Turn on **Reopen the
last session** (Preferences → Session behavior) and the app instead reopens the
session you had focused when you closed the window.

![The main window on first run](/img/main-window.png)
