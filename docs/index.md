---
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-08. Full change history: git log for this file.
# https://vitepress.dev/reference/default-theme-home-page
layout: home

hero:
  name: "Collins"
  text: "A vibecoded agentic developement environment to manage, orchestrate and compliment all your Claude cli coding-agent sessions"
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
    details: A sidebar of all your agent sessions, grouped by project, with a pinned Favorites section, auto-generated titles, live updates, and search.
  - icon: 🖥️
    title: Embedded terminals
    details: Click a session to resume it in a real VTE terminal tab — in the directory it last worked in, inside your own shell — plus a secondary shell panel per tab (Ctrl+J).
  - icon: 📋
    title: Copy & paste that just works
    details: Plain Ctrl+C copies whenever text is selected (and interrupts otherwise); Ctrl+V pastes; right-click for a menu. No Ctrl+Shift gymnastics.
  - icon: 🏷️
    title: Name & tag freely
    details: Give sessions custom names and tabs emoji prefixes. Everything is stored app-side; your agents' own data is never touched.
  - icon: 🔔
    title: Stays out of your way
    details: Guide lines, barber-pole activity, and notifications the agent raises itself tell you when a background session needs you, so you can work across many at once.
  - icon: 📊
    title: Know your limits
    details: A Claude usage panel under the session list shows your subscription limits with reset countdowns — read from the claude CLI's own login.
  - icon: 🐧
    title: Native GTK4
    details: Built with GTK4, libadwaita, and VTE. Installs as a .deb or runs from source. GPL-3.0, open source.
---

## An opinionated, Claude-first workspace

Call it an **Agent-First IDE**, an **AI-Native Workspace**, or an **Agent
Orchestrator** — Collins is an opinionated take on what a desktop workspace
built around a coding agent should look like: the agent front and center,
with your sessions organized around it. It is also, deliberately, a tool for
**Claude** — there are no plans to support other agents. And to be upfront
about it: Collins is itself entirely vibecoded — the code is written by
[Claude Code](https://claude.com/claude-code).

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

```bash [Debian / Ubuntu (.deb)]
# built with ./scripts/build_deb.sh, or from a GitHub release
sudo apt install ./collins_*_all.deb
```

```bash [From source]
git clone https://github.com/episode6/collins.git
cd collins
python3 -m collins
```

:::

See [Getting Started](/guide/getting-started) for system requirements and
from-source instructions. The installed command is `collins`.
