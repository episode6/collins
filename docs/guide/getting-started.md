<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-07-26. Full change history: git log for this file.
-->

# Getting Started

## Requirements

Collins is a GTK4 app. You'll need:

- **Python ≥ 3.10**
- **GTK 4**, **libadwaita ≥ 1.5**, **VTE** (the GTK 4 build), and **PyGObject**
- The [`claude` CLI](https://claude.com/claude-code) on your `PATH`

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

On first launch the sidebar lists every session found on disk (for Claude Code,
under `~/.claude/projects/`), with all groups collapsed. Expand a project, click
a session, and it opens in a terminal tab that resumes it. If you haven't used a
supported agent yet, run `claude` in a project once and the session will show up
automatically.

![The main window on first run](/img/main-window.png)
