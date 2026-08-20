<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-19. Full change history: git log for this file.
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

### Debian and everything else — `.deb`

A Launchpad PPA can only ever serve Ubuntu, so on Debian the `.deb` is the
way in. Grab the latest from the
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

## First run

On first launch the sidebar lists every session found under
`~/.claude/projects/`, with all groups collapsed. Each session is given an
auto-generated title (locally, from its first prompt), so you see names
instead of UUIDs right away. Expand a project, click a session, and it opens
in a terminal tab that resumes it. If you haven't used Claude Code yet, start
a session right from the app — the **New Session** button (`Ctrl+Shift+T`)
asks for a project folder and launches `claude` there — or run `claude` in a
project yourself and the session will show up automatically.

Later launches start with no session open by default. Turn on **Reopen the
last session** (Preferences → Startup) and the app instead reopens the
session you had focused when you closed the window.

![The main window on first run](/img/main-window.png)
