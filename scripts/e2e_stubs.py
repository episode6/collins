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
    if "gh_json" in data:
        from collins import prstatus

        def gh_json(args, cwd=None, timeout=None, **kwargs):
            args = [str(a) for a in args]
            _record("gh_json", args)
            return (_data().get("gh_json") or {}).get(" ".join(args[:2]))

        prstatus.gh_json = gh_json


apply()
