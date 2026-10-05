#!/usr/bin/env python3
"""The e2e checks' service: one `collins-service` per check (spec §3.20).

The service is its own process since PR-1.12b, so a check that builds an
`App()` (or drives bare widgets that reach the service) starts a service of
its own, after its scratch tree and every `COLLINS_*` / `XDG_*` override
are in the environment and the `claude` shim is on `PATH`, since the
service spawns the shells that run it and inherits the environment it is
started with:

    import e2e_service
    e2e_service.start_service()          # right before App()

`start_service(env)` spawns ``python3 -m collins.service.main --app-id
<the check's id>`` out of this checkout (``PYTHONPATH`` set to the repo
root, so the service never imports a system-installed `collins`) with the
check's environment plus ``COLLINS_DEBUG_API=1`` (the probe, D27), waits
for the socket to answer, and registers an `atexit` that sends SIGTERM and
waits, so a check's exit ends the sessions it started.

A check that drives widgets with no `App` behind them and reaches the
service through `apilink.current()` (a PR page's gh requests, a job) calls
`harness_link()`: a `SocketLink` connected to a service started here and
installed as the current link.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from collins.api import server as api_server  # noqa: E402

WAIT_S = 20.0
STOP_S = 15.0

_services: list[subprocess.Popen] = []


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(STOP_S)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
            proc.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _stop_all() -> None:
    for proc in list(_services):
        _stop(proc)
    if _stubs_data:
        for path in (_stubs_data, _stubs_data + ".calls.jsonl"):
            try:
                os.unlink(path)
            except OSError:
                pass


atexit.register(_stop_all)


STUBS_MODULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e_stubs.py")
_stubs_data: str | None = None


def start_service(
    env: dict | None = None,
    app_id: str | None = None,
    wait_s: float = WAIT_S,
    stubs: dict | None = None,
) -> subprocess.Popen:
    """Spawn a service for *app_id* (default: ``COLLINS_APP_ID``) with
    *env* (default: this process's environment) and wait for its socket.
    *stubs* is what the service's modules answer in place of the network
    and `gh` (see scripts/e2e_stubs.py: ``models``, ``pr_detail``,
    ``pr_threads``, ``gh_json``), applied inside the service process.
    Returns the `Popen`; raises RuntimeError when it does not come up."""
    global _stubs_data
    env = dict(os.environ if env is None else env)
    app_id = app_id or env.get("COLLINS_APP_ID") or os.environ["COLLINS_APP_ID"]
    env["COLLINS_APP_ID"] = app_id
    env["COLLINS_DEBUG_API"] = "1"
    env["PYTHONPATH"] = REPO + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if stubs is not None:
        import json
        import tempfile

        fd, _stubs_data = tempfile.mkstemp(prefix="collins-e2e-stubs-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(stubs, fh)
        env["COLLINS_E2E_STUBS"] = STUBS_MODULE
        env["COLLINS_E2E_STUBS_DATA"] = _stubs_data
    path = api_server.socket_path(app_id)
    proc = subprocess.Popen(
        [sys.executable, "-m", "collins.service.main", "--app-id", app_id],
        env=env,
        cwd=REPO,
        stdin=subprocess.DEVNULL,
    )
    _services.append(proc)
    deadline = time.monotonic() + wait_s
    while api_server.probe_socket(path) != "live":
        if proc.poll() is not None:
            raise RuntimeError(f"collins-service exited {proc.returncode} before listening on {path}")
        if time.monotonic() >= deadline:
            _stop(proc)
            raise RuntimeError(f"collins-service did not listen on {path} within {wait_s:.0f} s")
        time.sleep(0.1)
    return proc


def stop_service(proc: subprocess.Popen) -> None:
    """SIGTERM the service and wait (a check that wants it gone before it
    ends)."""
    _stop(proc)
    if proc in _services:
        _services.remove(proc)


def harness_link(device: str = "harness", stubs: dict | None = None):
    """Start a service (with *stubs*, see `start_service`) and connect a
    `SocketLink` to it, installed as the current link
    (`apilink.set_current`); for a check with no `App`."""
    from collins import apilink
    from collins.api.client import SocketLink

    app_id = os.environ["COLLINS_APP_ID"]
    start_service(app_id=app_id, stubs=stubs)
    link = SocketLink(api_server.socket_path(app_id), device=device)
    link.connect()
    link.prove_local()
    apilink.set_current(link)
    return link


if __name__ == "__main__":
    proc = start_service()
    print(api_server.socket_path(os.environ["COLLINS_APP_ID"]))
    try:
        proc.wait()
    except KeyboardInterrupt:
        stop_service(proc)


def wait_until(predicate, timeout_s: float = 5.0, step_s: float = 0.02) -> bool:
    """Pump the main loop until *predicate* is true or *timeout_s* passes;
    returns the predicate's last word. What the loopback answered inside
    a call (a write's reply, the service's save, an event's landing) the
    socket answers a few milliseconds later, on the main loop: a check
    that reads the outcome off disk or off a mirror waits for it here
    before asserting what it always asserted."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return bool(predicate())
        while context.pending():
            context.iteration(False)
        time.sleep(step_s)


def update_stubs(**changes) -> None:
    """Rewrite the stubs' data with *changes* merged in (the service's
    stubs read it again on every call)."""
    import json

    if not _stubs_data:
        raise RuntimeError("the service was started without stubs")
    with open(_stubs_data, encoding="utf-8") as fh:
        data = json.load(fh)
    data.update(changes)
    tmp = _stubs_data + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, _stubs_data)


def stub_calls() -> list[dict]:
    """Every call the service's stubs took so far, oldest first:
    ``{"stub": name, "args": [...]}`` each (see scripts/e2e_stubs.py)."""
    import json

    if not _stubs_data:
        return []
    try:
        with open(_stubs_data + ".calls.jsonl", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except OSError:
        return []


def settle(ms: int | None = None) -> None:
    """Pump the main loop for *ms*, then until nothing is pending: long
    enough for a write's reply and the service's save, or an event the
    reply implies, to land over the socket (what the loopback did inside
    the call)."""
    from gi.repository import GLib

    if ms is None:
        ms = int(os.environ.get("COLLINS_E2E_SETTLE_MS") or 100)
    wait_until(lambda: False, timeout_s=ms / 1000)
    context = GLib.MainContext.default()
    for _ in range(100):
        if not context.pending():
            break
        context.iteration(False)
