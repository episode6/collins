# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The session service: what runs every pty headless, GTK-free.

Nothing in this package imports GTK (or any of `gi`'s widget libraries): it
is the half of the split (see the split-service spec) that owns the ptys,
reads their output and answers the terminal's questions, so it has to run
with no display. Modules land one PR at a time; `termstream` was the first.

`session.Session` is a session tab's logic taken out of the tab — launching
the agent, reading and writing its input box, the transcript resolver and
tail, activity, the close flows — talking to its terminal through the two
ports in `ports` (`PtyPort` to write to it, `ScreenPort` to read it). Today
the ports are adapters over the tab's own VTE (`terminal.VtePtyPort`,
`terminal.VteScreenPort`); the split's later PRs swap them for the service's
own pty and screen (`termstream`, `termscreen`); see
~/specs/collins/split-service-and-client.md §3.5.
"""
