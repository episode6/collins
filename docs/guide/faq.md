# FAQ

## What kind of app is Collins?

Call it an **Agent-First IDE**, an **AI-Native Workspace**, or an **Agent
Orchestrator** — Collins is an opinionated version of what a desktop
workspace built around a coding agent should look like: the agent front and
center, with your sessions organized around it.

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

Collins talks to no third parties. Everything goes through the `claude` CLI
and the login you already have: resuming sessions runs `claude` in a
terminal; auto-titling and icon generation run headless `claude -p` jobs
with none of your skills, MCP servers, or the CLI's tools loaded
(pre-existing sessions are titled locally, and each model picker in
Preferences has a **None** option that turns the runs off); and three
things call Anthropic directly with the CLI's own stored token — read-only,
never written (when it's found expired, at launch or mid-run, Collins runs
one throwaway headless `claude -p` so the CLI itself refreshes its login):
the **usage panel**,
the **model pickers** (which list the models your login can use, asked for
about once a day and cached in `~/.cache/collins` in between), and the
**archive mirror**, which archives a session's claude.ai sibling when you
archive it here (*Archive on claude.ai too*, on by default). Pull request
features go through the GitHub CLI (`gh`) and your login there.

## What can break when Claude Code updates?

The parts of Collins that read the CLI's undocumented internals — its
transcript format, its credentials file, the usage and session-archive
endpoints, its `agents --json` listing, and its trust records. Anthropic can
change any of these without notice, and when one moves the feature built on
it stops working until Collins catches up; the app is written so that's a
blank panel or a skipped step, not a crash. The list is kept in
[How It Works](/guide/how-it-works#undocumented-apis-and-cli-internals).

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
