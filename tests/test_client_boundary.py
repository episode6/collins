# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""What the client no longer does itself (split-service spec §3.22,
PR-1.12d), read off the source with `ast` (the window is a GTK module this
suite cannot import): `window.py` imports nothing of `bgstatus` (the agent
list's poller, the fork matcher), reads no transcript for the background
handoff or the worktree ask, stats no transcript for a moved one, makes no
chat folder; `app.py` runs no archive sweep. A step towards the pathless
client of PR-2.8."""

import ast
from pathlib import Path

import collins

ROOT = Path(collins.__file__).resolve().parent


def _tree(name: str) -> ast.Module:
    return ast.parse((ROOT / name).read_text(encoding="utf-8"))


def _imports(tree: ast.Module) -> set[str]:
    """Every module and every name imported, as `module` and `module.name`."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = (node.module or "").lstrip(".")
            found.add(module)
            for alias in node.names:
                found.add(f"{module}.{alias.name}" if module else alias.name)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _names(tree: ast.Module) -> set[str]:
    """Every identifier and attribute name the module uses or defines."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def test_the_window_imports_nothing_of_bgstatus():
    imports = _imports(_tree("window.py"))
    assert not {i for i in imports if i == "bgstatus" or i.startswith("bgstatus.")}, imports
    # The gate it reads is bgblock's, which holds no poller and no CLI read.
    assert "bgblock.background_blocker" in imports


def test_the_window_reads_and_stats_no_transcript_for_the_services_work():
    names = _names(_tree("window.py"))
    for gone in (
        "match_background_fork",
        "first_message_uuid",
        "removable_worktree",
        "BackgroundStatusPoller",
        "_sync_transcript_paths",
        "_watch_background_fork",
        "_replay_pending_detaches",
        "ensure_chat_dir",
        "trash_expired_archives",
        "set_can_background",
        "set_backgrounding",
    ):
        assert gone not in names, gone


def test_the_app_runs_no_archive_sweep():
    tree = _tree("app.py")
    assert "autodelete" not in _imports(tree) and "_sweep_archived" not in _names(tree)


def test_a_refused_resubscribe_is_lost_whatever_its_code():
    """Spec D6 and ruling 32, on the app the suite cannot import: the handler
    hands every refusal to `lost` with the manager's words, and reads the code
    for nothing else. `test_connection.py` runs that road; the e2e run
    `refused-subscribe` runs it on a real window."""
    function = next(
        node
        for node in ast.walk(_tree("app.py"))
        if isinstance(node, ast.FunctionDef) and node.name == "_on_connected"
    )
    handler = next(
        handler
        for node in ast.walk(function)
        if isinstance(node, ast.Try)
        for handler in node.handlers
        if isinstance(handler.type, ast.Name) and handler.type.id == "RequestRefused"
    )
    conditions = [node.test for node in ast.walk(handler) if isinstance(node, ast.If)]
    assert not [test for test in conditions if "error" in ast.dump(test)], "a code decides whether it is lost"
    lost = [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "lost"
    ]
    assert len(lost) == 1 and "resubscribe_refused_words" in ast.dump(lost[0])
