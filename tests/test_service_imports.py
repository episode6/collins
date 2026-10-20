# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The service imports no GTK (split-service spec §3.1): every module of
collins.service and collins.api, imported in a fresh interpreter, loads none
of gi's widget libraries. GLib, GObject and Gio are what the service runs
on, and are allowed. A subprocess, because this suite's own import blocker
(conftest.py) would turn a GTK import into an ImportError rather than show
which module pulled it in, and because other tests may already have loaded
anything."""

import pkgutil
import subprocess
import sys
from pathlib import Path

import collins.api
import collins.service

FORBIDDEN = ("Gtk", "Adw", "Gdk", "Gsk", "Vte", "GtkSource", "Graphene")

_PROBE = """
import importlib, sys
for name in sys.argv[1:]:
    importlib.import_module(name)
loaded = sorted(m for m in sys.modules if m.startswith("gi.repository."))
print("\\n".join(loaded))
"""


def _modules(package) -> list[str]:
    return [package.__name__] + [
        f"{package.__name__}.{info.name}" for info in pkgutil.iter_modules(package.__path__)
    ]


def test_the_service_and_the_api_load_no_gtk():
    modules = _modules(collins.service) + _modules(collins.api)
    assert "collins.service.core" in modules and "collins.api.server" in modules
    assert "collins.api.client" in modules and "collins.service.main" in modules
    # PR-1.11's: the session tools, the notification history, the jobs and
    # token use are the service's, and as GTK-free as the rest.
    for name in (
        "tools", "notifications", "jobs", "tokenuse", "prfeed", "sandbox", "diffs",
        "hosting", "tracking", "finish",  # PR-1.12a: the session on the service
        "bgagents",  # PR-1.12d: the background agents
    ):
        assert f"collins.service.{name}" in modules, name
    root = Path(collins.__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, *modules],
        capture_output=True,
        text=True,
        cwd=root,
        env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    loaded = {line.removeprefix("gi.repository.") for line in result.stdout.split()}
    assert not loaded & set(FORBIDDEN), sorted(loaded & set(FORBIDDEN))
    # The probe saw something: the store (collins.store, through
    # service.core) runs on Gio's file monitors and GLib's main loop, so a
    # probe that imported nothing at all could not pass this line.
    assert {"GLib", "Gio"} <= loaded
