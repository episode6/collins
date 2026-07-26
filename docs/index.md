---
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.
# https://vitepress.dev/reference/default-theme-home-page
layout: home

hero:
  name: "Collins"
  text: "A native home for your AI coding-agent sessions"
  tagline: Browse, name, and resume every Claude Code session on your machine — in embedded terminal tabs.
  image:
    src: https://raw.githubusercontent.com/ghackett/agent-session-manager/main/data/screenshot.png
    alt: Collins
  actions:
    - theme: brand
      text: What is it?
      link: /guide/introduction
    - theme: alt
      text: Getting Started
      link: /guide/getting-started
    - theme: alt
      text: View on GitHub
      link: https://github.com/ghackett/agent-session-manager

features:
  - icon: 🗂️
    title: Every session, organized
    details: A sidebar of all your agent sessions, grouped by project, with a pinned Favorites section, live updates, and search.
  - icon: 🖥️
    title: Embedded terminals
    details: Click a session to resume it in a real VTE terminal tab — in its original project directory, inside your own shell.
  - icon: 🏷️
    title: Name & tag freely
    details: Give sessions custom names and tabs emoji prefixes. Everything is stored app-side; your agents' own data is never touched.
  - icon: 🔔
    title: Stays out of your way
    details: Status dots and desktop notifications tell you when a background session has finished, so you can work across many at once.
  - icon: 🔍
    title: Find anything
    details: Search the sidebar, search within a terminal's scrollback, and peek at a session's recent messages without resuming it.
  - icon: 🐧
    title: Native GTK4
    details: Built with GTK4, libadwaita, and VTE. Installs as a .deb or runs from source. GPL-3.0, open source.
---

## Install

::: code-group

```bash [Debian / Ubuntu (.deb)]
# built with ./scripts/build_deb.sh, or from a GitHub release
sudo apt install ./collins_*_all.deb
```

```bash [From source]
git clone https://github.com/ghackett/agent-session-manager.git collins
cd collins
python3 -m collins
```

:::

See [Getting Started](/guide/getting-started) for system requirements and
from-source instructions. The installed command is `collins`.
