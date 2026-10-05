# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The pathless walker (split-service spec §3.23 "The pathless test",
PR-2.1; finished as the required test in PR-2.8).

Every path in the API is a path on the service's machine, so the client
opens no project file and runs no program of the project's: a GTK module
or a client-side helper that reads the filesystem or spawns a process is
a site this walker finds, and `tests/pathless_allowlist.py` is the list
of the sites that are the device's own (`ui-state.json`, the caches, the
notification sound, the Markdown export, the desktop entry, the app's
icons, the update check, `buildinfo`) or not yet moved. The allowlist
shrinks per PR and never grows (`tests/test_client_is_pathless.py`).

**What is walked.** `walk()` reads every module of `collins/` (never
`collins/service/` or `collins/api/`, which are the service's and the
wire's) with `ast` and keeps the GTK modules — those importing a widget
library from `gi.repository` (`GTK_NAMES`) — plus `CLIENT_HELPERS`, the
GTK-free modules the client calls for files and processes. Shared
modules the service runs too (`gitops`, `sessions`, `store`, `chats`,
`trust`, ...) are not walked: their reads are the service's, and the
client reaches them behind a transport (`gitops.set_transport`) or a
mirror.

**What is a site.** A call to `open`; to `os.path.exists / isfile /
isdir / getsize / getmtime / islink / lexists`; `os.listdir / scandir /
stat / lstat / walk / makedirs / mkdir / remove / unlink / rename /
replace / access / readlink`; `shutil.*`; `subprocess.*`; `os.popen /
system / spawn* / exec*`; a `Path` method call (`read_text`,
`read_bytes`, `write_text`, `write_bytes`, `iterdir`, `is_file`,
`is_dir`, `unlink`, `mkdir`, `rmdir`, `glob`, `rglob`, `touch`,
`open`, and `exists`, `stat`, `rename`, `replace`, `resolve` when the
receiver is a `Path(...)` or `PurePath` expression or a name that says
it is one: `path`, `file`, `dir`, `target`, `folder`); and a
`Gio.File.new_for_path(...)` chained into `load_*`, `replace*`,
`monitor*`, `trash`, `delete`, `query_info*`, `read*`, `append_to*`,
`create*`, `copy`, `move`, `make_directory*`, `enumerate_children*`,
`set_contents*`. A site is named ``module:qualname:call`` — the module
under `collins`, the enclosing function or method (`Class.method`, or
``<module>``) and the dotted call — so a site keeps its name across
line moves and repeats inside one function count once.
"""

from __future__ import annotations

import ast
from pathlib import Path

import collins

ROOT = Path(collins.__file__).resolve().parent

GTK_NAMES = frozenset({"Gtk", "Adw", "Gdk", "Gsk", "Graphene", "Vte", "GtkSource", "Pango", "Spelling"})

# The GTK-free modules the client calls for files and processes: walked
# with the GTK modules. (A module the service also runs is not here.)
CLIENT_HELPERS = (
    "apilink",
    "blobcache",
    "clientsession",
    "connection",
    "editorfiles",
    "gitinfo",
    "gitloads",
    "gitpatch",
    "linkpatterns",
    "projecticons",
    "remotegit",
    "remoteimages",
    "transcriptlinks",
    "uistate",
)

OS_PATH_CALLS = frozenset(
    {"exists", "isfile", "isdir", "getsize", "getmtime", "islink", "lexists", "samefile"}
)
OS_CALLS = frozenset(
    {
        "listdir", "scandir", "stat", "lstat", "walk", "makedirs", "mkdir", "remove", "unlink", "rename",
        "replace", "access", "readlink", "popen", "system", "spawnv", "spawnvp", "spawnl", "spawnlp",
        "execv", "execvp", "execl", "execlp", "fork", "chmod", "utime", "symlink", "link", "rmdir",
        "open", "truncate", "getcwd", "chdir",
    }
)
PATH_METHODS = frozenset(
    {
        "read_text", "read_bytes", "write_text", "write_bytes", "iterdir", "is_file", "is_dir", "unlink",
        "mkdir", "rmdir", "glob", "rglob", "touch", "open", "is_symlink", "lstat", "samefile", "symlink_to",
        "hardlink_to", "chmod", "walk",
    }
)
PATH_METHODS_IF_PATH = frozenset({"exists", "stat", "rename", "replace", "resolve"})
PATH_NAME_HINTS = ("path", "file", "dir", "target", "folder", "root", "dest", "source")
GIO_FILE_METHODS = (
    "load_", "replace", "monitor", "trash", "delete", "query_info", "read", "append_to", "create", "copy",
    "move", "make_directory", "enumerate_children", "set_contents",
)


def _is_gtk_module(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("gi.repository"):
            if any(alias.name in GTK_NAMES for alias in node.names):
                return True
            tail = (node.module or "").split(".")[-1]
            if tail in GTK_NAMES:
                return True
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[-1] in GTK_NAMES for alias in node.names):
                return True
    return False


def modules() -> dict[str, ast.Module]:
    """Module name -> its tree, for every module walked."""
    found: dict[str, ast.Module] = {}
    for file in sorted(ROOT.glob("*.py")):
        name = file.stem
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        if name in CLIENT_HELPERS or _is_gtk_module(tree):
            found[name] = tree
    return found


def _dotted(node: ast.AST) -> str | None:
    """`a.b.c` for a Name / Attribute chain, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _looks_like_path(node: ast.AST) -> bool:
    """Whether a method's receiver is a path: a `Path(...)` / `PurePath`
    / `PurePosixPath` call, a chain ending in `.parent` or `.with_*`, or
    a name carrying a path hint."""
    if isinstance(node, ast.Call):
        name = _dotted(node.func) or ""
        return name.split(".")[-1] in ("Path", "PurePath", "PurePosixPath", "PosixPath")
    if isinstance(node, ast.Attribute) and (node.attr == "parent" or node.attr.startswith("with_")):
        return True
    dotted = _dotted(node)
    if dotted is None:
        return False
    leaf = dotted.split(".")[-1].lower()
    return any(hint in leaf for hint in PATH_NAME_HINTS)


def _gio_chain(node: ast.Call) -> str | None:
    """`Gio.File.new_for_path(...).<method>` chained: the method's name."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    inner = func.value
    while isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
        chain = _dotted(inner.func) or ""
        if chain.endswith("Gio.File.new_for_path") or chain.endswith("File.new_for_path"):
            return func.attr
        inner = inner.func.value
    return None


def _site_name(node: ast.Call, aliases: dict[str, str]) -> str | None:
    """The call's name when it is a filesystem or process site, else None."""
    func = node.func
    dotted = _dotted(func)
    if dotted is not None:
        dotted = aliases.get(dotted, dotted)
        if dotted == "open":
            return "open"
        if dotted.startswith("os.path.") and dotted.split(".")[-1] in OS_PATH_CALLS:
            return dotted
        if dotted.startswith("os.") and dotted.count(".") == 1 and dotted.split(".")[-1] in OS_CALLS:
            return dotted
        if dotted.startswith("shutil.") or dotted.startswith("subprocess."):
            return dotted
        if dotted in ("Popen", "run", "check_output", "check_call") and aliases.get(dotted, "").startswith(
            "subprocess."
        ):
            return aliases[dotted]
    if isinstance(func, ast.Attribute):
        gio = _gio_chain(node)
        if gio is not None and gio.startswith(GIO_FILE_METHODS):
            return f"Gio.File.new_for_path.{gio}"
        method = func.attr
        if method in PATH_METHODS and _looks_like_path(func.value):
            return f"Path.{method}"
        if method in PATH_METHODS and _dotted(func.value) is None and isinstance(func.value, ast.Call):
            return f"Path.{method}" if _looks_like_path(func.value) else None
        if method in PATH_METHODS_IF_PATH and _looks_like_path(func.value):
            return f"Path.{method}"
    return None


def _aliases(tree: ast.Module) -> dict[str, str]:
    """`from os.path import exists` -> {"exists": "os.path.exists"};
    `from shutil import which` -> {"which": "shutil.which"}; `import
    subprocess as sp` -> {"sp.run" won't resolve; keep "subprocess"}."""
    found: dict[str, str] = {}
    sources = ("os", "os.path", "shutil", "subprocess", "io")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in sources:
            for alias in node.names:
                found[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return found


class _Walker(ast.NodeVisitor):
    def __init__(self, module: str, aliases: dict[str, str]) -> None:
        self.module = module
        self.aliases = aliases
        self.stack: list[str] = []
        self.sites: set[str] = set()

    def _qualname(self) -> str:
        return ".".join(self.stack) if self.stack else "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _site_name(node, self.aliases)
        if name is not None:
            self.sites.add(f"{self.module}:{self._qualname()}:{name}")
        self.generic_visit(node)


def sites_of(module: str, tree: ast.Module) -> set[str]:
    walker = _Walker(module, _aliases(tree))
    walker.visit(tree)
    return walker.sites


def walk() -> set[str]:
    """Every site in every walked module."""
    found: set[str] = set()
    for name, tree in modules().items():
        found |= sites_of(name, tree)
    return found


if __name__ == "__main__":
    for site in sorted(walk()):
        print(site)
