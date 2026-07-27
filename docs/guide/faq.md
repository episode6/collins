# FAQ

## Why did you remove Cursor support?

Can't support the new owner.

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

## Why is there no built-in chat UI?

By design. Collins wraps the real CLI in embedded terminals instead of
reimplementing a chat interface on top of it — the terminal is the source of
truth, and everything Claude Code can do works exactly as it does in any
other terminal. Collins adds the organization around it: naming, search,
tabs, status, and usage.

## Why Linux only?

The embedded terminal is [VTE](https://gitlab.gnome.org/GNOME/vte) — the
widget behind GNOME Terminal — and it's the only production-grade embeddable
terminal widget out there, which makes the app GTK4/Linux-native. Prefer an
external window? Right-click a session → *Open in Ghostty*.

## Why "Collins"?

> My wife keeps referring to Claude as Collins. So now when she asks me if
> I'm talking to Collins, I can say yes

See [What is Collins?](/guide/introduction) for the fork's full origin story.
