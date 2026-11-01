#!/usr/bin/env python3
"""The e2e checks' stubs, applied inside the service process (D27).

Through Phase 1 a check patched a service-side module in its own process
(`claudemodels.available_models`, `prdetail.fetch`, `prstatus.gh_json`)
and the in-process service saw the patch. The service is its own process
since PR-1.12b, so the patch has to be applied there: `e2e_service.
start_service(stubs=...)` writes the canned data to a JSON file and names
this module in ``COLLINS_E2E_STUBS`` (with the data's path in
``COLLINS_E2E_STUBS_DATA``); `collins.service.main` imports it right
after building the core, and only when ``COLLINS_DEBUG_API=1`` is set,
the same flag that opens the ``debug.*`` requests. A service started by
the app never does either.

The data (every key optional)::

    models      a list of claudemodels.model_records: the catalog, cached
                and fresh alike, never fetched
    pr_detail   a prdetail.detail_record, or null: what prdetail.fetch
                answers for every url (null: the page's banner)
    pr_threads  a list of prdetail.thread_record: prdetail.fetch_threads
    gh_json     a map of "<first two argv words>" -> the JSON prstatus.gh_json
                answers (a missing key answers null)
    trash_aside a directory: the git page's trash (service.files.trash_paths)
                moves files there instead, recording each ask as "trash"
    gh_bytes    a map of a gh api endpoint ("repos/o/r/contents/a.png?ref=<sha>")
                -> a file whose bytes prstatus.gh_bytes answers for it (a
                missing key answers None): a PR file's blob, `kind=pr`
    scan_gate   a path the store's every scan (store.discover_sessions)
                waits for before it reads (30 s at most): a large projects
                directory, whose rows land only once the check says so,
                after a reconnecting window has re-attached its tabs
                (check_service_restart.py, V5-S1)
    caps_drop   a list of family caps: the service serves as a server that
                does not list them would (server-side spec D5). They leave the
                hello; their requests, a `spawn` or `close` of their form, their
                blob kinds and an upload are answered `unknown` (404 for the
                HTTP two) and recorded as ``refused`` with the type, so
                `check_server_families.py` can hold the window to sending none
    legacy_hello  true beside a caps_drop of the nine new family caps: the hello
                of a pre-family Collins service (spec D18), which has no
                `implementation`, and `service.restart` is still served
    refuse_subscribe  how many of the first `subscribe`s to refuse `unknown`: a
                server whose first subscribe fails, which is the Reconnecting
                banner and no failed startup (spec D6)

Every stubbed call is appended as a JSON line to ``<data>.calls.jsonl``
(``{"stub": name, "args": [...]}``), which `e2e_service.stub_calls()`
reads, so a check can count what the service was asked, as its lists
did in one process.
"""

from __future__ import annotations

import json
import os
import threading

_lock = threading.Lock()
DATA_PATH = os.environ.get("COLLINS_E2E_STUBS_DATA", "")
CALLS_PATH = DATA_PATH + ".calls.jsonl"


def _data() -> dict:
    try:
        with open(DATA_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _record(stub: str, args: list) -> None:
    """One line per call: the stub, its args, and the callers above it in
    the service (file:function, innermost last), so a check can say who
    asked for a fetch it did not expect."""
    import traceback

    stack = [
        f"{os.path.basename(f.filename)}:{f.name}"
        for f in traceback.extract_stack(limit=40)[:-2]
        if "collins" in f.filename
    ]
    with _lock, open(CALLS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"stub": stub, "args": args, "stack": stack}) + "\n")


def _refuse_subscribe(count: int) -> None:
    """Refuse the first *count* `subscribe`s, then serve them."""
    from collins.api import protocol, server

    left = {"count": count}
    real_answer = server.Connection._answer

    def answer(self, message):
        if message.type == "subscribe" and left["count"] > 0 and self.client is not None:
            left["count"] -= 1
            _record("refused_subscribe", [])
            return protocol.refuse(message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": "subscribe"})
        return real_answer(self, message)

    server.Connection._answer = answer


def _drop_caps(dropped: frozenset, legacy: bool = False) -> None:
    """Serve as a server that does not list *dropped* would: class-level, ahead
    of the `ApiServer` the service builds right after the stubs load. The hello
    keeps its `implementation` (D18: a service with every family dropped is no
    pre-family service) unless *legacy* says it is one."""
    from collins.api import protocol, server

    if legacy:
        real_hello = server.ApiServer.hello

        def hello(self, connection, message):
            reply = real_hello(self, connection, message)
            reply.pop("implementation", None)
            return reply

        server.ApiServer.hello = hello

    forms = {("spawn", "kind", "shell"): protocol.CAP_SHELLS, ("close", "mode", "background"): protocol.CAP_BACKGROUND}
    blob_kinds = {
        "git": protocol.CAP_GIT,
        "pr": protocol.CAP_PRS,
        "file": protocol.CAP_TOOLS,
        "remote": protocol.CAP_TOOLS,
        "icon": protocol.CAP_FILES,
    }

    def family_of(message) -> str:
        for (name, field, value), family in forms.items():
            if message.type == name and message.get(field) == value:
                return family
        return protocol.TYPES[message.type].family

    real_caps = server.ApiServer._caps
    server.ApiServer._caps = lambda self: [cap for cap in real_caps(self) if cap not in dropped]

    real_answer = server.Connection._answer

    def answer(self, message):
        if family_of(message) in dropped and self.client is not None and not (
            legacy and message.type == "service.restart"
        ):
            _record("refused", [message.type])
            return protocol.refuse(
                message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": message.type}
            )
        return real_answer(self, message)

    server.Connection._answer = answer

    real_blob = server.ApiServer._on_blob

    def on_blob(self, _server, msg, path, query, *rest):
        kind = dict(query or {}).get("kind", "")
        if blob_kinds.get(kind) in dropped:
            _record("refused", [f"blob:{kind}"])
            msg.set_status(404, None)
            return
        real_blob(self, _server, msg, path, query, *rest)

    server.ApiServer._on_blob = on_blob

    if protocol.CAP_TOOLS in dropped:

        def on_upload(self, _server, msg, path, query, *rest):
            _record("refused", ["upload"])
            msg.set_status(404, None)

        server.ApiServer._on_upload = on_upload


def apply() -> None:
    data = _data()
    if "models" in data:
        from collins import claudemodels

        def models():
            return claudemodels.models_from_records(data["models"])

        claudemodels.available_models = models
        claudemodels.refresh_models = models
        claudemodels.cached_models = models
        claudemodels.cache_fetched_at = lambda: 1.0
        claudemodels.cache_failed = lambda: False
    # The data is read again on every call: a check restages a step's
    # detail by rewriting the file (`e2e_service.update_stubs`).
    if "pr_detail" in data or "pr_threads" in data:
        from collins import prdetail

        if "pr_detail" in data:

            def fetch(url, *args, **kwargs):
                _record("pr_detail", [url])
                record = _data().get("pr_detail")
                return prdetail.detail_from_record(record) if record is not None else None

            prdetail.fetch = fetch
        if "pr_threads" in data:

            def fetch_threads(url, *args, **kwargs):
                _record("pr_threads", [url])
                threads = _data().get("pr_threads") or []
                return [t for t in (prdetail.thread_from_record(r) for r in threads) if t is not None]

            prdetail.fetch_threads = fetch_threads
    if data.get("caps_drop"):
        _drop_caps(frozenset(data["caps_drop"]), bool(data.get("legacy_hello")))
    if data.get("refuse_subscribe"):
        _refuse_subscribe(int(data["refuse_subscribe"]))
    if "gh_json" in data:
        from collins import prstatus

        def gh_json(args, cwd=None, timeout=None, **kwargs):
            args = [str(a) for a in args]
            _record("gh_json", args)
            return (_data().get("gh_json") or {}).get(" ".join(args[:2]))

        prstatus.gh_json = gh_json
    if "gh_bytes" in data:
        # A PR file's blob (`GET /api/blob?kind=pr`, PR-2.2): the endpoint
        # gh would be asked for, answered from a file the check wrote.
        from collins import prstatus

        def gh_bytes(args, max_bytes=None, **kwargs):
            args = [str(a) for a in args]
            _record("gh_bytes", args)
            source = (_data().get("gh_bytes") or {}).get(args[1] if len(args) > 1 else "")
            if not source:
                return None
            try:
                with open(source, "rb") as fh:
                    return fh.read()
            except OSError:
                return None

        prstatus.gh_bytes = gh_bytes
    if "trash_aside" in data:
        # The git page's trash is the service's (service.files.trash_paths,
        # through git.plan, PR-2.1). Gio refuses to trash on "system
        # internal" mounts (a tmpfs /tmp, where a check's repository
        # lives), so the mover is stubbed with one that records the ask
        # and moves each file into the `trash_aside` directory, as the
        # trash would. Recorded as ``{"stub": "trash", "args": [root,
        # [paths]]}``.
        from collins import gitops
        from collins.service import files

        def trash_paths(root, paths):
            aside = _data().get("trash_aside") or ""
            listed = [str(p) for p in paths]
            _record("trash", [str(root), listed])
            for path in listed:
                os.rename(os.path.join(str(root), path), os.path.join(aside, os.path.basename(path)))
            return gitops.GitResult(True, "", "")

        files.trash_paths = trash_paths
    if "scan_gate" in data:
        # A slow first scan: the service listens long before its store has
        # projected one (storefeed.subscribe sends no rows until `applied`),
        # as a large projects directory does; here the scan waits for the
        # check's word (the gate file), so the order is the check's to set.
        import time

        from collins import store as store_mod

        real_discover = store_mod.discover_sessions

        def discover_sessions(*args, **kwargs):
            gate = _data().get("scan_gate") or ""
            _record("scan_gate", [gate])
            deadline = time.monotonic() + 30
            while gate and not os.path.exists(gate) and time.monotonic() < deadline:
                time.sleep(0.05)
            return real_discover(*args, **kwargs)

        store_mod.discover_sessions = discover_sessions


apply()
