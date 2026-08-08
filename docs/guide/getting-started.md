<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-08. Full change history: git log for this file.
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
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91
```

```bash [Fedora]
sudo dnf install python3-gobject gtk4 libadwaita vte291-gtk4
```

```bash [Arch]
sudo pacman -S python-gobject gtk4 libadwaita vte4
```

:::

## Install

### Debian / Ubuntu — `.deb`

Build the package with `./scripts/build_deb.sh`, or grab the latest `.deb`
from the
[releases page](https://github.com/episode6/collins/releases/latest)
if one is published, then install it — dependencies are pulled in
automatically:

```bash
sudo apt install ./collins_*_all.deb
```

It appears in your app grid as **Collins**, and the installed command is
`collins`.

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

On later launches the app reopens the session you had focused when you closed
the window.

![The main window on first run](/img/main-window.png)
