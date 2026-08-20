---
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-19. Full change history: git log for this file.
# https://vitepress.dev/reference/default-theme-home-page
layout: home

hero:
  name: "Collins"
  text: "A vibecoded agentic developement environment to manage, orchestrate and compliment all your Claude Code CLI sessions"
  tagline: Browse, name, and resume every Claude Code session on your machine — in embedded terminal tabs.
  image:
    src: /img/hero.png
    alt: Collins
  actions:
    - theme: brand
      text: What is it?
      link: /guide/introduction
    - theme: brand
      text: Getting Started
      link: /guide/getting-started
    - theme: alt
      text: View on GitHub
      link: https://github.com/episode6/collins

features:
  - icon: 🗂️
    title: Every session, organized
    details: A sidebar of all your agent sessions, grouped by project — searchable, auto-titled, starred and renamed as you like, with live guide lines saying which sessions are working and which are waiting on you.
  - icon: 🖥️
    title: Embedded terminals
    details: Click a session to resume it in a real VTE terminal tab — in the directory it last worked in, inside your own shell — re-attaching to backgrounded sessions instead of resuming a copy.
  - icon: 📋
    title: Copy & paste that just works
    details: Plain Ctrl+C copies whenever text is selected (and interrupts otherwise); Ctrl+V pastes; right-click for a menu. No Ctrl+Shift gymnastics.
  - icon: 🔀
    title: Pull requests, start to merge
    details: Every PR a session opens is tracked on its row and tab — CI, conflicts, unanswered comments — with the actions to match — read the diff beside the terminal, merge, ask for a review, or send the failure back to the agent as a prompt.
  - icon: 🧰
    title: An IDE around the agent
    details: A code editor with the agent's latest edits one click away, shell panels under every tab, and a spell-checked prompt composer that opens the moment you start typing — drop images straight into it.
  - icon: 🔔
    title: Stays out of your way
    details: Close the window and sessions keep running behind a status icon that counts what's waiting for you. Sessions raise notifications when they need you, and Caffeine Mode keeps the machine awake while agents work.
---

## An opinionated, Claude-first workspace

Call it an **Agent-First IDE**, an **AI-Native Workspace**, or an **Agent
Orchestrator** — Collins is an opinionated take on what a desktop workspace
built around a coding agent should look like: the agent front and center,
with your sessions organized around it. It is **native** — GTK4, libadwaita,
and VTE, not a webview — and it is also, deliberately, a tool for
**Claude**: there are no plans to support other agents. And to be upfront
about it: Collins is itself entirely vibecoded — the code is written by
[Claude Code](https://claude.com/claude-code). GPL-3.0, open source.

## Why "Collins"?

> My wife keeps referring to Claude as Collins by mistake. So now when she asks me if I'm talking to Collins, I can say yes.

Collins is a fork of
[agent-session-manager](https://github.com/r4nd3l/agent-session-manager)
by Máté Molnár — see the
[original project's website](https://r4nd3l.github.io/agent-session-manager/).
All credit for the original app goes there; this fork is GPL-3.0 like the
original.

## Install

::: code-group

```bash [Ubuntu (PPA)]
sudo add-apt-repository ppa:episode6/stable
sudo apt install collins
```

```bash [PyPI (pipx)]
pipx install --system-site-packages collins
collins --install-desktop   # optional: add it to the app grid
```

```bash [Debian (.deb)]
# from the GitHub releases page
sudo apt install ./collins_*_all.deb
```

```bash [From source]
git clone https://github.com/episode6/collins.git
cd collins
python3 -m collins
```

:::

The PPA is the channel that updates itself, on Ubuntu 24.04+ and its
derivatives (Mint, Pop!_OS, elementary, Zorin); the `.deb` covers Debian and
the rest of its family; and PyPI is the way in everywhere else — it works
anywhere the system GTK libraries are installed (`--system-site-packages` is
required — Collins gets GTK from your distro, not from PyPI). See [Getting Started](/guide/getting-started) for system
requirements and the details of each channel. The installed command is
`collins`.
