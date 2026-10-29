# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The path picker's model (split-service spec §3.11, §3.24; PR-3.2),
GTK-free: what `pathpicker.PathPicker` shows and does, minus the widgets.

The picker chooses a folder on the service's machine for a client that is
not on it (`choosers.choose_folder`; a `local` client gets the desktop's
own chooser). Everything it reads is the service's:

- the listing is `fs.list` of the folder the entry names
  (`list_folders`), its subfolders only (a symlink to a folder counts),
  asked off the main loop and applied only while it is still the newest
  ask (`Listing`): the entry asks again at most every
  `LIST_DEBOUNCE_MS`, a click or a shortcut at once;
- the entry's text is a path of the service's, a leading `~` its home
  (`apilink.service_home`, `service.status`'s `home`): `split` names the
  folder to list and the prefix the rows are filtered by (`matching`),
  `complete` is what Tab makes of it, a prefix with a leading dot lists
  the hidden folders too (`wants_hidden`);
- the shortcuts are the service's home and the project roots of the
  sessions the store lists (`shortcuts`, at most `SHORTCUTS_MAX`);
- *New folder* is `fs.mkdir` of one bare name in the folder shown
  (`new_folder_error` first, the same rule the service applies);
- *Open* is the service's `fs.stat` of the typed path (`expand`): the
  pick is a folder that is there, as the service's own words say.

Nothing here reads this device's disk: the pure functions take the
service's home and answers as arguments, and the two requests go through
`remotefiles`.
"""

from __future__ import annotations

import os
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass

from . import remotefiles
from .projectfiles import RenameError, name_error

# How long the entry waits after a keystroke before it lists (§3.24).
LIST_DEBOUNCE_MS = 300
# How many shortcuts the side list holds: the home, then project roots.
SHORTCUTS_MAX = 12
# What an `fs.list` entry of a folder is (`protocol.FS_ENTRY_KINDS`: a
# symlink entry is one to a folder; the service lists no symlink to a file
# as a link).
_FOLDER_KINDS = frozenset({"dir", "symlink"})


@dataclass(frozen=True)
class Folder:
    """One row of the listing: a subfolder's name, and whether it is a
    symlink to one."""

    name: str
    link: bool = False


def expand(text: str, home: str | None) -> str | None:
    """The absolute path *text* names on the service's machine, or None:
    a leading `~` (alone, or `~/…`; never `~user`) is *home*, anything
    else must be absolute. Normalised, a trailing slash dropped."""
    text = text.strip()
    if text == "~" or text.startswith("~/"):
        if not home or not home.startswith("/"):
            return None
        text = home + text[1:]
    if not text.startswith("/"):
        return None
    return posixpath.normpath(text) if text != "/" else "/"


def split(text: str, home: str | None) -> tuple[str, str] | None:
    """The folder the entry's *text* is in and the name typed so far:
    ``/a/b/c`` is ``("/a/b", "c")``, ``/a/b/`` and ``~`` name the folder
    itself with no prefix. None for a path that is not absolute (or a `~`
    with no home known)."""
    stripped = text.strip()
    if stripped in ("~", "~/") or stripped.endswith("/"):
        folder = expand(stripped, home)
        return (folder, "") if folder is not None else None
    path = expand(stripped, home)
    if path is None:
        return None
    if path == "/":
        return "/", ""
    head, tail = posixpath.split(path)
    return head or "/", tail


def display(path: str, home: str | None) -> str:
    """*path* as the entry shows it: under the service's *home* as `~/…`."""
    if home and home != "/":
        home = home.rstrip("/")
        if path == home:
            return "~"
        if path.startswith(home + "/"):
            return "~" + path[len(home) :]
    return path


def entry_text(folder: str, home: str | None) -> str:
    """The entry's text for a folder the picker moved to: shown, and
    ending in a slash so the next keystroke filters its subfolders."""
    shown = display(folder, home)
    return shown if shown.endswith("/") else shown + "/"


def child(folder: str, name: str) -> str:
    return posixpath.join(folder, name)


def parent(folder: str) -> str | None:
    """The folder above *folder*, or None at the top."""
    if folder in ("", "/"):
        return None
    return posixpath.dirname(folder.rstrip("/")) or "/"


def folders(entries: Iterable[remotefiles.Entry]) -> list[Folder]:
    """The subfolders among an `fs.list`'s entries, in the service's order
    (folders first, then case-insensitively by name)."""
    return [Folder(entry.name, entry.kind == "symlink") for entry in entries if entry.kind in _FOLDER_KINDS]


def matching(rows: list[Folder], prefix: str) -> list[Folder]:
    """The rows the entry's *prefix* keeps: those whose name starts with
    it, or, when none does, those that start with it ignoring case."""
    if not prefix:
        return list(rows)
    exact = [row for row in rows if row.name.startswith(prefix)]
    if exact:
        return exact
    folded = prefix.casefold()
    return [row for row in rows if row.name.casefold().startswith(folded)]


def wants_hidden(prefix: str) -> bool:
    """Whether the listing should hold the dot folders: the typed name
    starts with a dot."""
    return prefix.startswith(".")


def common_prefix(names: list[str]) -> str:
    if not names:
        return ""
    return os.path.commonprefix(names)


def complete(text: str, home: str | None, rows: list[Folder]) -> str | None:
    """What Tab makes of the entry's *text* given the listing of the folder
    it is in (*rows*): one match is its folder with a trailing slash, more
    are their longest common start (when it is longer than what was
    typed); None when there is nothing to add."""
    where = split(text, home)
    if where is None:
        return None
    folder, prefix = where
    found = matching(rows, prefix)
    if not found:
        return None
    if len(found) == 1:
        return entry_text(child(folder, found[0].name), home)
    start = common_prefix([row.name for row in found])
    if len(start) <= len(prefix):
        return None
    shown = entry_text(folder, home)
    return shown + start


def shortcuts(home: str | None, roots: Iterable[str]) -> list[tuple[str, str]]:
    """The side list: (label, path), the service's *home* first (labelled
    by the caller), then each distinct project root by its folder's name
    (sorted that way, case-insensitively), at most SHORTCUTS_MAX in all.
    A root that is not absolute is dropped, and so is the home itself."""
    found: list[tuple[str, str]] = []
    if home and home.startswith("/"):
        found.append(("", posixpath.normpath(home)))
    seen = {path for _label, path in found}
    rest: list[tuple[str, str]] = []
    for root in roots:
        if not isinstance(root, str) or not root.startswith("/"):
            continue
        path = posixpath.normpath(root)
        if path in seen:
            continue
        seen.add(path)
        rest.append((posixpath.basename(path) or path, path))
    rest.sort(key=lambda row: (row[0].casefold(), row[1]))
    return (found + rest)[:SHORTCUTS_MAX]


def new_folder_error(name: str) -> str | None:
    """Why *name* can't be a new folder's name (the user's words, an
    untranslated msgid), or None: the service's own rule
    (`projectfiles.name_error`), run first so a typo is not a round trip."""
    verdict = name_error(name.strip())
    if verdict is RenameError.EMPTY:
        return "Type a name for the folder"
    if verdict is not None:
        return "A folder takes a bare name"
    return None


class Listing:
    """Which `fs.list` answer the rows show: each ask is numbered, and an
    answer lands only while it is the newest one (a slow listing of a
    folder the person has typed past is dropped)."""

    def __init__(self) -> None:
        self.asked = 0
        self.folder: str | None = None
        self.hidden = False
        self.rows: list[Folder] = []
        self.truncated = False
        self.error = ""

    def needs(self, folder: str, hidden: bool) -> bool:
        """Whether the rows are not already *folder*'s (with or without the
        dot folders, as asked): a filter alone asks the service nothing."""
        return folder != self.folder or (hidden and not self.hidden)

    def ask(self, folder: str, hidden: bool) -> int:
        self.asked += 1
        self.folder = folder
        self.hidden = hidden
        return self.asked

    def land(self, token: int, rows: list[Folder] | None, truncated: bool = False, error: str = "") -> bool:
        """Apply an answer: *rows* (None with *error* for a refusal).
        Whether it was the newest ask and was applied."""
        if token != self.asked:
            return False
        self.rows = list(rows or [])
        self.truncated = bool(truncated) and rows is not None
        self.error = error if rows is None else ""
        if rows is None:
            # A folder the service would not list: asking it again is
            # worth it once the person comes back to it.
            self.folder = None
        return True


# -- the two requests (blocking: a worker thread's, `remotefiles.off_main`) ----------------


def list_folders(folder: str, hidden: bool) -> tuple[list[Folder], bool]:
    """`fs.list` of *folder* on the service: its subfolders and whether
    the listing was cut. The folder is its own root (`fs.list`'s rule that
    a listed link stays inside the root then keeps a link out of it off
    the list). Raises `RequestRefused`."""
    entries, truncated = remotefiles.list_dir(folder, root=folder, hidden=hidden)
    return folders(entries), truncated


def make_folder(folder: str, name: str) -> str:
    """`fs.mkdir` of *name* inside *folder* (the root it is confined to):
    the new folder's path. Raises `RequestRefused` (its `reason` one of
    `protocol.FS_MKDIR_REASONS`)."""
    path = child(folder, name.strip())
    remotefiles.make_dir(path, root=folder)
    return path


def stat_folder(path: str) -> str | None:
    """The folder at *path* on the service's machine (its own normalised
    path), or None when nothing, or no folder, is there. Raises
    `RequestRefused`."""
    found = remotefiles.stat_path(path)
    if not found.is_dir:
        return None
    return found.path or path
