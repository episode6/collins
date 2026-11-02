# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""Level 2, the store: the list a person organises.

A session the sessions checks bound (or one made here) is renamed, starred,
looked up, archived and restored; a notification is posted and seen; folder
trust is asked; the transcript's tail and export are answered.
"""

from __future__ import annotations

import time

from .. import client as apiclient
from ..reporter import CheckFailed
from ..runner import TERM, Run
from .sessions import spawn_session


def a_session(run: Run, client: apiclient.Client, notes: list[str]) -> str:
    """A bound session id: the one the sessions checks left, else a new one."""
    if run.known_session:
        if any(e.type == "item" and e.get("session") == run.known_session for e in client.events):
            return run.known_session
    s = spawn_session(run, client, notes)
    if not client.wait_until(lambda: s.facts().get("takes_prompt") is True, 15.0):
        raise CheckFailed("the fake's box never came up")
    s.prompt("a turn for the store")
    if not client.wait_until(lambda: isinstance(s.facts().get("session"), str), 12.0):
        raise CheckFailed("the session was not bound")
    s.id = s.facts()["session"]
    client.wait_until(lambda: "path" in s.item(), 5.0)
    client.call("close", pty=s.pty, mode="kill")
    client.wait_for(lambda e: e.type == "pty-exited" and e.get("pty") == s.pty, 15.0)
    run.known_session = s.id
    return s.id


def item(client: apiclient.Client, session: str) -> dict:
    return client.item_facts(session)


def check_rename(run: Run, client: apiclient.Client, session: str, notes: list[str]) -> None:
    reply = client.call("store.rename", session=session, name="Kit's name")
    if not reply.ok:
        raise CheckFailed(f"store.rename: {reply.describe()}")
    if not client.wait_until(lambda: item(client, session).get("display_name") == "Kit's name", 5.0):
        raise CheckFailed(
            f"item.display_name is {item(client, session).get('display_name')!r} after the rename"
        )
    reply = client.call("state.get", key="names", entry=session)
    if not reply.ok or reply.get("value") != "Kit's name":
        raise CheckFailed(f"state.get names[{session}]: {reply.describe()}")
    reply = client.call("store.rename", session=session, name="")
    if not reply.ok:
        raise CheckFailed(f"store.rename to '': {reply.describe()}")
    if not client.wait_until(lambda: item(client, session).get("display_name") != "Kit's name", 5.0):
        raise CheckFailed("the manual name was not cleared by an empty rename")


def check_favorite(run: Run, client: apiclient.Client, session: str, notes: list[str]) -> None:
    reply = client.call("store.favorite", sessions=[session], favorite=True)
    if not reply.ok:
        raise CheckFailed(f"store.favorite: {reply.describe()}")
    if not client.wait_until(lambda: item(client, session).get("favorite") is True, 5.0):
        raise CheckFailed("item.favorite did not go true")
    reply = client.call("state.get", key="favorites")
    if not reply.ok or session not in (reply.get("value") or []):
        raise CheckFailed(f"state.get favorites: {reply.describe()}")
    client.call("store.favorite", sessions=[session], favorite=False)
    if not client.wait_until(lambda: item(client, session).get("favorite") is False, 5.0):
        raise CheckFailed("item.favorite did not go false")


def check_lookup(run: Run, client: apiclient.Client, session: str, notes: list[str]) -> None:
    reply = client.call("store.lookup", session=session)
    if not reply.ok or reply.get("found") is not True:
        raise CheckFailed(f"store.lookup of a known session: {reply.describe()}")
    records = reply.get("sessions") or []
    if not any(isinstance(r, dict) and r.get("session") == session for r in records):
        raise CheckFailed("store.lookup's reply carries no record of the session")
    reply = client.call("store.lookup", session="kit-no-such-session")
    if not reply.ok or reply.get("found") is not False:
        raise CheckFailed(f"store.lookup of an unknown session: {reply.describe()}")


def check_archive(run: Run, client: apiclient.Client, session: str, notes: list[str]) -> None:
    since = len(client.events)
    reply = client.call("store.archive", sessions=[session], archived=True)
    if not reply.ok:
        raise CheckFailed(f"store.archive: {reply.describe()}")
    if client.wait_for(lambda e: e.type == "put-away" and e.get("session") == session, 5.0, since) is None:
        raise CheckFailed("no `put-away` for the archived session")
    reply = client.call("state.get", key="archived")
    if not reply.ok or session not in (reply.get("value") or []):
        raise CheckFailed(f"state.get archived: {reply.describe()}")
    # A fresh client's snapshot leaves the archived row out until it asks.
    fresh = run.client("conformance-archive")
    _, snapshot = fresh.subscribe()
    if any(e.type == "item" and e.get("session") == session for e in snapshot):
        raise CheckFailed("an archived session's row was in a fresh snapshot")
    reply = fresh.call("store.page-archived")
    if not reply.ok:
        raise CheckFailed(f"store.page-archived: {reply.describe()}")
    records = reply.get("sessions") or []
    if not any(isinstance(r, dict) and r.get("session") == session for r in records):
        raise CheckFailed("store.page-archived's reply does not carry the archived session")
    if not fresh.wait_until(lambda: "path" in fresh.item_facts(session), 5.0):
        raise CheckFailed("store.page-archived sent no `item` for the archived session")
    fresh.close()
    reply = client.call("store.archive", sessions=[session], archived=False)
    if not reply.ok:
        raise CheckFailed(f"store.archive {{archived: false}}: {reply.describe()}")
    client.pump(1.0)


def check_notify(run: Run, client: apiclient.Client, session: str, notes: list[str]) -> None:
    other = run.client("conformance-notify-other")
    other.subscribe()
    since = len(client.events)
    since_other = len(other.events)
    reply = client.call(
        "notify.post", kind="message", msgid="The kit posted {what}", args={"what": "this"}, title="kit"
    )
    if not reply.ok or not reply.get("notification"):
        raise CheckFailed(f"notify.post: {reply.describe()}")
    ident = reply["notification"]
    for c, start, who in ((client, since, "the poster"), (other, since_other, "another client")):
        event = c.wait_for(lambda e: e.type == "notify" and e.get("notification") == ident, 5.0, start)
        if event is None:
            raise CheckFailed(f"no `notify` event for the posted row reached {who}")
        if event.get("msgid") != "The kit posted {what}" or (event.get("args") or {}).get("what") != "this":
            raise CheckFailed(f"the `notify` event's msgid and args are not the post's: {event.fields}")
    since_other = len(other.events)
    reply = client.call("seen", ids=[ident])
    if not reply.ok:
        raise CheckFailed(f"seen: {reply.describe()}")
    seen = other.wait_for(lambda e: e.type == "seen" and ident in (e.get("ids") or []), 5.0, since_other)
    if seen is None:
        raise CheckFailed("another client heard no `seen` event for the ids")
    reply = client.call("notify.remove", ids=[ident])
    if not reply.ok:
        raise CheckFailed(f"notify.remove: {reply.describe()}")
    other.close()


def check_trust_and_transcript(run: Run, client: apiclient.Client, session: str, notes: list[str]) -> None:
    reply = client.call("trust.check", path=run.cwd)
    if not reply.ok or not isinstance(reply.get("trusted"), bool):
        raise CheckFailed(f"trust.check: {reply.describe()}")
    notes.append(f"trusted: {reply['trusted']}")
    reply = client.call("store.transcript-tail", session=session)
    if not reply.ok or not isinstance(reply.get("links"), list):
        raise CheckFailed(f"store.transcript-tail: {reply.describe()}")
    reply = client.call("store.transcript-export", session=session, timeout=20)
    if not reply.ok or not isinstance(reply.get("text"), str):
        raise CheckFailed(f"store.transcript-export: {reply.describe()}")
    if "turn" not in reply["text"]:
        raise CheckFailed("the Markdown export does not hold the prompt's text")
    reply = client.call("store.refresh")
    if not reply.ok:
        raise CheckFailed(f"store.refresh: {reply.describe()}")
    started = time.monotonic()
    if client.wait_for(lambda e: e.type == "rows", 10.0) is None:
        raise CheckFailed("no `rows` after store.refresh")
    notes.append(f"rows {time.monotonic() - started:.1f} s after the refresh")


def run_level2(run: Run) -> None:
    r = run.reporter
    r.group("level 2")
    import os

    if not os.path.isfile(run.options.fake_claude):
        r.skip("a session to organise", f"no fake claude at {run.options.fake_claude}")
        return
    client = run.client("conformance-store")
    client.subscribe()
    client.send_event("theme", term=TERM)
    session = r.run("a bound session to organise", a_session, run, client)
    if session is None:
        return
    r.run("store.rename round trip", check_rename, run, client, session)
    r.run("store.favorite", check_favorite, run, client, session)
    r.run("store.lookup", check_lookup, run, client, session)
    r.run("store.archive, put-away, store.page-archived", check_archive, run, client, session)
    r.run("notify.post, the notify event, seen", check_notify, run, client, session)
    r.run(
        "trust.check, the transcript's tail and export, store.refresh",
        check_trust_and_transcript,
        run,
        client,
        session,
    )
