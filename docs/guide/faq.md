# FAQ

## What kind of app is Collins?

Call it an **Agent-First IDE**, an **AI-Native Workspace**, or an **Agent
Orchestrator** — Collins is an opinionated version of what a desktop
workspace built around a coding agent should look like: the agent front and
center in real terminals, with your sessions organized around it.

## Why did you remove Cursor support?

Can't support the new owner.

## Will Collins support other agents?

No. Collins is a tool for Claude specifically, and there are no plans to
introduce support for other agents.

## Is Collins affiliated with Anthropic?

No. Collins is an independent, unofficial community tool — not affiliated
with or endorsed by Anthropic (or any other agent vendor). It's a desktop
companion for the `claude` CLI you already use.

## Will it modify my Claude Code sessions?

No. Transcripts under `~/.claude/projects/` are read-only to Collins — names,
favorites, emoji, and every other bit of app state live in
`~/.config/collins/`. The only exceptions are the explicit *Move to trash*
and *Delete permanently* actions, both behind a confirmation. See
[How It Works](/guide/how-it-works) for details.

## Does anything leave my machine?

Collins makes no network calls of its own and talks to no third parties.
Everything goes through the `claude` CLI and login you already have: resuming
sessions runs `claude` in a terminal, auto-titling summarizes a new session's
first prompt via a headless `claude -p` run (pre-existing sessions are titled
locally, and the toggle is in Preferences), and the usage panel queries
Anthropic's usage endpoint with the CLI's own stored token — read-only, never
refreshed or written.

## Do I need an API key?

No. If `claude` works in your terminal, Collins works. Session titles and the
usage panel reuse the CLI's existing login — no extra credentials.

## Why Linux only?

Because I wasn't happy with any of the existing claude tools for linux,
while other platforms get native claude app support.

## Why "Collins"?

> My wife keeps referring to Claude as Collins by mistake. So now when she asks me if
> I'm talking to Collins, I can say yes.

See [What is Collins?](/guide/introduction) for the fork's full origin story.
