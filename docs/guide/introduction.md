<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-08. Full change history: git log for this file.
-->

# What is Collins?

Collins is a native Linux desktop app that gives your
[Claude Code](https://claude.com/claude-code) sessions a proper home.

As for the name — straight from the README:

> My wife keeps referring to Claude as Collins by mistake. So now when she asks me if I'm talking to Collins, I can say yes.

Collins is a fork of
[agent-session-manager](https://github.com/r4nd3l/agent-session-manager) by
Máté Molnár — see the
[original project's website](https://r4nd3l.github.io/agent-session-manager/)
for the app it grew out of. All credit for the original goes there; the fork
is GPL-3.0 like the original.

![Collins](/img/hero.png)

If you use a coding agent daily, you accumulate **dozens of sessions** scattered
across every project you touch. They live as JSONL transcripts named by UUID
(Claude Code, for instance, stores them under `~/.claude/projects/`) — there's
no overview, no way to name them, and no quick way to tell which one was which.
The agent's own `--resume` only shows a picker for the current directory.

This app fixes that. It scans every session on your machine, presents them in
a searchable sidebar grouped by project, **auto-generates a short title** for
each one, lets you **name and star** the ones that matter, and opens any of
them in an **embedded terminal tab** that resumes it right where it last
worked — re-attaching to sessions that are still running in the background.

::: tip Unofficial community tool
Collins is an independent community project, not affiliated with
or endorsed by any agent vendor (including Anthropic). It is strictly read-only
with respect to your agents' data — names, favorites, and all app state live in
`~/.config/collins/`. Your transcripts are never modified.
:::

## Why use it?

- **One place for everything.** Every session, every project — sessions newest
  first, projects in your own order (drag a project header to rearrange) —
  instead of UUID-named files and per-directory pickers.
- **Human-readable.** Every session gets an auto-generated title; rename
  `a3b2152e…` to “JWT auth” yourself, star your daily drivers, add an emoji to
  a tab.
- **Work in parallel.** Open several sessions as tabs, each with its own
  secondary shell panel. Sidebar guide lines show what each one is doing, and
  a session can raise a desktop notification when it wants you back — or
  background a session (`/bg`) and re-attach later.
- **Stay oriented.** Search the sidebar, search a terminal's scrollback, peek
  at a session's recent messages before resuming it, and keep an eye on your
  Claude subscription usage under the session list.

## How it compares

Coding agents are terminal programs, and you can absolutely keep using them
bare. Collins is a layer *on top* — it doesn't replace the CLI, it
launches and organizes it:

| | Bare `--resume` | Collins |
| --- | --- | --- |
| See all sessions across projects | ❌ | ✅ |
| Custom names & favorites | ❌ | ✅ |
| Multiple sessions side by side | Manual terminals | ✅ Tabs |
| "Which session was this?" | Read the UUID | ✅ Preview, details, peek |
| Finished-in-the-background alerts | ❌ | ✅ Notifications |

## What's next?

Head to [Getting Started](/guide/getting-started) to install it, or browse the
full [Features](/guide/features).
