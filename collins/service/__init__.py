# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The session service: what runs every pty headless, GTK-free.

Nothing in this package imports GTK (or any of `gi`'s widget libraries): it
is the half of the split (see the split-service spec) that owns the ptys,
reads their output and answers the terminal's questions, so it has to run
with no display. Modules land one PR at a time; `termstream` is the first.
"""
