---
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-30. Full change history: git log for this file.
# https://vitepress.dev/reference/default-theme-home-page
layout: home

hero:
  name: "Collins"
  text: "A vibecoded agentic development environment to manage, orchestrate and complement all your Claude Code CLI sessions"
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
    details: A sidebar of all your agent sessions, grouped by project — searchable, auto-titled, starred and renamed as you like, with guide lines saying which are working and which are waiting on you — and a click resumes any of them in a real VTE terminal tab, in the directory it last worked in, inside your own shell.
  - icon: 📋
    title: Copy & paste that just works
    details: Plain Ctrl+C copies whenever text is selected (and interrupts otherwise); Ctrl+V pastes; right-click for a menu. No Ctrl+Shift gymnastics.
  - icon: ✍️
    title: Drafts that wait for you
    details: A real prompt box — multi-line, spell-checked, open the moment you start typing, pasted images and all — that never loses a prompt. Close the tab or quit mid-sentence and the draft is back when you return, for a running session or one you haven't started yet.
  - icon: 🔀
    title: Pull requests, start to merge
    details: Every PR a session opens is tracked on its row and tab — CI, conflicts, unanswered comments — with the actions to match — read the diff beside the terminal, merge, ask for a review, or send the failure back to the agent as a prompt.
  - icon: 🧰
    title: An IDE around the agent
    details: A code editor with the agent's latest edits one click away, shell panels under every tab, a gallery of everything the session has shown you, and the composer floating over the terminal or docked below it.
  - icon: 🔔
    title: Stays out of your way
    details: Close the window and sessions keep running behind a status icon that counts what's waiting for you — reopen one and Collins re-attaches to the live process instead of resuming a copy. Sessions raise notifications when they need you — a card inside the window, or a desktop notification when you're away — a bell in the header keeps the history, and Caffeine Mode keeps the machine awake while agents work.
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

```bash [Fedora (COPR)]
sudo dnf copr enable episode6/stable
sudo dnf install collins
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

The PPA and the COPR are the channels that update themselves — the PPA on
Ubuntu 24.04 and 26.04 LTS and their derivatives (Mint, Pop!_OS, elementary,
Zorin), the COPR on every current Fedora and on RHEL 10 and its rebuilds; the
`.deb` covers Debian and the rest of its family, with an `.rpm` on the same
releases page for an RPM distro that would rather not enable a repository;
and PyPI is the way in everywhere else — it works
anywhere the system GTK libraries are installed (`--system-site-packages` is
required — Collins gets GTK from your distro, not from PyPI). See [Getting Started](/guide/getting-started) for system
requirements and the details of each channel. The installed command is
`collins`.
