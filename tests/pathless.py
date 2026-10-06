# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The pathless walker (split-service spec §3.23 "The pathless test",
PR-2.1; finished as the required test in PR-2.8).

Every path in the API is a path on the service's machine, so the client
opens no project file and runs no program of the project's: a GTK module
or a client-side helper that reads the filesystem or spawns a process is
a site this walker finds, and `tests/pathless_allowlist.py` is the list
of the sites that are the device's own (`ui-state.json`, the caches, the
Markdown export's destination, the desktop entry, the app's icons, the
update check, `buildinfo`, the service's process) or a local extra behind
`apilink.is_local()` (`function_asks_local` reads a site's function for
the ask). The allowlist shrank per PR through Phase 2; what
`tests/test_client_is_pathless.py` enforces now is that no site is
outside it, no entry is stale, and each group keeps its pinned size.

**What is walked.** `walk()` reads every module of `collins/` (never
`collins/service/` or `collins/api/`, which are the service's and the
wire's) with `ast` and keeps the GTK modules — those importing a widget
library from `gi.repository` (`GTK_NAMES`) — plus `CLIENT_HELPERS`, the
GTK-free modules the client calls for files and processes (PR-2.8 added
the four the final list names and no GTK import marked: `buildinfo`,
`desktopentry`, `updatecheck`, and `openwith`, the local extras'
launcher). Shared modules the service runs too (`gitops`, `sessions`,
`store`, `chats`, `trust`, ...) are not walked: their reads are the
service's, and the client reaches them behind a transport
(`gitops.set_transport`) or a mirror.

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
`set_contents*`; and the image constructors that read a file
(`Gdk.Texture.new_from_filename`, `GdkPixbuf.Pixbuf.new_from_file*`,
`GdkPixbuf.PixbufAnimation.new_from_file*`, `IMAGE_FILE_CALLS`). A
name bound to a `Path(...)`, a path expression (`root / name`, `.parent`,
`.with_*`) or a `Gio.File.new_for_path(...)` inside a function is a path
from then on (`candidate = Path(root, path); candidate.is_file()` is a
site), so the hint rule is not the only way a receiver is known to be a
path. A site is named ``module:qualname:call`` — the module under
`collins`, the enclosing function or method (`Class.method`, or
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
    "buildinfo",
    "clientsession",
    "connection",
    "desktopentry",
    "editorfiles",
    "gitinfo",
    "gitloads",
    "gitpatch",
    "linkpatterns",
    "openwith",
    "projecticons",
    "remotegit",
    "remoteicons",
    "remoteimages",
    "remoteuploads",
    "transcriptlinks",
    "uistate",
    "updatecheck",
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
# Image constructors that open a file by path (a dotted call's tail).
IMAGE_FILE_CALLS = frozenset(
    {
        "Texture.new_from_filename", "Texture.new_from_file", "Pixbuf.new_from_file",
        "Pixbuf.new_from_file_at_scale", "Pixbuf.new_from_file_at_size", "PixbufAnimation.new_from_file",
        "PixbufAnimation.new_from_file_at_scale",
    }
)
PATH_CONSTRUCTORS = ("Path", "PurePath", "PurePosixPath", "PosixPath")


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


def _is_path_call(node: ast.AST) -> bool:
    """A `Path(...)` / `PurePath` / `PurePosixPath` / `PosixPath` call."""
    if not isinstance(node, ast.Call):
        return False
    name = _dotted(node.func) or ""
    return name.split(".")[-1] in PATH_CONSTRUCTORS


def _is_gio_file_call(node: ast.AST) -> bool:
    """A `Gio.File.new_for_path(...)` call."""
    if not isinstance(node, ast.Call):
        return False
    chain = _dotted(node.func) or ""
    return chain.endswith("Gio.File.new_for_path") or chain.endswith("File.new_for_path")


def _looks_like_path(node: ast.AST, path_names: frozenset[str] = frozenset()) -> bool:
    """Whether a method's receiver is a path: a `Path(...)` / `PurePath`
    / `PurePosixPath` call, a chain ending in `.parent` or `.with_*`, a
    `/` expression whose left side is one, a name bound to one of those
    in the enclosing function (*path_names*), or a name carrying a path
    hint."""
    if _is_path_call(node):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _looks_like_path(node.left, path_names)
    if isinstance(node, ast.Attribute) and (node.attr == "parent" or node.attr.startswith("with_")):
        return True
    dotted = _dotted(node)
    if dotted is None:
        return False
    if dotted in path_names:
        return True
    leaf = dotted.split(".")[-1].lower()
    return any(hint in leaf for hint in PATH_NAME_HINTS)


def _gio_chain(node: ast.Call, gio_names: frozenset[str] = frozenset()) -> str | None:
    """`Gio.File.new_for_path(...).<method>` chained, or `<name>.<method>`
    on a name bound to one (*gio_names*): the method's name."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    receiver = _dotted(func.value)
    if receiver is not None and receiver in gio_names:
        return func.attr
    inner = func.value
    while isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
        if _is_gio_file_call(inner):
            return func.attr
        inner = inner.func.value
    return None


def _site_name(
    node: ast.Call,
    aliases: dict[str, str],
    path_names: frozenset[str] = frozenset(),
    gio_names: frozenset[str] = frozenset(),
) -> str | None:
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
        tail = ".".join(dotted.split(".")[-2:])
        if tail in IMAGE_FILE_CALLS:
            return tail
    if isinstance(func, ast.Attribute):
        gio = _gio_chain(node, gio_names)
        if gio is not None and gio.startswith(GIO_FILE_METHODS):
            return f"Gio.File.new_for_path.{gio}"
        method = func.attr
        if method in PATH_METHODS and _looks_like_path(func.value, path_names):
            return f"Path.{method}"
        if method in PATH_METHODS_IF_PATH and _looks_like_path(func.value, path_names):
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


def _bound_names(node: ast.AST) -> list[str]:
    """The names a function binds to *node*'s value, before the body is
    walked: every `name = <path-like>` / `name: T = <path-like>` in it
    (nested functions included, so a worker closure's binding counts),
    for `_looks_like_path` and `_gio_chain`."""
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Assign):
            targets, value = child.targets, child.value
        elif isinstance(child, ast.AnnAssign) and child.value is not None:
            targets, value = [child.target], child.value
        else:
            continue
        if not _path_valued(value):
            continue
        names.extend(target.id for target in targets if isinstance(target, ast.Name))
    return names


def _path_valued(value: ast.AST) -> bool:
    """Whether an assigned *value* is a path: a `Path(...)`, a `/` off a
    path, a `.parent` or `.with_*`."""
    if _is_path_call(value):
        return True
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Div):
        return _looks_like_path(value.left)
    return isinstance(value, ast.Attribute) and (value.attr == "parent" or value.attr.startswith("with_"))


def _gio_bound_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Assign) and _is_gio_file_call(child.value):
            names.extend(t.id for t in child.targets if isinstance(t, ast.Name))
        elif isinstance(child, ast.AnnAssign) and child.value is not None and _is_gio_file_call(child.value):
            if isinstance(child.target, ast.Name):
                names.append(child.target.id)
    return names


class _Walker(ast.NodeVisitor):
    def __init__(self, module: str, aliases: dict[str, str]) -> None:
        self.module = module
        self.aliases = aliases
        self.stack: list[str] = []
        self.sites: set[str] = set()
        # The path-bound and Gio.File-bound names of the enclosing
        # functions (outermost first; a nested function sees its own too).
        self.path_names: list[frozenset[str]] = [frozenset()]
        self.gio_names: list[frozenset[str]] = [frozenset()]

    def _qualname(self) -> str:
        return ".".join(self.stack) if self.stack else "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.path_names.append(self.path_names[-1] | frozenset(_bound_names(node)))
        self.gio_names.append(self.gio_names[-1] | frozenset(_gio_bound_names(node)))
        self.generic_visit(node)
        self.gio_names.pop()
        self.path_names.pop()
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _site_name(node, self.aliases, self.path_names[-1], self.gio_names[-1])
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


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _function(tree: ast.Module, qualname: str) -> ast.AST | None:
    """The function or method *qualname* names (`Class.method`, `f`, a
    nested `f.inner`), or None."""
    node: ast.AST = tree
    for part in qualname.split("."):
        found = None
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, _SCOPES) and child.name == part:
                found = child
                break
        if found is None:
            return None
        node = found
    return node if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else None


def function_asks_local(site: str, trees: dict[str, ast.Module] | None = None) -> bool:
    """Whether the function a ``module:qualname:call`` *site* sits in asks
    `apilink.is_local()` (a call whose name ends in ``is_local``): the
    static half of "a local extra is behind `app.local`". A site at module
    level has no function to ask in and is False. *trees* is `modules()`
    when the caller has it already (it parses every walked module)."""
    module, qualname, _call = site.split(":", 2)
    tree = (modules() if trees is None else trees).get(module)
    if tree is None or qualname == "<module>":
        return False
    function = _function(tree, qualname)
    if function is None:
        return False
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = _dotted(node.func) or ""
            if name.split(".")[-1] == "is_local":
                return True
    return False


if __name__ == "__main__":
    for site in sorted(walk()):
        print(site)
