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

**What is a site.** A call to `open` (also `io.open`, `builtins.open`,
`codecs.open`, `io.FileIO`, `io.open_code`; `posixpath` is read as
`os.path`);
to `os.path.exists / isfile / isdir / getsize / getmtime / islink /
lexists / samefile / ismount / realpath`; `os.listdir / scandir / stat /
lstat / walk / makedirs / mkdir / remove / unlink / rename / replace /
access / readlink / chmod / ...` (`OS_CALLS`) and every `os.spawn*`,
`os.exec*`, `os.posix_spawn*`; anything of `shutil`, `subprocess`, `glob`
or `tempfile`; `GLib.file_*`, `GLib.spawn_*`, `GLib.find_program_in_path`
and `Gio.Subprocess`; a `Path` method call — `is_dir`, `is_file`,
`read_text`, `read_bytes`, `write_bytes`, `iterdir`, `rglob`,
`is_symlink` and the other names only a path has (`PATH_METHODS_ANY`) on
**any** receiver, and `write_text`, `unlink`, `mkdir`, `rmdir`, `glob`,
`touch`, `open`, `exists`, `stat`, `rename`, `replace`, `resolve`, ...
when the receiver is known to be a path: a `Path(...)` or `PurePath`
call, `Path.home()`, a chain through a path's own methods off one
(`Path(p).expanduser().resolve()`), a `/` off one, a `.parent` or
`.with_*`, a name that says it is one (`path`, `file`, `dir`, `target`,
`folder`, `root`, `dest`, `source`), or a name **bound** to one; a
`Gio.File.new_for_path(...)` / `new_for_uri(...)` /
`new_for_commandline_arg(...)`, chained or bound to a name, into
`load_*`, `replace*`, `monitor*`, `trash`, `delete`, `query_*`
(`query_exists` and `query_info` among them), `read*`, `append_to*`,
`create*`, `copy`, `move`, `make_directory*`, `enumerate_children*`,
`set_contents*`, and the method names only a `Gio.File` has
(`load_contents`, `load_bytes`, `query_info`, `query_exists`,
`enumerate_children`, `monitor_file`, `replace_contents`, ...:
`GIO_FILE_METHODS_ANY`) on **any** receiver; a process started through
an object (`spawn_async`, `spawn_sync`, `spawn_with_fds_async`,
`spawnv` on any receiver: Vte's terminal and pty,
`Gio.SubprocessLauncher`), `Gio.Subprocess(...)`,
`Gtk.show_uri`, anything of `webbrowser`; the launchers (PR-3.0:
`Gtk.FileLauncher.new`, `Gtk.UriLauncher.new`,
`Gio.AppInfo.launch_default_for_uri`, `open_containing_folder` and
`launch_uris*` on any receiver, `launch` on an app's info:
`LAUNCHER_CALLS`, `LAUNCHER_METHODS_ANY`); and the constructors and
setters that open or write a file by path or by `Gio.File`
(`Gdk.Texture.new_from_filename`, `GdkPixbuf.Pixbuf.new_from_file*`,
`Gtk.Picture.new_for_filename` / `new_for_file`, `.set_filename(...)`,
`.set_file(...)`, `.load_from_file(...)`, `.savev(...)`,
`GtkSource.FileLoader.new`, ...: `IMAGE_FILE_CALLS`,
`WIDGET_FILE_METHODS`), the keyword-property form of those constructors
(`Gtk.Picture(file=g)`, `Gtk.Image(filename=p)`,
`GtkSource.FileLoader(buffer=b, location=f)`: `KEYWORD_FILE_CLASSES`)
and `set_property("file" | "filename", ...)` on any receiver; a
`Gio.File` renamed (`set_display_name`), re-moded (`set_attribute*`)
or made (`Gio.File.new_tmp`).

**What is no site, though it reads like one.** The lexical functions of
the whole-module words (`glob.escape`, `tempfile.gettempdir`,
`subprocess.list2cmdline`, `GLib.spawn_check_wait_status`, ...:
`NOT_SITES`); `getattr` on a walked module with a constant name that is
none of the functions above (`getattr(os, "O_NOFOLLOW", 0)`); a method
called with more arguments than a path's takes (`stream.read_bytes(4096,
None)`, `text.replace("a", "b")`); a `Gio.File` made from a constant
`resource://` address. What the vocabulary still flags on an object that
is no path at all (a service fact with an `is_dir()` of its own, a
`Gtk.Root` named `root`) is answered in `pathless_allowlist.NOT_A_SITE`,
one reviewed line each.

**How a name is followed.** A module's import aliases are resolved first
(`import shutil as sh`, `import os.path as osp`, `from os import path as
P`, `from os.path import isdir as there`, `from gi.repository import Gio
as G`: `_aliases`). A name is bound to a path by `name = <path>`, a
tuple of them, `(name := <path>)`, a parameter annotated `Path` or
defaulting to one, a loop or comprehension over `map(Path, ...)` or
`<path>.iterdir()` / `.glob()`, or `name = <a bound name>`, anywhere in
the enclosing function or at the module's top level (flow is not
followed). And one of the functions above **named without being called**
is a site where it is named: handed on (`map(os.path.isdir, ps)`,
`run=subprocess.run` as a default) or bound (`fn = os.path.isdir`), and
so is `getattr(os.path, ...)`; a path's method handed on or bound
uncalled the same (`filter(Path.is_dir, ps)`, `key=Path.stat`,
`fn = Path.read_text`).

**What is not seen** is written down, form by form, beside `UNRULED` in
`tests/pathless_allowlist.py` (the D50 record): a helper's read (a site
is named by the function the read is written in, never by its callers),
a receiver with no hint for the method names other objects use too, and
the rest. (The launchers are seen since PR-3.0.) `tests/test_client_is_pathless.py` holds a
table of spellings the walker must catch and of the ones it is known to
miss, so a change to either is a change to that table.

A site is named ``module:qualname:call`` — the module under `collins`,
the enclosing function or method (`Class.method`, or ``<module>``) and
the dotted call with its aliases resolved — so a site keeps its name
across line moves and repeats inside one function count once.
"""

from __future__ import annotations

import ast
import functools
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
    {
        "exists", "isfile", "isdir", "getsize", "getmtime", "islink", "lexists", "samefile", "ismount",
        "getatime", "getctime", "realpath",
    }
)
OS_CALLS = frozenset(
    {
        "listdir", "scandir", "stat", "lstat", "walk", "makedirs", "mkdir", "remove", "unlink", "rename",
        "replace", "access", "readlink", "popen", "system", "fork", "forkpty", "chmod", "utime", "symlink",
        "link", "rmdir", "open", "truncate", "getcwd", "chdir", "removedirs", "renames", "startfile",
        "fwalk", "statvfs", "chown", "lchown", "listxattr", "getxattr", "setxattr", "removexattr", "mkfifo",
        "mknod",
    }
)
# `os.spawn*`, `os.exec*`, `os.posix_spawn*`: every spelling of them.
OS_CALL_PREFIXES = ("spawn", "exec", "posix_spawn")
# Modules whose every call is a site (a module's name after its aliases
# are resolved: `import shutil as sh; sh.which(...)` is `shutil.which`).
SITE_MODULES = ("shutil", "subprocess", "glob", "tempfile", "webbrowser")
# ...but for these, which touch no disk and start nothing: a pattern's or
# an argv's text, a value read off the environment, a status decoded, an
# exception or a result constructed.
NOT_SITES = frozenset(
    {
        "glob.escape", "glob.has_magic", "glob.translate", "shutil.get_terminal_size", "shutil.Error",
        "subprocess.list2cmdline", "subprocess.CompletedProcess", "subprocess.CalledProcessError",
        "subprocess.TimeoutExpired", "subprocess.SubprocessError", "tempfile.gettempdir",
        "tempfile.gettempdirb", "tempfile.gettempprefix", "tempfile.gettempprefixb",
        "GLib.spawn_check_wait_status", "GLib.spawn_check_exit_status", "GLib.spawn_close_pid",
        "GLib.spawn_error_quark", "GLib.spawn_exit_error_quark", "GLib.file_error_quark",
        "GLib.file_error_from_errno",
    }
)
# `open` however it is reached.
OPEN_CALLS = frozenset({"open", "io.open", "builtins.open", "codecs.open", "io.FileIO", "io.open_code"})
PATH_METHODS = frozenset(
    {
        "read_text", "read_bytes", "write_text", "write_bytes", "iterdir", "is_file", "is_dir", "unlink",
        "mkdir", "rmdir", "glob", "rglob", "touch", "open", "is_symlink", "lstat", "samefile", "symlink_to",
        "hardlink_to", "chmod", "walk", "is_mount", "readlink", "owner", "group", "lchmod", "is_fifo",
        "is_socket", "is_block_device", "is_char_device", "is_junction",
    }
)
PATH_METHODS_IF_PATH = frozenset({"exists", "stat", "rename", "replace", "resolve"})
# Method names only a path has: a site on any receiver at all, hinted or
# not (`p.is_dir()` on a bare parameter, `self._thing.is_dir()`,
# `somewhere(p).is_dir()`). The rest of PATH_METHODS are words other
# objects use too (`write_text` is `Session.write_text`, the pty's;
# `unlink`, `open`, `glob`, `touch`, `walk`, ...) and need a receiver the
# walker can tell is a path.
PATH_METHODS_ANY = frozenset(
    {
        "read_text", "read_bytes", "write_bytes", "iterdir", "is_file", "is_dir", "rglob", "is_symlink",
        "symlink_to", "hardlink_to", "is_mount", "readlink", "is_fifo", "is_socket", "is_block_device",
        "is_char_device", "is_junction",
    }
)
# ...unless the call's shape says it is another object's method of the
# same name: `Path.read_bytes()` and `read_text()` take no positional
# argument (a `Gio.InputStream.read_bytes(count, cancellable)` does), and
# `Path.replace(target)` / `rename(target)` take one (`str.replace(old,
# new)` takes two).
PATH_METHODS_MAX_ARGS = {"read_bytes": 0, "read_text": 2, "replace": 1, "rename": 1}
# Methods of a path that answer another path: a chain through them is
# still a path (`Path(p).expanduser().resolve().is_dir()`).
PATH_RETURNING = frozenset(
    {
        "expanduser", "resolve", "absolute", "joinpath", "with_name", "with_suffix", "with_stem",
        "with_segments", "relative_to", "readlink",
    }
)
# Methods of a path that answer paths, for a loop's or a comprehension's
# target (`for child in folder.iterdir(): child.exists()`).
PATH_ITERATING = frozenset({"iterdir", "glob", "rglob"})
PATH_NAME_HINTS = ("path", "file", "dir", "target", "folder", "root", "dest", "source")
GIO_FILE_METHODS = (
    "load_", "replace", "monitor", "trash", "delete", "query_", "read", "append_to", "create", "copy",
    "move", "make_directory", "enumerate_children", "set_contents", "measure_disk_usage", "open_readwrite",
    "make_symbolic_link", "set_display_name", "set_attribute",
)
# A `Gio.File` made and written in one call (a tuple comes back, so no
# chain follows it): a site by its own name.
GIO_FILE_CREATING = frozenset({"File.new_tmp", "Gio.file_new_tmp"})
# `Gio.File` method names nothing else has: a site on any receiver at all
# (a `Gio.File` the function did not make: a parameter, an attribute, a
# dropped file out of a `Gdk.FileList`). `trash`, `delete`, `copy`,
# `move`, `read`, `replace`, `create` are words other objects use and
# need a receiver the walker saw made.
GIO_FILE_METHODS_ANY = frozenset(
    {
        "load_contents", "load_contents_async", "load_bytes", "load_bytes_async",
        "load_partial_contents_async",
        "query_info", "query_info_async", "query_exists", "query_file_type", "query_filesystem_info",
        "enumerate_children", "enumerate_children_async", "monitor_file", "monitor_directory",
        "replace_contents", "replace_contents_async", "replace_contents_bytes_async", "make_directory",
        "make_directory_with_parents", "make_directory_async", "make_symbolic_link", "measure_disk_usage",
        "open_readwrite", "append_to", "set_attributes_from_info",
    }
)
# The `Gio.File` constructors a chain or a bound name starts from
# (`Gio.File.<name>`, or GIO's function form `Gio.file_<name>`).
GIO_FILE_CONSTRUCTORS = (
    "new_for_path", "new_for_uri", "new_for_commandline_arg", "new_for_commandline_arg_and_cwd",
    "parse_name", "new_build_filename", "new_build_filenamev", "new_tmp",
)
# A process started through an object: `Vte.Terminal.spawn_async` (how
# the tab launched its child before the split) and `spawn_with_fds_async`
# (Vte's third spawn, the terminal's and the pty's), `Gio.
# SubprocessLauncher`'s `spawnv`, on any receiver. (A bare `.spawn(...)`
# is a word the client's own requests use; it is not followed.)
# What `gio_file_call` answers for a `Gio.File` made from a constant
# `resource://` address: a file object, and no site whatever is read off it.
RESOURCE = "<resource>"
PROCESS_METHODS_ANY = frozenset(
    {"spawn_async", "spawn_sync", "spawnv", "spawn_with_fds_async"}
)
# `GLib`'s own file and process calls (a dotted call's last name, after
# `GLib.`): every `file_*` and `spawn_*`, and these.
GLIB_CALL_PREFIXES = ("file_", "spawn_")
GLIB_CALLS = frozenset(
    {"mkdir_with_parents", "mkdtemp", "mkstemp", "unlink", "rmdir", "remove", "rename", "access", "chmod",
     "find_program_in_path", "dir_make_tmp"}
)
# Gio's process spawns and GTK's own launcher function (a dotted call's
# tail): `Gio.Subprocess.new(...)`, the keyword constructor
# `Gio.Subprocess(argv=...)`, `Gtk.show_uri(...)`.
GIO_PROCESS_CALLS = frozenset({"Subprocess.new", "Subprocess.newv", "Gio.Subprocess", "Gtk.show_uri"})
# The launchers (PR-3.0 item 7, the Fable review of PR 615's (a)): they
# open nothing themselves, they hand a path or a URI to a program of this
# device's, which is what a local extra is (or, for this device's own
# file or a web address, `DEVICE`). By a dotted call's tail: GTK's two
# launchers made, and GIO's default handler asked for a URI.
LAUNCHER_CALLS = frozenset(
    {
        "FileLauncher.new", "UriLauncher.new", "AppInfo.launch_default_for_uri",
        "AppInfo.launch_default_for_uri_async",
    }
)
# ...and by a method's name on any receiver, the names only a launcher or
# a `Gio.AppInfo` has: `launcher.open_containing_folder(...)`,
# `app_info.launch_uris(...)`.
LAUNCHER_METHODS_ANY = frozenset(
    {
        "open_containing_folder", "launch_uris", "launch_uris_async", "launch_uris_as_manager",
        "launch_uris_as_manager_with_fds",
    }
)
# `Gio.AppInfo.launch(files, context)`: `launch` is a word other objects
# use too (a `Gtk.FileLauncher`'s, whose `new` is the site already), so a
# receiver whose name says it is an app's info (`info`, `app_info`).
APP_INFO_HINTS = ("info",)
# Image constructors that open a file by path (a dotted call's tail).
IMAGE_FILE_CALLS = frozenset(
    {
        "Texture.new_from_filename", "Texture.new_from_file", "Pixbuf.new_from_file",
        "Pixbuf.new_from_file_at_scale", "Pixbuf.new_from_file_at_size", "PixbufAnimation.new_from_file",
        "PixbufAnimation.new_from_file_at_scale", "Picture.new_for_filename", "Image.new_from_file",
        "MediaFile.new_for_filename", "Video.new_for_filename", "Picture.new_for_file",
        "MediaFile.new_for_file", "Video.new_for_file", "Builder.new_from_file", "Pixbuf.get_file_info",
        "Pixbuf.get_file_info_async", "IOChannel.new_file", "FileLoader.new", "FileSaver.new",
        "FileSaver.new_with_target",
    }
)
# A widget's or a provider's method that opens a file by path, on any
# receiver (the names are GTK's own): `picture.set_filename(p)`,
# `image.set_from_file(p)`, `css.load_from_path(p)`.
# And by `Gio.File`, or writing one: `picture.set_file(gfile)`,
# `css.load_from_file(gfile)`, `builder.add_from_file(p)`,
# `pixbuf.savev(p, ...)`, `texture.save_to_png(p)`.
WIDGET_FILE_METHODS = frozenset(
    {
        "set_filename", "set_from_file", "load_from_path", "set_file", "load_from_file", "add_from_file",
        "savev", "save_to_png", "save_to_tiff",
    }
)
# The GTK classes whose keyword-property constructor opens a file
# (`Gtk.Picture(file=g)`, `GtkSource.FileLoader(buffer=b, location=f)`:
# PyGObject's ordinary way to construct with a property), and the
# keywords that name one.
KEYWORD_FILE_CLASSES = frozenset({"Picture", "Image", "MediaFile", "Video", "FileLoader", "FileSaver"})
FILE_KEYWORDS = ("file", "filename", "location")
PATH_CONSTRUCTORS = ("Path", "PurePath", "PurePosixPath", "PosixPath")
# The modules whose import aliases are resolved (`_aliases`).
ALIAS_SOURCES = (
    "os", "os.path", "shutil", "subprocess", "io", "glob", "builtins", "pathlib", "tempfile", "codecs",
    "webbrowser", "posixpath",
)

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


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


@functools.cache
def _parsed() -> tuple[tuple[str, ast.Module], ...]:
    """Every walked module, parsed once for the run (the trees are read,
    never changed)."""
    found: list[tuple[str, ast.Module]] = []
    for file in sorted(ROOT.glob("*.py")):
        name = file.stem
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        if name in CLIENT_HELPERS or _is_gtk_module(tree):
            found.append((name, tree))
    return tuple(found)


def modules() -> dict[str, ast.Module]:
    """Module name -> its tree, for every module walked."""
    return dict(_parsed())


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


def _aliases(tree: ast.Module) -> dict[str, str]:
    """What a module's imports rename, as first name -> what it stands
    for: `import shutil as sh` -> {"sh": "shutil"}; `import os.path as
    osp` -> {"osp": "os.path"}; `from os import path as P` -> {"P":
    "os.path"}; `from os.path import isdir as there` -> {"there":
    "os.path.isdir"}; `from shutil import which` -> {"which":
    "shutil.which"}; `from gi.repository import Gio as G` -> {"G":
    "Gio"}. Imports anywhere in the module count (a function's own
    included); a name the module rebinds afterwards is not followed."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname and alias.asname != alias.name and alias.name in ALIAS_SOURCES:
                    found[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module in ALIAS_SOURCES:
                for alias in node.names:
                    found[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            elif node.module == "gi.repository":
                for alias in node.names:
                    if alias.asname and alias.asname != alias.name:
                        found[alias.asname] = alias.name
    return found


def _resolve(dotted: str, aliases: dict[str, str]) -> str:
    """*dotted* with its first name replaced by what the module imported
    it as (`sh.which` -> `shutil.which`, `there` -> `os.path.isdir`)."""
    first, _dot, rest = dotted.partition(".")
    head = aliases.get(first)
    resolved = dotted if head is None else (f"{head}.{rest}" if rest else head)
    # `posixpath` is `os.path` on Linux, under the name the module gives it.
    if resolved == "posixpath" or resolved.startswith("posixpath."):
        resolved = "os.path" + resolved[len("posixpath") :]
    return resolved


def _names_a_path_type(annotation: ast.AST | None) -> bool:
    """Whether an annotation says `Path` (`Path`, `Path | None`,
    `Optional[pathlib.Path]`, `"Path"`)."""
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in PATH_CONSTRUCTORS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in PATH_CONSTRUCTORS:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            words = node.value.replace("|", " ").replace("[", " ").replace("]", " ").replace(".", " ").split()
            if any(word in PATH_CONSTRUCTORS for word in words):
                return True
    return False


def _pairs(target: ast.AST, value: ast.AST) -> list[tuple[str, ast.AST]]:
    """(name, the value bound to it) for an assignment: a plain name, or
    a tuple of names against a tuple of values (`a, b = Path(p), q`)."""
    if isinstance(target, ast.Name):
        return [(target.id, value)]
    if (
        isinstance(target, ast.Tuple | ast.List)
        and isinstance(value, ast.Tuple | ast.List)
        and len(target.elts) == len(value.elts)
    ):
        found: list[tuple[str, ast.AST]] = []
        for one, its in zip(target.elts, value.elts, strict=True):
            found.extend(_pairs(one, its))
        return found
    return []


def _own_nodes(scope: ast.AST):
    """The nodes of *scope* that are not inside a function, a lambda or a
    class of its own (a module's top level)."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        node = todo.pop()
        if isinstance(node, _SCOPES + (ast.Lambda,)):
            continue
        yield node
        todo.extend(ast.iter_child_nodes(node))


class _Reader:
    """The walker's reading of one module: its import aliases, and the
    questions asked of a node with them."""

    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases

    def name(self, node: ast.AST) -> str | None:
        """The dotted name of a Name / Attribute chain, aliases resolved."""
        dotted = _dotted(node)
        return None if dotted is None else _resolve(dotted, self.aliases)

    def is_path_call(self, node: ast.AST) -> bool:
        """A `Path(...)` / `PurePath` / `PurePosixPath` / `PosixPath`
        call, or `Path.home()` / `Path.cwd()`."""
        if not isinstance(node, ast.Call):
            return False
        parts = (self.name(node.func) or "").split(".")
        if parts[-1] in PATH_CONSTRUCTORS:
            return True
        return len(parts) >= 2 and parts[-2] in PATH_CONSTRUCTORS and parts[-1] in ("home", "cwd")

    def gio_file_call(self, node: ast.AST) -> str | None:
        """`new_for_path` / `new_for_uri` / `new_for_commandline_arg` when
        *node* is that `Gio.File` constructor's call, else None."""
        if not isinstance(node, ast.Call):
            return None
        parts = (self.name(node.func) or "").split(".")
        constructor = None
        if len(parts) >= 2 and parts[-2] == "File" and parts[-1] in GIO_FILE_CONSTRUCTORS:
            constructor = parts[-1]
        elif len(parts) >= 2 and parts[-2] == "Gio" and parts[-1].startswith("file_"):
            if parts[-1][len("file_") :] in GIO_FILE_CONSTRUCTORS:
                constructor = parts[-1][len("file_") :]
        if constructor is None:
            return None
        # A constant `resource://` address is the package's own bundled
        # data, compiled into the program: no file of anyone's is opened.
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if first.value.startswith("resource:"):
                return RESOURCE
        return constructor

    def looks_like_path(self, node: ast.AST, path_names: frozenset[str] = frozenset()) -> bool:
        """Whether a method's receiver is a path: a `Path(...)` call, a
        chain through a path's own path-returning methods
        (`.expanduser()`, `.resolve()`, ...) off one, a chain ending in
        `.parent` or `.with_*`, a `/` expression whose left side is one,
        a name bound to one of those in the enclosing function
        (*path_names*), or a name carrying a path hint."""
        if self.is_path_call(node):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in PATH_RETURNING
        ):
            return self.looks_like_path(node.func.value, path_names)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self.looks_like_path(node.left, path_names)
        if isinstance(node, ast.Attribute) and (node.attr == "parent" or node.attr.startswith("with_")):
            return True
        if isinstance(node, ast.NamedExpr):
            return self.looks_like_path(node.value, path_names)
        dotted = _dotted(node)
        if dotted is None:
            return False
        if dotted in path_names:
            return True
        leaf = dotted.split(".")[-1].lower()
        return any(hint in leaf for hint in PATH_NAME_HINTS)

    def path_valued(self, value: ast.AST, bound: frozenset[str] | set[str]) -> bool:
        """Whether an assigned *value* is a path: a `Path(...)`, a chain or
        a `/` off a path, a `.parent` or `.with_*`, or a name already
        bound to one (a hinted name alone binds nothing: `x = self.path`
        does not make `x` a path)."""
        if isinstance(value, ast.Name):
            return value.id in bound
        if isinstance(value, ast.Attribute):
            if not (value.attr == "parent" or value.attr.startswith("with_")):
                return False
        return self.looks_like_path(value, frozenset(bound))

    def yields_paths(self, iterable: ast.AST, bound: frozenset[str] | set[str]) -> bool:
        """Whether a loop's *iterable* hands out paths: `map(Path, ...)`,
        `<path>.iterdir()` / `.glob()` / `.rglob()`, or one of those
        inside `sorted()` / `list()` / `tuple()` / `reversed()`."""
        if not isinstance(iterable, ast.Call):
            return False
        name = self.name(iterable.func) or ""
        if name == "map" and iterable.args:
            mapped = (self.name(iterable.args[0]) or "").split(".")[-1]
            return mapped in PATH_CONSTRUCTORS
        if name in ("sorted", "list", "tuple", "reversed") and iterable.args:
            return self.yields_paths(iterable.args[0], bound)
        if isinstance(iterable.func, ast.Attribute) and iterable.func.attr in PATH_ITERATING:
            return self.looks_like_path(iterable.func.value, frozenset(bound))
        return False

    def bound_paths(
        self, scope: ast.AST, inherited: frozenset[str], own_only: bool = False
    ) -> frozenset[str]:
        """The names *scope* binds to a path, with the ones it inherits: a
        `name = <path>` / `name: T = <path>` / `(name := <path>)`, a
        tuple of them, a parameter annotated `Path` or defaulting to one,
        a loop's or a comprehension's target over paths, and a name bound
        to a name that is one (`q = p`). Flow is not followed: a name
        bound to a path anywhere in the function is a path all through
        it, nested functions included (a worker closure's binding
        counts). *own_only* is a module's top level."""
        bound = set(inherited)
        pairs: list[tuple[str, ast.AST]] = []
        loops: list[tuple[str, ast.AST]] = []
        nodes = _own_nodes(scope) if own_only else ast.walk(scope)
        for child in nodes:
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    pairs.extend(_pairs(target, child.value))
            elif isinstance(child, ast.AnnAssign):
                if isinstance(child.target, ast.Name):
                    if _names_a_path_type(child.annotation):
                        bound.add(child.target.id)
                    elif child.value is not None:
                        pairs.append((child.target.id, child.value))
            elif isinstance(child, ast.NamedExpr):
                pairs.extend(_pairs(child.target, child.value))
            elif isinstance(child, ast.arg):
                if _names_a_path_type(child.annotation):
                    bound.add(child.arg)
            elif isinstance(child, ast.For | ast.AsyncFor | ast.comprehension):
                if isinstance(child.target, ast.Name):
                    loops.append((child.target.id, child.iter))
            if isinstance(child, _FUNCTIONS + (ast.Lambda,)):
                args = child.args
                positional = [*args.posonlyargs, *args.args]
                defaulted = positional[len(positional) - len(args.defaults) :]
                for arg, default in zip(defaulted, args.defaults, strict=True):
                    pairs.append((arg.arg, default))
                for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
                    if default is not None:
                        pairs.append((arg.arg, default))
        moved = True
        while moved:
            moved = False
            for name, value in pairs:
                if name not in bound and self.path_valued(value, bound):
                    bound.add(name)
                    moved = True
            for name, iterable in loops:
                if name not in bound and self.yields_paths(iterable, bound):
                    bound.add(name)
                    moved = True
        return frozenset(bound)

    def gio_valued(self, value: ast.AST, bound: dict[str, str]) -> str | None:
        """The `Gio.File` constructor *value* comes from: the constructor's
        own call, a chain of calls off one (`.get_child(...)`), or a name
        bound to one. None when it is no `Gio.File` the walker knows."""
        inner = value
        while True:
            constructor = self.gio_file_call(inner)
            if constructor is not None:
                return constructor
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                inner = inner.func.value
                continue
            break
        dotted = _dotted(inner)
        return bound.get(dotted) if dotted is not None else None

    def bound_gio(self, scope: ast.AST, inherited: dict[str, str], own_only: bool = False) -> dict[str, str]:
        """The names *scope* binds to a `Gio.File`, each with the
        constructor it came from; a name bound to such a name too."""
        bound = dict(inherited)
        pairs: list[tuple[str, ast.AST]] = []
        nodes = _own_nodes(scope) if own_only else ast.walk(scope)
        for child in nodes:
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    pairs.extend(_pairs(target, child.value))
            elif isinstance(child, ast.AnnAssign) and child.value is not None:
                pairs.extend(_pairs(child.target, child.value))
            elif isinstance(child, ast.NamedExpr):
                pairs.extend(_pairs(child.target, child.value))
        moved = True
        while moved:
            moved = False
            for name, value in pairs:
                if name in bound:
                    continue
                if isinstance(value, ast.Name):
                    constructor = bound.get(value.id)
                else:
                    constructor = self.gio_valued(value, {})
                if constructor is not None:
                    bound[name] = constructor
                    moved = True
        return bound

    def function_site(self, node: ast.AST) -> str | None:
        """The site a Name / Attribute chain names when it is one of the
        module-level functions the walker knows (`os.path.isdir`,
        `shutil.which`, `GLib.file_test`, ...), else None."""
        name = self.name(node)
        return None if name is None else self.named_site(name)

    def named_site(self, name: str) -> str | None:
        """`function_site` for a dotted name already resolved."""
        if name in OPEN_CALLS:
            return name
        parts = name.split(".")
        if ".".join(parts[-2:]) in NOT_SITES:
            return None
        if len(parts) == 3 and name.startswith("os.path.") and parts[-1] in OS_PATH_CALLS:
            return name
        if len(parts) == 2 and parts[0] == "os":
            if parts[1] in OS_CALLS or parts[1].startswith(OS_CALL_PREFIXES):
                return name
        if len(parts) >= 2 and parts[0] in SITE_MODULES:
            return name
        tail = ".".join(parts[-2:])
        if tail in IMAGE_FILE_CALLS or tail in GIO_PROCESS_CALLS or tail in LAUNCHER_CALLS:
            return tail
        if tail in GIO_FILE_CREATING:
            return "Gio.File.new_tmp"
        if len(parts) >= 2 and parts[-2] == "GLib":
            if parts[-1].startswith(GLIB_CALL_PREFIXES) or parts[-1] in GLIB_CALLS:
                return tail
        return None

    def reference_site(self, node: ast.AST) -> str | None:
        """The site a Name / Attribute chain names when it is such a
        function **not called here**: handed on (`map(os.path.isdir,
        ps)`, `key=os.path.getmtime`) or bound to a name to call later
        (`fn = os.path.isdir; fn(p)`). Lower-case names only, so a
        constant or a class in an annotation (`subprocess.DEVNULL`,
        `subprocess.CompletedProcess`) is no site; a bare `open` handed
        on is not followed either (a name any scope may rebind)."""
        if not isinstance(node, ast.Attribute):
            return None
        parts = (self.name(node) or "").split(".")
        if len(parts) >= 2 and parts[-2] in PATH_CONSTRUCTORS:
            # A path's method handed on or bound uncalled (`filter(Path.is_dir,
            # ps)`, `key=Path.stat`, `fn = Path.read_text`): the called form
            # is caught through the method tables; this is the other one.
            if parts[-1] in PATH_METHODS or parts[-1] in PATH_METHODS_IF_PATH or parts[-1] in PATH_RETURNING:
                return f"Path.{parts[-1]}"
            return None
        name = self.function_site(node)
        if name is None or not name.split(".")[-1].islower():
            return None
        return name

    def getattr_site(self, node: ast.Call) -> str | None:
        """`getattr(os.path, "isdir")` and the like: a function of one of
        the modules the walker reads, fetched by name. With a constant
        name it is the site that name is, or none (`getattr(os,
        "O_NOFOLLOW", 0)` is a flag); with a name the walker cannot read
        it is a site whatever it turns out to be."""
        if _dotted(node.func) != "getattr" or len(node.args) < 2:
            return None
        module = self.name(node.args[0])
        if module not in ALIAS_SOURCES or module in ("builtins", "pathlib", "io", "codecs"):
            return None
        fetched = node.args[1]
        if isinstance(fetched, ast.Constant) and isinstance(fetched.value, str):
            return self.named_site(f"{module}.{fetched.value}")
        return f"getattr.{module}"

    def site_name(
        self,
        node: ast.Call,
        path_names: frozenset[str] = frozenset(),
        gio_names: dict[str, str] | None = None,
    ) -> str | None:
        """The call's name when it is a filesystem or process site, else None."""
        func = node.func
        name = self.function_site(func) or self.getattr_site(node)
        if name is not None:
            return name
        parts = (self.name(func) or "").split(".")
        if len(parts) >= 2 and parts[-1] in KEYWORD_FILE_CLASSES:
            named = [keyword.arg for keyword in node.keywords if keyword.arg in FILE_KEYWORDS]
            if named:
                return f"Widget.{parts[-1]}({named[0]}=)"
        if isinstance(func, ast.Attribute):
            if func.attr == "set_property" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value in ("file", "filename"):
                    return f"Widget.set_property.{first.value}"
            constructor = self.gio_valued(func.value, gio_names or {})
            if constructor == RESOURCE:
                return None
            if constructor is not None and func.attr.startswith(GIO_FILE_METHODS):
                return f"Gio.File.{constructor}.{func.attr}"
            method = func.attr
            if method in GIO_FILE_METHODS_ANY:
                return f"Gio.File.{method}"
            if method in PROCESS_METHODS_ANY:
                return f"Process.{method}"
            if method in LAUNCHER_METHODS_ANY:
                return f"Launcher.{method}"
            if method == "launch":
                receiver = (_dotted(func.value) or "").split(".")[-1].lower()
                if any(hint in receiver for hint in APP_INFO_HINTS):
                    return "Launcher.launch"
            most = PATH_METHODS_MAX_ARGS.get(method)
            if most is not None and len(node.args) > most:
                return None  # another object's method of the same name
            if method in PATH_METHODS_ANY:
                return f"Path.{method}"
            if method in PATH_METHODS and self.looks_like_path(func.value, path_names):
                return f"Path.{method}"
            if method in PATH_METHODS_IF_PATH and self.looks_like_path(func.value, path_names):
                return f"Path.{method}"
            if method in WIDGET_FILE_METHODS:
                return f"Widget.{method}"
        return None


class _Walker(ast.NodeVisitor):
    def __init__(self, module: str, tree: ast.Module) -> None:
        self.module = module
        self.reader = _Reader(_aliases(tree))
        self.stack: list[str] = []
        self.sites: set[str] = set()
        # Every site with the call it is, for `function_asks_local`.
        # ...and with the function it is written in (None at a module's or
        # a class's own level): two functions may share a qualname (a
        # property's getter and setter, a redefinition), and each call is
        # judged in its own.
        self.calls: list[tuple[str, ast.AST, ast.AST | None]] = []
        self._called: set[int] = set()
        self._functions: list[ast.AST | None] = [None]
        # The path-bound and Gio.File-bound names in sight: the module's
        # own first, then each enclosing function's (a nested function
        # sees its own too).
        self.path_names: list[frozenset[str]] = [self.reader.bound_paths(tree, frozenset(), own_only=True)]
        self.gio_names: list[dict[str, str]] = [self.reader.bound_gio(tree, {}, own_only=True)]

    def _qualname(self) -> str:
        return ".".join(self.stack) if self.stack else "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self._functions.append(None)
        self.generic_visit(node)
        self._functions.pop()
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self._functions.append(node)
        self.path_names.append(self.reader.bound_paths(node, self.path_names[-1]))
        self.gio_names.append(self.reader.bound_gio(node, self.gio_names[-1]))
        self.generic_visit(node)
        self.gio_names.pop()
        self.path_names.pop()
        self._functions.pop()
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)

    def _found(self, name: str, node: ast.AST) -> None:
        site = f"{self.module}:{self._qualname()}:{name}"
        self.sites.add(site)
        self.calls.append((site, node, self._functions[-1]))

    def visit_Call(self, node: ast.Call) -> None:
        name = self.reader.site_name(node, self.path_names[-1], self.gio_names[-1])
        if name is not None:
            self._found(name, node)
        self._called.add(id(node.func))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # A site's function named without being called: handed on, or
        # bound to a name. (A call's own `func` was read by visit_Call.)
        if id(node) not in self._called and isinstance(node.ctx, ast.Load):
            name = self.reader.reference_site(node)
            if name is not None:
                self._found(name, node)
                return
        self.generic_visit(node)


def sites_of(module: str, tree: ast.Module) -> set[str]:
    walker = _Walker(module, tree)
    walker.visit(tree)
    return walker.sites


def walk() -> set[str]:
    """Every site in every walked module."""
    return set(counts())


def counts() -> dict[str, int]:
    """Every site in every walked module, with the number of calls (or
    uncalled references) that make it: what `NOT_A_SITE` pins an entry
    to."""
    found: dict[str, int] = {}
    for name, tree in modules().items():
        walker = _Walker(name, tree)
        walker.visit(tree)
        for site, _node, _function in walker.calls:
            found[site] = found.get(site, 0) + 1
    return found


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
    return node if isinstance(node, _FUNCTIONS) else None


# -- the local proof: is a site behind `apilink.is_local()` --------------------------------


def _ask_names(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    """How a module spells the ask: (the names `apilink` is bound to, the
    names `apilink.is_local` itself is imported as). `from . import
    apilink`, `from collins import apilink as link`, `import
    collins.apilink as link`, `from .apilink import is_local`."""
    modules_: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = node.module or ""
            if (node.level >= 1 and source == "") or (node.level == 0 and source == "collins"):
                for alias in node.names:
                    if alias.name == "apilink":
                        modules_.add(alias.asname or alias.name)
            if (node.level >= 1 and source == "apilink") or (node.level == 0 and source == "collins.apilink"):
                for alias in node.names:
                    if alias.name == "is_local":
                        functions.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "collins.apilink":
                    modules_.add(alias.asname or alias.name)
    # A name the module itself defines or assigns at its top level is no
    # longer the import (`from .apilink import is_local`, then `def
    # is_local(): return True`).
    rebound = _bound_in(tree, own_only=True)
    return frozenset(modules_ - rebound), frozenset(functions - rebound)


def _bound_in(scope: ast.AST, own_only: bool = False) -> set[str]:
    """The names *scope* binds other than by import: assigned, a loop's
    or a `with`'s target, a parameter, a function or class defined. For a
    function, everything in it (nested functions too); *own_only* is a
    module's top level."""
    names: set[str] = set()
    if own_only:
        nodes = list(_own_nodes(scope))
        nodes += [node for node in ast.iter_child_nodes(scope) if isinstance(node, _SCOPES)]
    else:
        nodes = [node for node in ast.walk(scope) if node is not scope]
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            names.add(node.id)
        elif isinstance(node, _SCOPES):
            names.add(node.name)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store | ast.Del):
            # `apilink.is_local = ...`: the module's function reassigned as
            # an attribute, which the AST shows as plainly as a name.
            if node.attr == "is_local":
                receiver = _dotted(node.value)
                if receiver is not None:
                    names.add(receiver)
        elif isinstance(node, ast.Call) and _dotted(node.func) == "setattr" and len(node.args) >= 2:
            # `setattr(apilink, "is_local", ...)`, the same with a constant name.
            fetched = node.args[1]
            receiver = _dotted(node.args[0])
            if isinstance(fetched, ast.Constant) and fetched.value == "is_local" and receiver is not None:
                names.add(receiver)
    if not own_only:
        names.update(node.arg for node in ast.walk(scope) if isinstance(node, ast.arg))
    return names


def _is_ask(node: ast.AST, asks: tuple[frozenset[str], frozenset[str]]) -> bool:
    """`apilink.is_local()`: the module's own function, **called**. Not
    the bare attribute (`if not apilink.is_local:` is always false: the
    bug that opens every gate), and not some other object's `is_local()`
    (`remoteuploads.is_local`, `Gdk.Clipboard.is_local`, `self.is_local`)."""
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    dotted = _dotted(node.func)
    if dotted is None:
        return False
    modules_, functions = asks
    if dotted in functions:
        return True
    receiver, _dot, attr = dotted.rpartition(".")
    return attr == "is_local" and receiver in modules_


def _true_means_local(test: ast.AST, asks) -> bool:
    """Whether *test* being true says the ask was made and answered yes:
    the ask itself, or an `and` with it among its operands."""
    if _is_ask(test, asks):
        return True
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(_true_means_local(value, asks) for value in test.values)
    return False


def _false_means_local(test: ast.AST, asks) -> bool:
    """Whether *test* being false says so: `not <ask>`, or an `or` with
    that among its operands (`if one is None or not apilink.is_local():`)."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _true_means_local(test.operand, asks)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return any(_false_means_local(value, asks) for value in test.values)
    return False


def _behind_the_ask(function: ast.AST, call: ast.AST, asks) -> bool:
    """Whether *call*, somewhere in *function*, runs only after
    `apilink.is_local()` answered yes, in one of the shapes the walker
    can read:

    1. a refusal earlier in the function's own body, a statement of the
       body itself (so everything after it is behind it): ``if not
       apilink.is_local(): return`` (or ``raise``; the test may be an
       ``or`` with other refusals, the body must end in the return, and
       there is no ``else``);
    2. the call inside the body of ``if apilink.is_local():`` (or an
       ``and`` with it), or in the ``else`` of ``if not
       apilink.is_local():``, as a statement or a conditional expression;
    3. the call in an operand after the ask's in the same ``and``
       (``apilink.is_local() and os.path.isdir(p)``), or after ``not
       <ask>`` in the same ``or``.

    An ask anywhere else does not count: after the call, in another
    branch, inside a lambda or a nested function the call is not in, or
    bound to a name first (``local = apilink.is_local()``)."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(function):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    child: ast.AST = call
    top: ast.AST | None = None
    while child is not function:
        parent = parents.get(child)
        if parent is None:
            return False
        if isinstance(parent, ast.If | ast.IfExp):
            body = parent.body if isinstance(parent.body, list) else [parent.body]
            orelse = parent.orelse if isinstance(parent.orelse, list) else [parent.orelse]
            if any(child is one for one in body) and _true_means_local(parent.test, asks):
                return True
            if any(child is one for one in orelse) and _false_means_local(parent.test, asks):
                return True
        if isinstance(parent, ast.BoolOp):
            before = parent.values[: next(i for i, one in enumerate(parent.values) if one is child)]
            if isinstance(parent.op, ast.And) and any(_true_means_local(one, asks) for one in before):
                return True
            if isinstance(parent.op, ast.Or) and any(_false_means_local(one, asks) for one in before):
                return True
        if parent is function:
            top = child
        child = parent
    body = getattr(function, "body", [])
    if not any(top is statement for statement in body):
        return False  # a default argument, a decorator: nothing runs before it
    for statement in body:
        if statement is top:
            break
        if (
            isinstance(statement, ast.If)
            and not statement.orelse
            and _false_means_local(statement.test, asks)
            and isinstance(statement.body[-1], ast.Return | ast.Raise)
        ):
            return True
    return False


def function_asks_local(site: str, trees: dict[str, ast.Module] | None = None) -> bool:
    """Whether a ``module:qualname:call`` *site* is behind the local
    proof: **every** call of that name, in **every** function the
    qualname names (a property's getter and its setter, a function
    defined twice), runs only after `apilink.is_local()` — the module's
    function, called — answered yes (`_behind_the_ask` has the shapes).
    The static half of "a local extra is behind `app.local`".

    A site at a module's or a class's own level has no function to ask
    in and is False; so is a site in a nested function that does not ask
    itself (the ask has to be in the function the site is named by), and
    a site or module that is not there. *trees* is `modules()` when the
    caller has it already.

    A function that binds `apilink` (or the imported `is_local`) itself
    — a parameter of that name, an assignment, a loop variable — or a
    module that redefines it at its top level has no ask: the name is
    not the import there.

    What this does not prove: that the name was not rebound some way the
    AST does not show (`globals()`, a `global` statement in another
    function, `setattr` with a name it cannot read; an attribute store
    to `<module>.is_local` and `setattr(<module>, "is_local", …)` in the
    function are seen); that a helper the function calls
    reads nothing before the ask; and anything about a read the walker
    has no word for (the module docstring's list)."""
    module, _qualname, _call = site.split(":", 2)
    tree = (modules() if trees is None else trees).get(module)
    if tree is None:
        return False
    walker = _Walker(module, tree)
    walker.visit(tree)
    calls = [(node, function) for name, node, function in walker.calls if name == site]
    if not calls or any(function is None for _node, function in calls):
        return False
    modules_, functions = _ask_names(tree)

    def behind(node: ast.AST, function: ast.AST) -> bool:
        # A name the function binds itself (a parameter called `apilink`, a
        # loop variable, an assignment) is not the module's import there.
        rebound = _bound_in(function)
        asks = (
            modules_ - {name for name in modules_ if name in rebound or name.split(".")[0] in rebound},
            functions - rebound,
        )
        return _behind_the_ask(function, node, asks)

    return all(behind(node, function) for node, function in calls)


if __name__ == "__main__":
    for site in sorted(walk()):
        print(site)
