# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-09. Full change history: git log for this file.

import json
import sys
import uuid

import pytest

# Namespaces CI does not have. Its test job installs `python3-gi` and nothing
# else (see .github/workflows/ci.yml), which brings GLib/GObject/Gio but none
# of the GTK stack — those need separate gir packages, and constructing their
# widgets would need a display on top.
#
# A dev machine running the app *does* have them, so a test importing a
# GTK-dependent module passes locally and then fails collection on CI with a
# bare "Namespace Gtk not available". Blocking them here means the local run
# reproduces CI instead of disagreeing with it.
_CI_MISSING_NAMESPACES = frozenset({"Adw", "Gdk", "Graphene", "Gsk", "Gtk", "Vte"})


class _BlockNamespacesMissingOnCI:
    """Meta-path finder that refuses the GTK-stack `gi.repository` namespaces.

    Sits ahead of PyGObject's importer so it catches both import styles — a
    `gi.require_version()` call and a bare `from gi.repository import Gtk`
    (which `collins.usagepanel` does), the latter being why patching
    `require_version` alone would not be enough.
    """

    def find_spec(self, fullname, path=None, target=None):
        prefix = "gi.repository."
        if fullname.startswith(prefix):
            namespace = fullname[len(prefix):]
            if namespace in _CI_MISSING_NAMESPACES:
                raise ImportError(
                    f"gi.repository.{namespace} is deliberately unavailable to the test "
                    f"suite: CI installs python3-gi only, so a test that reaches it fails "
                    f"there even when it passes on your machine (see tests/conftest.py).\n"
                    f"Move the logic under test into a module that doesn't need the GTK "
                    f"stack — bgstatus, sessions, state, store and providers are all "
                    f"importable — or, to test widgets for real, add the gir packages and "
                    f"a headless display to .github/workflows/ci.yml first."
                )
        return None


def pytest_configure(config):
    sys.meta_path.insert(0, _BlockNamespacesMissingOnCI())


def make_transcript_lines(cwd: str, user_text: str, model: str = "claude-opus-4-8") -> list[dict]:
    """Minimal but realistic Claude Code transcript entries."""
    session_id = str(uuid.uuid4())
    return [
        {"type": "mode", "sessionId": session_id},
        {"type": "file-history-snapshot"},
        {
            "type": "user",
            "cwd": cwd,
            "sessionId": session_id,
            "timestamp": "2026-06-01T10:00:00.000Z",
            "message": {"role": "user", "content": user_text},
        },
        {
            "type": "assistant",
            "cwd": cwd,
            "sessionId": session_id,
            "timestamp": "2026-06-01T10:00:05.000Z",
            "message": {
                "role": "assistant",
                "model": model,
                "content": [
                    {"type": "text", "text": "Hello!"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 2000,
                },
            },
        },
        {
            "type": "user",
            "cwd": cwd,
            "sessionId": session_id,
            "timestamp": "2026-06-01T10:01:00.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
        },
    ]


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    """A fake ~/.claude/projects with two projects and three sessions."""
    root = tmp_path / "projects"

    def write_session(project: str, cwd: str, user_text: str) -> str:
        project_dir = root / project
        project_dir.mkdir(parents=True, exist_ok=True)
        session_id = str(uuid.uuid4())
        path = project_dir / f"{session_id}.jsonl"
        lines = make_transcript_lines(cwd, user_text)
        path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
        return session_id

    ids = {
        "alpha1": write_session("-home-user-alpha", "/home/user/alpha", "Build the alpha feature"),
        "alpha2": write_session("-home-user-alpha", "/home/user/alpha", "Fix the alpha bug"),
        "beta1": write_session("-home-user-beta", "/home/user/beta", "Write beta docs"),
    }
    # noise that must be ignored
    (root / "-home-user-alpha" / "not-a-session.jsonl").write_text("{}", encoding="utf-8")
    (root / "-home-user-alpha" / f"{uuid.uuid4()}.jsonl").write_text("", encoding="utf-8")

    import collins.providers as providers_mod
    import collins.sessions as sessions_mod

    monkeypatch.setattr(sessions_mod, "CLAUDE_PROJECTS_DIR", root)
    # Force Claude available regardless of PATH.
    monkeypatch.setattr(providers_mod.ClaudeProvider, "available", lambda self: True)
    return root, ids


@pytest.fixture
def app_state(tmp_path, monkeypatch):
    """AppState isolated to a temp config dir."""
    import collins.panelhistory as panelhistory_mod
    import collins.state as state_mod

    config_dir = tmp_path / "config"
    old_dir = tmp_path / "old_config"  # isolated; pre-rebrand location
    older_dir = tmp_path / "older_config"  # isolated; oldest pre-rebrand location
    monkeypatch.setattr(state_mod, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(state_mod, "_OLD_CONFIG_DIRS", [old_dir, older_dir])
    monkeypatch.setattr(state_mod, "_STATE_FILE", config_dir / "state.json")
    monkeypatch.setattr(state_mod, "_LEGACY_NAMES_FILE", older_dir / "names.json")
    # The panel_states migration counts shell history files; keep it off
    # the real user's state dir.
    monkeypatch.setattr(panelhistory_mod, "_HISTORY_DIR", tmp_path / "panel_history")
    return state_mod
