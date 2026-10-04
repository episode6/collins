"""Tests for collins.api.protocol: the service/client API's message table,
validation, framing, binary header and version window."""

import ast
import copy
import subprocess
import sys
from pathlib import Path

import pytest

from collins.api import protocol as p

# ---- pinned numbers ------------------------------------------------------------


def test_protocol_version_is_pinned():
    """Bumping either is a deliberate act (spec §3.2, D13): a change no
    capability can express. Update this test in the same commit, with the
    reason."""
    assert p.PROTOCOL == 1
    assert p.MIN_PROTOCOL == 1


def test_protocol_window_is_at_most_two_wide():
    """A client speaks the service's protocol or the one before it."""
    assert p.PROTOCOL - 1 <= p.MIN_PROTOCOL <= p.PROTOCOL


def test_transport_numbers_are_the_specs():
    assert p.MAX_FRAME == 1024 * 1024
    assert p.MAX_INCOMING == 16 * 1024 * 1024
    assert p.QUEUE_BYTES == 4 * 1024 * 1024
    assert p.KEEPALIVE_INTERVAL_S == 10
    assert p.KEEPALIVE_PONG_TIMEOUT_S == 10
    assert p.HEADER_SIZE == 16
    assert p.MAX_PAYLOAD == p.MAX_FRAME - 16
    assert p.LOCAL_PROOF_BYTES == 32


def test_capabilities_and_error_codes_are_closed():
    assert p.CAPABILITIES == {"local"}
    assert p.ERRORS == {
        "unknown",
        "invalid",
        "direction",
        "protocol",
        "sequence",
        "gone",
        "refused",
        "failed",
    }


def test_seen_cap_mirrors_the_history_cap():
    from collins import notifycenter

    assert p.SEEN_MAX == notifycenter.ROW_CAP


def test_mark_caps_mirror_the_stores_caps():
    from collins import diffnotes

    assert p.NOTES_MAX == diffnotes.MAX_NOTES
    assert p.HIGHLIGHTS_MAX == diffnotes.MAX_HIGHLIGHTS


def test_post_kinds_are_notifications_but_the_finished_run():
    from collins import notifycenter

    assert p.POST_KINDS == notifycenter.KINDS - {notifycenter.KIND_FINISHED}


# ---- the table -------------------------------------------------------------------

PHASE_ONE_TYPES = [
    "hello",
    "local",
    "subscribe",
    "attach",
    "detach",
    "resize",
    "focus",
    "theme",
    "spawn",
    # PR-1.11: the panel history key moves into the service.
    "panel.key",
    "prompt",
    "switch",
    "mention",
    "cut",
    "clear",
    "paint",
    "close",
    "state.set",
    "state.get",
    "item",
    # The store's (PR-1.10, spec §3.15): the projection, the archive edge,
    # every mutation the sidebar, the window and Preferences make, and the
    # CLI's folder trust.
    "rows",
    "put-away",
    "store.lookup",
    "store.page-archived",
    "store.refresh",
    "store.show-archived",
    "store.rename",
    "store.regenerate-name",
    "store.favorite",
    "store.archive",
    "store.archive-project",
    "store.trash",
    "store.delete",
    "store.forward",
    "store.add-project",
    "store.keep-projects",
    "store.forget-project",
    "store.move-project",
    "store.flags",
    "trust.check",
    "trust.grant",
    "pty",
    "pty-exited",
    "pr",
    # PR-1.11: the PR hub's status fan-out, its writes and every gh call;
    # the notification history's writes; the job shape; token use; the
    # diff's marks.
    "pr-status",
    "pr.set",
    "pr.fetch",
    "pr.sweep",
    "pr.detail",
    "pr.threads",
    "pr.blob",
    "pr.action",
    "pr.comment",
    "pr.review",
    "pr.thread",
    "notify",
    "notify.post",
    "notify.remove",
    "notify.clear",
    "notify.green",
    "notify.rekey",
    "seen",
    "job.start",
    "job.cancel",
    "job",
    "usage.get",
    "models.get",
    "models.defaults",
    "icon.save",
    "tool",
    "tool-reply",
    "diff.notes",
    "diff.set-notes",
    "sandbox.plan",
    "sandbox.grants",
    "sandbox.allow",
    "sandbox.revoke",
    "sandbox.tools",
    "sandbox.restart",
    "sandbox",
    "service.restart",
    "service.status",
]


def test_table_is_exactly_the_phase_one_list():
    """The spec's list for Phase 1 (PR-1.3), and nothing else: a type added
    later lands behind a capability, and lands here on purpose."""
    assert list(p.type_names()) == PHASE_ONE_TYPES


def _forms():
    for entry in p.TYPES.values():
        if entry.request is not None:
            yield entry.name, p.REQUEST, entry.request
        if entry.event is not None:
            yield entry.name, p.EVENT, entry.event


FORMS = [(name, kind) for name, kind, _shape in _forms()]


def _walk(fields, prefix=""):
    for name, spec in fields.items():
        yield prefix + name, spec
        if spec.kind == p.K_OBJ:
            yield from _walk(spec.fields, f"{prefix}{name}.")
        if spec.item is not None and spec.item.kind == p.K_OBJ:
            yield from _walk(spec.item.fields, f"{prefix}{name}[].")


def test_table_shapes_are_sound():
    kinds = {
        p.K_STR,
        p.K_INT,
        p.K_NUM,
        p.K_BOOL,
        p.K_LIST,
        p.K_OBJ,
        p.K_MAP,
        p.K_JSON,
        p.K_JSON_OBJECT,
        p.K_PATH,
        p.K_URL,
        p.K_SCALAR,
    }
    for entry in p.TYPES.values():
        assert entry.summary
        assert entry.request is not None or entry.event is not None
        if entry.request is not None:
            assert entry.request.sender == p.CLIENT  # requests only ever go client -> service
            assert entry.request.reply is not None
        if entry.event is not None:
            assert entry.event.sender in p.PEERS
            assert entry.event.reply is None
        for _name, _kind, shape in [f for f in _forms() if f[0] == entry.name]:
            assert not set(shape.fields) & p.ENVELOPE, entry.name
            assert set(shape.one_of) <= set(shape.fields)
            for _path, spec in [*_walk(shape.fields), *_walk(shape.reply or {})]:
                assert spec.kind in kinds
                if spec.kind in (p.K_STR,):
                    assert spec.high is not None, (entry.name, _path)  # every string bounded
                if spec.kind in (p.K_LIST, p.K_MAP):
                    assert spec.high is not None and spec.item is not None
                if spec.kind == p.K_INT:
                    assert spec.low is not None and spec.high is not None


# ---- samples: one valid message per form, every field filled -----------------------

ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
BOX = "0123456789abcdef0123456789abcdef"
TERM = {"vte": 8400, "fg": "#c0c0c0", "bg": "#000000", "scheme": "dark"}
DELIVERY = {
    "path": "/home/u/data",
    "status": "pending",
    "inside": "/home/u/data",
    "linked": True,
    "reason": "bindfs not installed",
}
PR_RECORD = {"url": "https://github.com/o/r/pull/1", "number": 1, "title": "Fix it"}
MARKS = {
    "handle": "s-4",
    "session": ID,
    "notes": [{"id": "n1", "path": "a.py", "side": "new", "line": 3, "summary": "Why"}],
    "highlights": [{"id": "h1", "path": "a.py", "side": "new", "line": 3, "start": 0, "end": 4}],
}

SAMPLES = {
    ("hello", p.REQUEST): {
        "protocol": 1,
        "min_protocol": 1,
        "version": "0.2.0",
        "client_id": ID,
        "device": "laptop",
        "locale": "de_DE.UTF-8",
        "term": TERM,
    },
    ("local", p.REQUEST): {"proof": "ab" * 32},
    ("subscribe", p.REQUEST): {},
    ("attach", p.REQUEST): {"pty": 7, "cols": 120, "rows": 40},
    ("detach", p.REQUEST): {"pty": 7},
    ("resize", p.EVENT): {"pty": 7, "cols": 100, "rows": 30},
    ("focus", p.EVENT): {"pty": 7, "focused": True},
    ("theme", p.EVENT): {"term": TERM},
    ("spawn", p.REQUEST): {
        "kind": "agent",
        "cwd": "/home/u/project",
        "session": ID,
        "prompt": "Fix the build\nplease",
        "model": "claude-opus-5-5",
        "effort": "high",
        "permission_mode": "acceptEdits",
        "add_dirs": ["/home/u/other"],
        "worktree": True,
        "worktree_name": "feature-x",
        "sandbox": True,
        "sandbox_box": BOX,
        "cols": 120,
        "rows": 40,
        "history": ID,
        "ordinal": 2,
    },
    ("panel.key", p.REQUEST): {"pty": 7, "history": ID},
    ("prompt", p.REQUEST): {"pty": 7, "text": "hello\nworld"},
    ("switch", p.REQUEST): {"pty": 7, "model": "opus", "effort": "max"},
    ("mention", p.REQUEST): {"pty": 7, "path": "/home/u/project/a.py", "start_line": 2, "end_line": 4},
    ("cut", p.REQUEST): {"pty": 7},
    ("clear", p.REQUEST): {"pty": 7},
    ("paint", p.REQUEST): {"pty": 7, "text": "\x1b[1;33m[session manager]\x1b[0m hi"},
    ("close", p.REQUEST): {"pty": 7, "mode": "background"},
    ("state.set", p.REQUEST): {"key": "names", "entry": ID, "value": "A title"},
    ("state.set", p.EVENT): {"key": "settings", "entry": "title_model", "value": {"a": [1, 2.5, None]}},
    ("state.get", p.REQUEST): {"key": "favorites", "entry": ID},
    ("item", p.EVENT): {
        "session": ID,
        "removed": False,
        "project": "/home/u/project",
        "cwd": "/home/u/project",
        "display_name": "Fix the build",
        "cli_title": "Build fix",
        "subtitle": "2 minutes ago",
        "preview": "Fix the build please",
        "provider": "claude",
        "favorite": True,
        "status": "open",
        "state": "waiting",
        "busy": True,
        "unread": False,
        "syncing": False,
        "backgrounding": False,
        "can_background": True,
        "mtime": 1790000000.5,
        "created": 1790000000,
        "size": 123456,
        "path": "/home/u/.claude/projects/-home-u-project/" + ID + ".jsonl",
        "forward": "moved",
    },
    ("rows", p.EVENT): {
        "rows": [ID],
        "groups": [{"kind": "proj", "name": "project", "label": "project", "count": 1}],
        "empty": [{"kind": "proj", "name": "kept", "label": "kept", "cwd": "/home/u/kept"}],
        "order": ["kept", "project"],
        "order_changed": True,
        "show_archived": False,
        "total": 3,
        "hidden": 2,
        "size": 4096,
        "projects": ["project"],
        "chats": 0,
    },
    ("put-away", p.EVENT): {"session": ID},
    ("store.lookup", p.REQUEST): {"session": ID},
    ("store.page-archived", p.REQUEST): {},
    ("store.refresh", p.REQUEST): {"force": True},
    ("store.show-archived", p.REQUEST): {"show": True},
    ("store.rename", p.REQUEST): {"session": ID, "name": "A better name"},
    ("store.regenerate-name", p.REQUEST): {"session": ID},
    ("store.favorite", p.REQUEST): {"sessions": [ID], "favorite": True},
    ("store.archive", p.REQUEST): {"sessions": [ID], "archived": False},
    ("store.archive-project", p.REQUEST): {"project": "project", "archived": True},
    ("store.trash", p.REQUEST): {"sessions": [ID]},
    ("store.delete", p.REQUEST): {"session": ID},
    ("store.forward", p.REQUEST): {"session": ID, "to": "1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"},
    ("store.add-project", p.REQUEST): {"cwd": "/home/u/new"},
    ("store.keep-projects", p.REQUEST): {"projects": ["project"]},
    ("store.forget-project", p.REQUEST): {"project": "kept"},
    ("store.move-project", p.REQUEST): {"project": "project", "before": "kept"},
    ("store.flags", p.REQUEST): {
        "session": ID,
        "status": "open",
        "busy": True,
        "unread": False,
        "backgrounding": False,
        "can_background": True,
    },
    ("trust.check", p.REQUEST): {"path": "/home/u/project"},
    ("trust.grant", p.REQUEST): {"path": "/home/u/project", "scope": "launch"},
    ("pty", p.EVENT): {
        "pty": 7,
        "kind": "agent",
        "session": ID,
        "cwd": "/home/u/project",
        "pid": 4242,
        "cols": 120,
        "rows": 40,
        "sandboxed": True,
        "box": BOX,
        "active": False,
        "sized_for": "desktop",
    },
    ("pty-exited", p.EVENT): {"pty": 7, "status": 0},
    ("pr", p.EVENT): {
        "session": ID,
        "prs": [{"url": "https://github.com/o/r/pull/1", "number": 1, "extra": {"x": [1]}}],
        "attached": ["https://github.com/o/r/pull/1"],
    },
    ("pr-status", p.EVENT): {
        "url": "https://github.com/o/r/pull/1",
        "status": {"state": "open", "checks": {"pass": 3}},
    },
    ("pr.set", p.REQUEST): {"session": ID, "prs": [PR_RECORD]},
    ("pr.fetch", p.REQUEST): {"urls": ["https://github.com/o/r/pull/1"], "invalidate": True},
    ("pr.sweep", p.REQUEST): {
        "targets": [{"session": ID, "prs": [PR_RECORD], "cwd": "/home/u/project"}],
    },
    ("pr.detail", p.REQUEST): {"url": "https://github.com/o/r/pull/1"},
    ("pr.threads", p.REQUEST): {"url": "https://github.com/o/r/pull/1"},
    ("pr.blob", p.REQUEST): {"repository": "o/r", "ref": "a" * 40, "path": "docs/shot.png"},
    ("pr.action", p.REQUEST): {"pr": PR_RECORD, "key": "merge"},
    ("pr.comment", p.REQUEST): {"pr": PR_RECORD, "body": "Looks good"},
    ("pr.review", p.REQUEST): {"pr": PR_RECORD, "verdict": "approve", "body": "Ship it"},
    ("pr.thread", p.REQUEST): {
        "pr": PR_RECORD,
        "thread": "PRRT_kwDOabc123=",
        "body": "Done",
        "resolved": True,
    },
    ("notify", p.EVENT): {
        "notification": ID,
        "kind": "message",
        "session": ID,
        "title": "Fix the build",
        "project": "project",
        "msgid": "Rang the bell ×{count}",
        "args": {"count": 3, "who": "agent", "ok": True, "none": None, "ratio": 0.5},
        "when": 1790000000.25,
        "read": False,
        "count": 3,
        "url": "https://github.com/episode6/collins/releases/tag/v0.1.5",
        "removed": False,
    },
    ("notify.post", p.REQUEST): {
        "kind": "update",
        "session": "",
        "title": "Collins 0.1.5",
        "project": "",
        "msgid": "Collins {version} is out",
        "args": {"version": "0.1.5"},
        "read": False,
        "url": "https://github.com/episode6/collins/releases/tag/v0.1.5",
        "key": "update:0.1.5",
    },
    ("notify.remove", p.REQUEST): {"ids": [ID]},
    ("notify.clear", p.REQUEST): {},
    ("notify.green", p.REQUEST): {"session": ID, "on": True, "title": "Fix", "project": "project"},
    ("notify.rekey", p.REQUEST): {"session": "placeholder-3", "to": ID},
    ("seen", p.REQUEST): {"ids": [ID, "green:" + ID], "session": ID, "all": False},
    ("seen", p.EVENT): {"ids": ["update:0.1.5"], "session": ID, "all": True},
    ("job.start", p.REQUEST): {"kind": "clone", "args": {"source": "o/r", "dest": "/home/u/r"}},
    ("job.cancel", p.REQUEST): {"job": "job-3"},
    ("job", p.EVENT): {
        "job": "job-3",
        "kind": "clone",
        "state": "failed",
        "msgid": "The clone failed (exit status {code})",
        "args": {"code": 128},
        "result": {"path": "/home/u/r"},
    },
    ("usage.get", p.REQUEST): {},
    ("models.get", p.REQUEST): {"fetch": True, "refresh": False},
    ("models.defaults", p.REQUEST): {"cwd": "/home/u/project", "model": "claude-opus-5-5"},
    ("icon.save", p.REQUEST): {"cwd": "/home/u/project", "svg": "<svg/>"},
    ("tool", p.EVENT): {
        "call": "call-17",
        "session": ID,
        "handle": "s-4",
        "name": "open_in_editor",
        "arguments": {"path": "/home/u/project/a.py", "line": 3},
    },
    ("tool-reply", p.EVENT): {"call": "call-17", "ok": True, "text": "Opened a.py"},
    ("diff.notes", p.EVENT): MARKS,
    ("diff.set-notes", p.REQUEST): MARKS,
    ("sandbox.plan", p.REQUEST): {"box": BOX, "pty": 7},
    ("sandbox.grants", p.REQUEST): {"box": BOX, "pty": 7},
    ("sandbox.allow", p.REQUEST): {"box": BOX, "pty": 7, "path": "/home/u/data", "scope": "project"},
    ("sandbox.revoke", p.REQUEST): {"box": BOX, "pty": 7, "path": "/home/u/data", "scope": "session"},
    ("sandbox.tools", p.REQUEST): {
        "box": BOX,
        "pty": 7,
        "tools": {"show_image": True},
        "reset": False,
    },
    ("sandbox.restart", p.REQUEST): {"box": BOX, "pty": 7},
    ("sandbox", p.EVENT): {"box": BOX, "pty": 7, "session": ID, "delivery": DELIVERY},
    ("service.restart", p.REQUEST): {"when": "idle"},
    ("service.status", p.REQUEST): {},
}

REPLIES = {
    "hello": {
        "protocol": 1,
        "min_protocol": 1,
        "version": "0.2.0",
        "service_id": ID,
        "host": "box",
        "caps": ["local"],
        "local_proof": {"path": "/run/user/1000/collins/x/local-proof", "length": 32},
    },
    "local": {},
    "subscribe": {"items": 12, "ptys": 2},
    "attach": {"cols": 120, "rows": 40, "active": False, "sized_for": "desktop", "modes": ["?1004h", ">5u"]},
    "detach": {},
    "spawn": {"pty": 8, "cols": 120, "rows": 40},
    "prompt": {},
    "switch": {},
    "mention": {},
    "cut": {"text": "half a thought"},
    "clear": {},
    "paint": {},
    "close": {},
    "state.set": {},
    "state.get": {"value": None},
    "store.lookup": {"found": True},
    "store.page-archived": {"items": 40},
    "store.refresh": {},
    "store.show-archived": {},
    "store.rename": {},
    "store.regenerate-name": {},
    "store.favorite": {},
    "store.archive": {},
    "store.archive-project": {},
    "store.trash": {"errors": {ID: "Permission denied"}},
    "store.delete": {"error": "No such file or directory"},
    "store.forward": {},
    "store.add-project": {},
    "store.keep-projects": {},
    "store.forget-project": {},
    "store.move-project": {},
    "store.flags": {},
    "trust.check": {"trusted": False, "root": "/home/u/project"},
    "trust.grant": {"written": True},
    "panel.key": {},
    "pr.set": {},
    "pr.fetch": {},
    "pr.sweep": {"results": {ID: [PR_RECORD]}},
    "pr.detail": {"detail": {"url": "https://github.com/o/r/pull/1", "files": [{"path": "a.py"}]}},
    "pr.threads": {"threads": [{"id": "PRRT_1", "path": "a.py", "comments": []}]},
    "pr.blob": {"file": "/home/u/.cache/collins/pr-blobs/ab.png", "error": ""},
    "pr.action": {"error": "gh: Pull request is not mergeable"},
    "pr.comment": {"error": ""},
    "pr.review": {"error": ""},
    "pr.thread": {"error": ""},
    "notify.post": {"notification": ID},
    "notify.remove": {"removed": 1},
    "notify.clear": {"removed": 4},
    "notify.green": {"changed": True},
    "notify.rekey": {"moved": 2},
    "seen": {},
    "job.start": {"job": "job-3"},
    "job.cancel": {},
    "usage.get": {"snapshot": {"bars": [{"label": "5h", "used": 0.4}]}, "kind": "", "error": ""},
    "models.get": {
        "models": [{"id": "claude-opus-5-5", "display_name": "Opus 5.5"}],
        "cached": True,
        "fetched_at": 1790000000.0,
        "failed": False,
    },
    "models.defaults": {"model": "claude-opus-5-5", "effort": "high"},
    "icon.save": {"path": "/home/u/project/project-icon.svg"},
    "diff.set-notes": {},
    "sandbox.plan": {"plan": {"version": 2, "inputs": {"workspace": "/home/u/project"}}},
    "sandbox.grants": {
        "grants": [DELIVERY],
        "launched": ["/home/u/old"],
        "defaults": ["/home/u/data"],
        "tools": {"show_image": True, "notify_user": False},
        "available": {"show_image": True, "notify_user": True},
        "overridden": True,
        "stale": True,
        "can_restart": True,
    },
    "sandbox.allow": {},
    "sandbox.revoke": {},
    "sandbox.tools": {"tools": {"show_image": True}},
    "sandbox.restart": {},
    "service.restart": {},
    "service.status": {
        "version": "0.2.0",
        "protocol": 1,
        "ptys": 3,
        "busy": 1,
        "clients": 2,
        "started": 1790000000.0,
    },
}


def _sender(name, kind):
    entry = p.TYPES[name]
    return (entry.request if kind == p.REQUEST else entry.event).sender


def _other(sender):
    return p.SERVICE if sender == p.CLIENT else p.CLIENT


def _frame(name, kind, fields, request_id=5):
    if kind == p.REQUEST:
        return p.request(name, request_id, **fields)
    return p.event(name, **fields)


def _shape(name, kind):
    entry = p.TYPES[name]
    return entry.request if kind == p.REQUEST else entry.event


def test_samples_cover_every_form_and_field():
    assert set(SAMPLES) == set(FORMS)
    for (name, kind), sample in SAMPLES.items():
        assert set(sample) == set(_shape(name, kind).fields), (name, kind)
    requests = {name for name, kind in FORMS if kind == p.REQUEST}
    assert set(REPLIES) == requests
    for name, reply in REPLIES.items():
        assert set(reply) == set(p.TYPES[name].request.reply), name


@pytest.mark.parametrize(("name", "kind"), FORMS, ids=[f"{n}:{k}" for n, k in FORMS])
def test_every_form_round_trips(name, kind):
    sample = SAMPLES[(name, kind)]
    frame = p.decode(p.encode(_frame(name, kind, sample)))
    message = p.validate(frame, _sender(name, kind))
    assert isinstance(message, p.Message), message
    assert message.type == name
    assert message.kind == kind
    assert message.id == (5 if kind == p.REQUEST else None)
    assert message.fields == sample


@pytest.mark.parametrize("name", sorted(REPLIES))
def test_every_reply_round_trips(name):
    frame = p.decode(p.encode(p.reply(9, **REPLIES[name])))
    response = p.validate_response(frame, name)
    assert isinstance(response, p.Response), response
    assert response.ok is True
    assert response.re == 9
    assert response.fields == REPLIES[name]


def test_optional_fields_may_be_left_out():
    message = p.validate(p.request("spawn", 1, kind="shell", cwd="/home/u"), p.CLIENT)
    assert isinstance(message, p.Message)
    assert message.fields == {"kind": "shell", "cwd": "/home/u"}
    assert message.get("prompt") is None
    assert message.get("prompt", "") == ""


def test_nullable_fields_take_null():
    message = p.validate(p.event("pty-exited", pty=3, status=None), p.SERVICE)
    assert isinstance(message, p.Message)
    assert message.fields == {"pty": 3, "status": None}
    message = p.validate(p.request("state.set", 1, key="names", entry=ID, value=None), p.CLIENT)
    assert message.fields["value"] is None
    message = p.validate(p.event("item", session=ID, cwd=None), p.SERVICE)
    assert message.fields["cwd"] is None


# ---- bounds: one refusal per bound, derived from the table --------------------------


def _get(container, path):
    for step in path:
        container = container[step]
    return container


def _with(sample, path, value):
    out = copy.deepcopy(sample)
    _get(out, path[:-1])[path[-1]] = value
    return out


def _without(sample, path):
    out = copy.deepcopy(sample)
    del _get(out, path[:-1])[path[-1]]
    return out


def _label(path):
    out = ""
    for step in path:
        if isinstance(step, int):
            out += f"[{step}]"
        else:
            out += f".{step}" if out else step
    return out


DELETE = object()  # a case's value meaning "leave this field out"

_PATTERN_MISSES = {
    p._ID_RE: "-leading-dash",
    p._KEY_RE: "1key",
    p._ARG_KEY_RE: "1",
    p._CAP_RE: "Local",
    p._CODE_RE: "Bad",
    p._COLOR_RE: "#zzzzzz",
    p._HEX_RE: "abc",
    p._LOCALE_RE: "de DE",
    p._TOOL_RE: "Open",
}


def _deep(depth):
    value = 0
    for _ in range(depth):
        value = [value]
    return value


def _spec_cases(path, spec, sample_value):
    """(path, bad value, msgid) for every bound *spec* has."""
    label = _label(path)
    kind = spec.kind
    if not spec.nullable and kind != p.K_SCALAR:
        yield path, None, "{field} must not be null", label
    if kind == p.K_STR:
        yield path, 5, "{field} must be a string", label
        if spec.high is not None:
            yield path, "a" * (spec.high + 1), "{field} must be at most {max} characters", label
        if spec.low == 1:
            yield path, "", "{field} must not be empty", label
        if spec.choices is not None:
            yield path, "nope", "{field} must be one of: {choices}", label
        if spec.pattern is not None:
            miss = _PATTERN_MISSES.get(spec.pattern, "!")
            yield path, miss, "{field} is not well formed", label
    elif kind == p.K_PATH:
        yield path, 5, "{field} must be a string", label
        yield path, "/" + "a" * p.PATH_MAX, "{field} must be at most {max} characters", label
        yield path, "relative/path", "{field} must be an absolute path", label
        yield path, "/with\0nul", "{field} must be an absolute path", label
    elif kind == p.K_URL:
        yield path, 5, "{field} must be a string", label
        yield path, "https://" + "a" * p.PATH_MAX, "{field} must be at most {max} characters", label
        yield path, "javascript:alert(1)", "{field} must be an http(s) URL", label
        yield path, "https://x/\n", "{field} must be an http(s) URL", label
    elif kind == p.K_INT:
        yield path, "1", "{field} must be an integer", label
        yield path, True, "{field} must be an integer", label
        yield path, 1.0, "{field} must be an integer", label
        yield path, spec.low - 1, "{field} must be between {min} and {max}", label
        yield path, spec.high + 1, "{field} must be between {min} and {max}", label
    elif kind == p.K_NUM:
        yield path, "1", "{field} must be a finite number", label
        yield path, True, "{field} must be a finite number", label
        yield path, float("nan"), "{field} must be a finite number", label
        yield path, float("inf"), "{field} must be a finite number", label
    elif kind == p.K_BOOL:
        yield path, 1, "{field} must be true or false", label
    elif kind == p.K_LIST:
        yield path, {}, "{field} must be a list", label
        yield path, [sample_value[0]] * (spec.high + 1), "{field} must hold at most {max} items", label
        if spec.low:
            yield path, [], "{field} must hold at least {min} items", label
        yield from _spec_cases((*path, 0), spec.item, sample_value[0])
    elif kind == p.K_MAP:
        yield path, [], "{field} must be an object", label
        first = next(iter(sample_value.values()))
        many = {f"k{index}": first for index in range(spec.high + 1)}
        yield path, many, "{field} must hold at most {max} items", label
        if spec.low:
            yield path, {}, "{field} must hold at least {min} items", label
        if spec.key is not None:
            yield path, {"Not A Key": first}, "{field} has a key that is not well formed", label
        key = next(iter(sample_value))
        yield from _spec_cases((*path, key), spec.item, first)
    elif kind == p.K_SCALAR:
        yield path, [1], "{field} must be text, a number, true, false or null", label
        yield path, "a" * (p.ARG_TEXT_MAX + 1), "{field} must be at most {max} characters", label
    elif kind == p.K_OBJ:
        yield path, [], "{field} must be an object", label
        for name, inner in spec.fields.items():
            if inner.required:
                yield (*path, name), DELETE, "{field} is missing", f"{label}.{name}"
            yield from _spec_cases((*path, name), inner, sample_value[name])
    elif kind in (p.K_JSON, p.K_JSON_OBJECT):
        if kind == p.K_JSON_OBJECT:
            yield path, [], "{field} must be an object", label
            yield path, {"x": _deep(p.JSON_MAX_DEPTH)}, "{field} is nested too deeply", label
            yield path, {"x": [0] * p.JSON_MAX_NODES}, "{field} holds too much", label
        else:
            yield path, _deep(p.JSON_MAX_DEPTH + 1), "{field} is nested too deeply", label
            yield path, [0] * p.JSON_MAX_NODES, "{field} holds too much", label
        yield path, {"x": float("nan")}, "{field} must be a finite number", label
        yield path, {"x": "a" * (p.TEXT_MAX + 1)}, "{field} must be at most {max} characters", label
        yield path, {"x": {1, 2}}, "{field} must be a JSON value", label
        yield path, {"x": {3: 1}}, "{field} must have text keys", label


def _bound_cases():
    for name, kind in FORMS:
        shape = _shape(name, kind)
        sample = SAMPLES[(name, kind)]
        for field_name, spec in shape.fields.items():
            if spec.required:
                yield name, kind, (field_name,), DELETE, "{field} is missing", field_name
            yield from (
                (name, kind, *case) for case in _spec_cases((field_name,), spec, sample[field_name])
            )


BOUND_CASES = list(_bound_cases())


def _case_id(case):
    name, kind, _path, value, msgid, label = case
    shown = "<missing>" if value is DELETE else repr(value)[:12]
    return f"{name}:{kind}:{label}:{msgid}:{shown}"


@pytest.mark.parametrize("case", BOUND_CASES, ids=[_case_id(c) for c in BOUND_CASES])
def test_every_bound_refuses(case):
    name, kind, path, value, msgid, label = case
    sample = SAMPLES[(name, kind)]
    fields = _without(sample, path) if value is DELETE else _with(sample, path, value)
    refusal = p.validate(_frame(name, kind, fields), _sender(name, kind))
    assert isinstance(refusal, p.Refusal), refusal
    assert refusal.error == p.ERROR_INVALID
    assert refusal.msgid == msgid
    assert refusal.args.get("field") == label
    assert refusal.re == (5 if kind == p.REQUEST else None)
    refusal.msgid.format_map(refusal.args)  # every placeholder is filled


def _reply_bound_cases():
    for name, sample in REPLIES.items():
        for field_name, spec in p.TYPES[name].request.reply.items():
            if spec.required:
                yield name, "reply", (field_name,), DELETE, "{field} is missing", field_name
            yield from (
                (name, "reply", *case) for case in _spec_cases((field_name,), spec, sample[field_name])
            )


REPLY_BOUND_CASES = list(_reply_bound_cases())


@pytest.mark.parametrize("case", REPLY_BOUND_CASES, ids=[_case_id(c) for c in REPLY_BOUND_CASES])
def test_every_reply_bound_refuses(case):
    name, _kind, path, value, msgid, label = case
    sample = REPLIES[name]
    fields = _without(sample, path) if value is DELETE else _with(sample, path, value)
    refusal = p.validate_response(p.reply(9, **fields), name)
    assert isinstance(refusal, p.Refusal), refusal
    assert refusal.error == p.ERROR_INVALID
    assert refusal.msgid == msgid
    assert refusal.args.get("field") == label
    assert refusal.re is None
    refusal.msgid.format_map(refusal.args)


def test_bound_cases_reach_every_field():
    """Each field of each form has at least one case: the derivation above
    can't silently skip a kind."""
    reached = {(name, kind, label) for name, kind, _path, _value, _msgid, label in BOUND_CASES}
    for name, kind in FORMS:
        for field_name in _shape(name, kind).fields:
            assert (name, kind, field_name) in reached, (name, kind, field_name)
    reached = {(name, label) for name, _kind, _path, _value, _msgid, label in REPLY_BOUND_CASES}
    for name in REPLIES:
        for field_name in p.TYPES[name].request.reply:
            assert (name, field_name) in reached, (name, field_name)


@pytest.mark.parametrize(
    ("name", "fields"),
    [
        ("switch", {"pty": 1}),
        ("seen", {}),
        ("sandbox.tools", {"box": BOX, "pty": 1}),
        ("pr.thread", {"pr": PR_RECORD, "thread": "PRRT_1"}),
        ("store.flags", {"session": ID}),
    ],
)
def test_one_of_refuses_when_none_is_present(name, fields):
    refusal = p.validate(p.request(name, 1, **fields), p.CLIENT)
    assert isinstance(refusal, p.Refusal)
    assert refusal.error == p.ERROR_INVALID
    assert refusal.msgid == "One of {fields} is required"


def test_seen_event_one_of_refuses_too():
    refusal = p.validate(p.event("seen"), p.SERVICE)
    assert isinstance(refusal, p.Refusal)
    assert refusal.msgid == "One of {fields} is required"


def test_lone_surrogate_text_is_refused():
    """json.loads accepts \\ud800; nothing downstream can encode it."""
    frame = p.decode('{"t":"prompt","id":1,"pty":1,"text":"a\\ud800b"}')
    refusal = p.validate(frame, p.CLIENT)
    assert isinstance(refusal, p.Refusal)
    assert refusal.msgid == "{field} is not valid text"


def test_a_map_key_that_is_not_text_is_refused():
    refusal = p.validate(p.request("sandbox.tools", 1, box=BOX, pty=1, tools={1: True}), p.CLIENT)
    assert isinstance(refusal, p.Refusal)
    assert refusal.msgid == "{field} has a key that is not well formed"


# ---- unknown fields, unknown types, the envelope -----------------------------------


def test_unknown_fields_are_dropped_at_every_depth():
    message = p.validate(
        p.request(
            "hello",
            1,
            protocol=1,
            version="0.2.0",
            client_id=ID,
            term={**TERM, "cursor": "#ffffff"},
            future_field={"anything": [1, 2]},
        ),
        p.CLIENT,
    )
    assert isinstance(message, p.Message)
    assert "future_field" not in message.fields
    assert message.fields["term"] == TERM


def test_unknown_fields_in_list_items_are_dropped():
    reply = {**REPLIES["sandbox.grants"], "grants": [{**DELIVERY, "mounted_by": "x"}]}
    response = p.validate_response(p.reply(1, **reply), "sandbox.grants")
    assert response.fields["grants"] == [DELIVERY]


def test_a_pending_delivery_leaves_inside_out():
    """Delivery.inside is "" while a grant is pending; the wire omits it."""
    pending = {"path": "/home/u/data", "status": "pending", "reason": "bindfs not installed"}
    message = p.validate(p.event("sandbox", box=BOX, pty=7, delivery=pending), p.SERVICE)
    assert isinstance(message, p.Message)
    assert message.fields["delivery"] == pending
    refusal = p.validate(
        p.event("sandbox", box=BOX, pty=7, delivery={**pending, "inside": ""}), p.SERVICE
    )
    assert refusal.msgid == "{field} must be an absolute path"


def test_free_json_is_kept_whole():
    """A free JSON value (state, PR records, tool arguments) is bounded, not
    shaped: its receiver re-validates it."""
    record = {"url": "https://github.com/o/r/pull/1", "future": {"deep": [True]}}
    message = p.validate(p.event("pr", session=ID, prs=[record]), p.SERVICE)
    assert message.fields["prs"] == [record]


@pytest.mark.parametrize("sender", [p.CLIENT, p.SERVICE])
def test_an_unknown_request_type_is_refused_with_unknown(sender):
    refusal = p.validate({"t": "editor.open", "id": 12, "path": "/x"}, sender)
    assert refusal == p.Refusal(12, "unknown", "Unknown message type: {type}", {"type": "editor.open"})
    assert refusal.to_message() == {
        "re": 12,
        "ok": False,
        "error": "unknown",
        "msgid": "Unknown message type: {type}",
        "args": {"type": "editor.open"},
    }


def test_an_unknown_event_type_is_refused_with_nobody_to_answer():
    refusal = p.validate({"t": "tree-changed", "cwd": "/x"}, p.SERVICE)
    assert isinstance(refusal, p.Refusal)
    assert refusal.error == p.ERROR_UNKNOWN
    assert refusal.re is None
    assert refusal.to_message() is None


@pytest.mark.parametrize(
    ("message", "msgid"),
    [
        ([], "A message must be a JSON object"),
        ("hello", "A message must be a JSON object"),
        ({"id": 1}, "A message needs a type"),
        ({"t": 5, "id": 1}, "A message needs a type"),
        ({"t": "Hello", "id": 1}, "A message needs a type"),
        ({"t": "x" * (p.TYPE_MAX + 1), "id": 1}, "A message needs a type"),
        ({"re": 1, "ok": True}, "A response needs the request it answers"),
        ({"t": "detach", "id": True, "pty": 1}, "The id must be an integer between 0 and {max}"),
        ({"t": "detach", "id": -1, "pty": 1}, "The id must be an integer between 0 and {max}"),
        ({"t": "detach", "id": 2**53, "pty": 1}, "The id must be an integer between 0 and {max}"),
        ({"t": "detach", "id": 1.0, "pty": 1}, "The id must be an integer between 0 and {max}"),
        ({"t": "detach", "id": None, "pty": 1}, "The id must be an integer between 0 and {max}"),
        ({"t": "detach", "id": "1", "pty": 1}, "The id must be an integer between 0 and {max}"),
    ],
)
def test_a_broken_envelope_is_refused(message, msgid):
    refusal = p.validate(message, p.CLIENT)
    assert isinstance(refusal, p.Refusal)
    assert refusal.error == p.ERROR_INVALID
    assert refusal.msgid == msgid
    refusal.msgid.format_map(refusal.args)


def test_request_ids_span_their_range():
    for request_id in (0, p.REQUEST_ID_MAX):
        message = p.validate(p.request("detach", request_id, pty=1), p.CLIENT)
        assert isinstance(message, p.Message)
        assert message.id == request_id


def test_a_request_without_an_id_is_refused():
    refusal = p.validate(p.event("prompt", pty=1, text="hi"), p.CLIENT)
    assert refusal == p.Refusal(None, "invalid", "{type} is a request and needs an id", {"type": "prompt"})


def test_an_event_with_an_id_is_refused_and_answered():
    refusal = p.validate(p.request("resize", 4, pty=1, cols=80, rows=24), p.CLIENT)
    assert refusal == p.Refusal(4, "invalid", "{type} is an event and takes no id", {"type": "resize"})


def test_validate_needs_a_known_sender():
    with pytest.raises(ValueError):
        p.validate(p.request("detach", 1, pty=1), "phone")


# ---- direction --------------------------------------------------------------------


@pytest.mark.parametrize(("name", "kind"), FORMS, ids=[f"{n}:{k}" for n, k in FORMS])
def test_a_form_from_the_wrong_peer_is_refused(name, kind):
    sender = _sender(name, kind)
    wrong = _other(sender)
    refusal = p.validate(_frame(name, kind, SAMPLES[(name, kind)]), wrong)
    assert isinstance(refusal, p.Refusal)
    assert refusal.error == p.ERROR_DIRECTION
    assert refusal.args == {"type": name, "peer": wrong}
    assert refusal.re == (5 if kind == p.REQUEST else None)


def test_state_set_goes_both_ways_in_its_two_forms():
    sample = SAMPLES[("state.set", p.REQUEST)]
    assert isinstance(p.validate(p.request("state.set", 1, **sample), p.CLIENT), p.Message)
    assert isinstance(p.validate(p.event("state.set", **sample), p.SERVICE), p.Message)
    assert p.validate(p.event("state.set", **sample), p.CLIENT).error == p.ERROR_DIRECTION


# ---- responses ----------------------------------------------------------------------


def test_a_refusal_response_validates():
    frame = p.refuse(3, "refused", "Can't allow {path}: {reason}", {"path": "/x", "reason": "no"})
    response = p.validate_response(p.decode(p.encode(frame)), "sandbox.allow")
    assert response == p.Response(
        3,
        False,
        {},
        error="refused",
        msgid="Can't allow {path}: {reason}",
        args={"path": "/x", "reason": "no"},
    )


def test_a_refusal_with_a_newer_code_is_kept():
    response = p.validate_response(p.refuse(3, "rate-limited", "Slow down"), "prompt")
    assert response.ok is False
    assert response.error == "rate-limited"
    assert response.args == {}


@pytest.mark.parametrize(
    ("frame", "msgid"),
    [
        ({"re": 1}, "{field} must be true or false"),
        ({"re": 1, "ok": 1}, "{field} must be true or false"),
        ({"re": 1, "ok": False, "msgid": "x"}, "{field} is missing"),
        ({"re": 1, "ok": False, "error": "Bad Code", "msgid": "x"}, "{field} is not well formed"),
        ({"re": 1, "ok": False, "error": "refused"}, "{field} is missing"),
        ({"re": 1, "ok": False, "error": "refused", "msgid": ""}, "{field} must not be empty"),
        (
            {"re": 1, "ok": False, "error": "refused", "msgid": "x", "args": {"a": [1]}},
            "{field} must be text, a number, true, false or null",
        ),
        (
            {"re": 1, "ok": False, "error": "refused", "msgid": "x", "args": {f"a{i}": 1 for i in range(33)}},
            "{field} must hold at most {max} items",
        ),
        ({"re": 1, "ok": True, "cols": 80, "rows": 24}, "{field} is missing"),
        ({"re": "1", "ok": True}, "A response needs the request it answers"),
        ({"re": -1, "ok": True}, "A response needs the request it answers"),
        ({"t": "attach", "re": 1, "ok": True}, "A response needs the request it answers"),
        ([], "A response needs the request it answers"),
    ],
)
def test_a_broken_response_is_refused(frame, msgid):
    refusal = p.validate_response(frame, "attach")
    assert isinstance(refusal, p.Refusal)
    assert refusal.re is None
    assert refusal.msgid == msgid


def test_unknown_reply_fields_are_dropped():
    response = p.validate_response(p.reply(1, **REPLIES["cut"], extra=1), "cut")
    assert response.fields == REPLIES["cut"]


def test_validate_response_needs_a_request_type():
    with pytest.raises(ValueError):
        p.validate_response(p.reply(1), "item")
    with pytest.raises(ValueError):
        p.validate_response(p.reply(1), "nonsense")


def test_builders_take_fields_named_like_their_parameters():
    assert p.event("tool", name="open_in_editor")["name"] == "open_in_editor"
    assert p.request("x", 1, name="n", request_id=2) == {"t": "x", "id": 1, "name": "n", "request_id": 2}
    assert p.reply(1, re_id=3) == {"re": 1, "ok": True, "re_id": 3}


def test_response_id():
    assert p.response_id({"re": 4, "ok": True}) == 4
    assert p.response_id({"re": True, "ok": True}) is None
    assert p.response_id({"t": "x", "re": 4}) is None
    assert p.response_id("x") is None


# ---- versions --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("own", "own_min", "peer", "peer_min", "agreed"),
    [
        (1, 1, 1, 1, 1),
        (2, 1, 1, 1, 1),  # an upgraded client keeps talking to the old service
        (1, 1, 2, 1, 1),  # ...and the old service agrees
        (3, 2, 1, 1, None),  # too far apart
        (1, 1, 3, 2, None),
        (2, 2, 2, None, 2),  # a peer naming no minimum speaks only its own
        (2, 1, 1, None, 1),
        (3, 3, 2, None, None),
    ],
)
def test_negotiate(own, own_min, peer, peer_min, agreed):
    assert p.negotiate(own, own_min, peer, peer_min) == agreed
    if peer_min is not None:
        assert p.negotiate(peer, peer_min, own, own_min) == agreed  # both sides agree


def test_negotiate_refuses_an_inverted_window():
    assert p.negotiate(1, 1, 1, 2) is None
    assert p.negotiate(1, 2, 1, 1) is None


# ---- JSON framing --------------------------------------------------------------------------


def test_encode_is_compact_and_keeps_unicode():
    assert p.encode({"t": "prompt", "id": 1, "text": "ü"}) == '{"t":"prompt","id":1,"text":"ü"}'


def test_encode_refuses_an_oversized_frame():
    p.encode({"t": "paint", "id": 1, "pty": 1, "text": "a" * (p.MAX_FRAME - 100)})
    with pytest.raises(ValueError):
        p.encode({"t": "paint", "id": 1, "pty": 1, "text": "a" * p.MAX_FRAME})
    with pytest.raises(ValueError):  # bytes, not characters
        p.encode({"text": "ü" * (p.MAX_FRAME // 2 + 1)})


def test_encode_refuses_what_json_cannot_carry():
    with pytest.raises(ValueError):
        p.encode({"x": float("nan")})
    with pytest.raises(ValueError):
        p.encode({"x": "\ud800"})


def test_decode_takes_text_and_bytes():
    assert p.decode('{"t":"subscribe","id":3}') == {"t": "subscribe", "id": 3}
    assert p.decode(b'{"t":"subscribe","id":3}') == {"t": "subscribe", "id": 3}


@pytest.mark.parametrize(
    "frame",
    [
        "[1]",
        '"x"',
        "{",
        '{"x": NaN}',
        '{"x": Infinity}',
        '{"x": -Infinity}',
        b"\xff\xfe",
        "[" * 100_000 + "]" * 100_000,
    ],
)
def test_decode_refuses_a_broken_frame(frame):
    with pytest.raises(ValueError):
        p.decode(frame)


def test_decode_refuses_an_oversized_frame():
    big = '{"x":"' + "a" * p.MAX_INCOMING + '"}'
    with pytest.raises(ValueError):
        p.decode(big)
    with pytest.raises(ValueError):
        p.decode(big.encode())


# ---- the binary header ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "flags", "stream", "offset"),
    [
        (p.TAG_OUTPUT, p.FLAG_REDRAW, 1, 0),
        (p.TAG_OUTPUT, p.FLAG_REDRAW | p.FLAG_REDRAW_END, 7, 123456789),
        (p.TAG_INPUT, 0, 42, 2**40),
        (p.TAG_BLOB, 0, 0, 0),
        (p.U8_MAX, p.U8_MAX, p.U32_MAX, p.U64_MAX),
    ],
)
def test_header_round_trips(tag, flags, stream, offset):
    data = p.pack_header(tag, flags, stream, offset)
    assert len(data) == 16
    assert p.unpack_header(data) == p.Header(tag, flags, stream, offset, 0)


def test_header_is_big_endian_in_the_specs_order():
    data = p.pack_header(0x01, 0x02, 0x0A0B0C0D, 0x0102030405060708)
    assert data == bytes.fromhex("01 02 0000 0a0b0c0d 0102030405060708")


def test_header_reports_reserved_and_never_refuses_it():
    data = bytes.fromhex("01 00 beef 00000001 0000000000000000")
    assert p.unpack_header(data).reserved == 0xBEEF


@pytest.mark.parametrize("size", [0, 1, 15])
def test_a_short_header_is_refused(size):
    with pytest.raises(ValueError):
        p.unpack_header(bytes(size))
    with pytest.raises(ValueError):
        p.unpack_frame(bytes(size))


@pytest.mark.parametrize(
    ("tag", "flags", "stream", "offset"),
    [
        (p.U8_MAX + 1, 0, 1, 0),
        (-1, 0, 1, 0),
        (1, p.U8_MAX + 1, 1, 0),
        (1, 0, p.U32_MAX + 1, 0),
        (1, 0, -1, 0),
        (1, 0, 1, p.U64_MAX + 1),
        (1, 0, 1, -1),
        (True, 0, 1, 0),
        (1, 0, 1.0, 0),
    ],
)
def test_pack_header_refuses_out_of_range(tag, flags, stream, offset):
    with pytest.raises(ValueError):
        p.pack_header(tag, flags, stream, offset)


def test_frames_carry_their_payload():
    frame = p.pack_frame(p.TAG_OUTPUT, p.FLAG_REDRAW_END, 3, 99, b"\x1b[0mhi")
    header, payload = p.unpack_frame(frame)
    assert header == p.Header(p.TAG_OUTPUT, p.FLAG_REDRAW_END, 3, 99)
    assert payload == b"\x1b[0mhi"
    assert p.unpack_frame(p.pack_header(p.TAG_INPUT, 0, 3, 0)) == (p.Header(p.TAG_INPUT, 0, 3, 0), b"")


def test_pack_frame_refuses_an_oversized_payload():
    assert len(p.pack_frame(p.TAG_BLOB, 0, 1, 0, bytes(p.MAX_PAYLOAD))) == p.MAX_FRAME
    with pytest.raises(ValueError):
        p.pack_frame(p.TAG_BLOB, 0, 1, 0, bytes(p.MAX_PAYLOAD + 1))


def test_unpack_frame_refuses_over_the_incoming_cap():
    with pytest.raises(ValueError):
        p.unpack_frame(bytes(p.MAX_INCOMING + 1))


@pytest.mark.parametrize(
    ("tag", "sender", "error"),
    [
        (p.TAG_OUTPUT, p.SERVICE, None),
        (p.TAG_OUTPUT, p.CLIENT, "direction"),
        (p.TAG_INPUT, p.CLIENT, None),
        (p.TAG_INPUT, p.SERVICE, "direction"),
        (p.TAG_BLOB, p.CLIENT, None),
        (p.TAG_BLOB, p.SERVICE, None),
        (0x04, p.CLIENT, "unknown"),
        (0x00, p.SERVICE, "unknown"),
    ],
)
def test_check_frame_enforces_tag_and_direction(tag, sender, error):
    refusal = p.check_frame(p.Header(tag, 0, 1, 0), sender)
    if error is None:
        assert refusal is None
    else:
        assert refusal.error == error
        assert refusal.re is None
        refusal.msgid.format_map(refusal.args)


def test_check_frame_wants_a_pty_for_terminal_bytes():
    assert p.check_frame(p.Header(p.TAG_OUTPUT, 0, 0, 0), p.SERVICE).error == p.ERROR_INVALID
    assert p.check_frame(p.Header(p.TAG_INPUT, 0, 0, 0), p.CLIENT).error == p.ERROR_INVALID
    assert p.check_frame(p.Header(p.TAG_BLOB, 0, 0, 0), p.CLIENT) is None


# ---- GTK-free, stdlib only ------------------------------------------------------------------------

_ALLOWED_IMPORTS = {"__future__", "json", "math", "re", "struct", "collections.abc", "dataclasses"}


def test_protocol_imports_only_the_standard_library():
    """Spec §4: nothing in collins/api/protocol.py imports GTK; both halves
    of the split import it, so it imports nothing of Collins either."""
    source = Path(p.__file__).read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            imported.add(node.module)
    assert imported <= _ALLOWED_IMPORTS


def test_importing_protocol_loads_no_gi():
    code = (
        "import sys; import collins.api.protocol; "
        "print(any(m == 'gi' or m.startswith('gi.') for m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.stdout.strip() == "False"
