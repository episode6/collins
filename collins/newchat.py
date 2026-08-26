# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The new-chat screen's bookkeeping: draft ids, records and labels.

A session Collins starts fresh no longer opens straight onto the agent's
console. It opens onto a *new-chat screen* -- the project's icon and name
over the native composer, with a checkbox for the worktree launch and a
picker for the model -- and only the first prompt's Send spawns the CLI
(see newchatview.NewChatView and TerminalTab.begin_session). Until then
the tab is a draft: nothing is running, so closing it loses nothing *as
long as what was written is kept* -- and it is, here. A draft that holds
text, or has a terminal open in its dock, is written to state.json under a
``draft-`` id, listed in the sidebar under its project, and comes back the
same way a session does.

The rules -- what makes a tab worth keeping, what a record has to look
like to be trusted back off disk, what a sidebar row calls a draft -- are
pure functions of the data, so they live here where CI's GTK-free tests
can reach them.
"""

from __future__ import annotations

import re
import uuid

DRAFT_PREFIX = "draft-"

# How much of a draft's first line a sidebar row shows. Rows ellipsize on
# their own; this is the cap on what is handed to them, so a pasted wall of
# text doesn't become the row's tooltip in full either.
_LABEL_CHARS = 80


def new_draft_id() -> str:
    """A fresh draft id. Distinct from session ids (the CLI's UUIDs) and the
    window's ``placeholder-N`` ids by its prefix, so everything that files
    things by id can tell a draft apart."""
    return f"{DRAFT_PREFIX}{uuid.uuid4().hex}"


def is_draft_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith(DRAFT_PREFIX) and len(value) > len(DRAFT_PREFIX)


def draft_worthy(text: str, has_panel_pages: bool) -> bool:
    """Whether a new-chat tab has anything in it worth keeping.

    Text is the obvious half: a prompt half-written is the user's own
    writing. A dock with pages in it is the other: a shell opened beside
    the screen (Ctrl+J) is work in progress too -- something was run, or
    is about to be -- and the tab that holds it should come back with it.
    Whitespace alone is an emptied box, not a draft (see
    composerkeys.stashable_draft).
    """
    return bool(text.strip()) or bool(has_panel_pages)


def draft_record(
    cwd: str,
    provider: str,
    text: str,
    worktree: bool | None,
    layout: dict | None,
    created: float,
    model: str = "",
) -> dict:
    """The persisted shape of a draft (see valid_draft for the contract).

    *worktree* is the checkbox as the user left it, or None when it was
    never touched -- an untouched box keeps following the project's default,
    which may change before the draft is picked up again. *model* is the
    picker's choice, "" while it stands on the CLI's default -- which,
    likewise, is read afresh when the draft comes back rather than kept.
    """
    record = {
        "cwd": cwd,
        "provider": provider,
        "text": text,
        "created": float(created),
    }
    if worktree is not None:
        record["worktree"] = bool(worktree)
    if model:
        record["model"] = model
    if layout:
        record["layout"] = layout
    return record


def valid_draft(record: object) -> dict | None:
    """The trustworthy copy of a draft record read back off disk, or None.

    Persisted state is untrusted input (state.py's rule): a record missing
    its directory, or with the wrong shape in any slot, is dropped rather
    than half-restored. Optional slots are copied only when well-formed.
    """
    if not isinstance(record, dict):
        return None
    cwd = record.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    text = record.get("text")
    provider = record.get("provider")
    created = record.get("created")
    clean = {
        "cwd": cwd,
        "provider": provider if isinstance(provider, str) and provider else "claude",
        "text": text if isinstance(text, str) else "",
        "created": float(created) if isinstance(created, (int, float)) else 0.0,
    }
    worktree = record.get("worktree")
    if isinstance(worktree, bool):
        clean["worktree"] = worktree
    model = record.get("model")
    if isinstance(model, str) and model.strip():
        clean["model"] = model.strip()
    layout = record.get("layout")
    if isinstance(layout, dict) and layout:
        clean["layout"] = layout
    return clean


def draft_label(text: str, fallback: str) -> str:
    """What a sidebar row calls a draft: its first line with something on
    it, whitespace collapsed and capped, or *fallback* for a draft with no
    text (one kept for the terminal in its dock)."""
    for line in text.splitlines():
        words = re.sub(r"\s+", " ", line).strip()
        if words:
            if len(words) > _LABEL_CHARS:
                return words[: _LABEL_CHARS - 1].rstrip() + "…"
            return words
    return fallback


def effective_worktree(choice: bool | None, project_default: bool, is_git: bool) -> bool:
    """Whether a draft launches with the worktree flag: the checkbox as the
    user left it, else the project's default -- and never outside a git
    checkout, where the flag has no meaning (the window's rule, see
    MainWindow._worktree_for_new_session)."""
    if not is_git:
        return False
    return project_default if choice is None else bool(choice)
