# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The path picker's model (split-service spec §3.11, §3.24; PR-3.2, D60),
GTK-free: what `pathpicker.PathPicker` shows and does, minus the widgets.

The picker chooses a folder on the service's machine for a client that is
not on it (`choosers.choose_folder`; a `local` client gets the desktop's
own chooser). Everything it reads is the service's, over the two
requests that are the picker's own and unconfined (D60): the whole
filesystem, as the native dialog browses it.

- The listing is `fs.dirs` of the folder the entry names
  (`list_folders`): its subfolders (a link to one flagged), the folder
  as the service listed it (`~` expanded there), and `truncated`. It is
  asked off the main loop and applied only while it is still the newest
  ask (`Listing`): the entry asks again at most every `LIST_DEBOUNCE_MS`,
  a click or a shortcut at once. *Show hidden* is the request's `hidden`.
- The entry's text is a path of the service's, read as text here and
  never expanded on this side: a leading `~` is the service's home, which
  the service expands. `split` names the folder to list and the name
  typed so far, which filters the rows (`matching`); `complete` is what
  Tab makes of it.
- The shortcuts are the service's home (`service.status`'s `home`) and
  the project roots of the sessions the store lists (`shortcuts`, at most
  `SHORTCUTS_MAX`).
- *New folder* is `fs.newdir` of one bare name in the folder shown
  (`new_folder_error` first, the service's own rule).
- *Open* is the service's `fs.stat` of the typed or navigated path
  (`stat_folder`): the pick is a folder that is there, handed on as the
  service echoed it (`~` expanded, normalised, never resolved).
"""

from __future__ import annotations

import os
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass

from . import remotefiles
from .i18n import N_
from .projectfiles import RenameError, name_error

# How long the entry waits after a keystroke before it lists (§3.24).
LIST_DEBOUNCE_MS = 300
# How many shortcuts the side list holds: the home, then project roots.
SHORTCUTS_MAX = 12


@dataclass(frozen=True)
class Folder:
    """One row of the listing: a subfolder's name, and whether it is a
    symlink to one (navigable like any other; it carries a link mark)."""

    name: str
    link: bool = False


def well_formed(text: str) -> bool:
    """Whether *text* names a path the service can take: absolute, or led
    by `~` (alone or `~/…`; never `~user`), which the service expands."""
    text = text.strip()
    return text.startswith("/") or text == "~" or text.startswith("~/")


def split(text: str) -> tuple[str, str] | None:
    """The folder the entry's *text* is in and the name typed so far:
    ``/a/b/c`` is ``("/a/b", "c")``, ``/a/b/`` names the folder itself
    with no name, ``~`` and ``~/`` the service's home, ``~/sr`` is
    ``("~", "sr")``. None for a path the service cannot take."""
    text = text.strip()
    if not well_formed(text):
        return None
    if text in ("~", "~/"):
        return "~", ""
    if text.endswith("/"):
        return text.rstrip("/") or "/", ""
    head, _sep, tail = text.rpartition("/")
    return head or "/", tail


def entry_text(folder: str) -> str:
    """The entry's text for a folder the picker moved to, ending in a
    slash so the next keystroke filters its subfolders."""
    return folder if folder.endswith("/") else folder + "/"


def child(folder: str, name: str) -> str:
    return posixpath.join(folder, name)


def parent(folder: str) -> str | None:
    """The folder above the absolute *folder*, or None at the top (or for
    a path that is not absolute: `~` is the service's to expand)."""
    if not folder.startswith("/") or folder == "/":
        return None
    return posixpath.dirname(folder.rstrip("/")) or "/"


def folders(rows: Iterable[tuple[str, bool]]) -> list[Folder]:
    """`fs.dirs`' entries as rows, in the service's order."""
    return [Folder(name, bool(link)) for name, link in rows]


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


def complete(text: str, rows: list[Folder]) -> str | None:
    """What Tab makes of the entry's *text* given the listing of the folder
    it is in (*rows*): one match is its folder with a trailing slash, more
    are their longest common start (when it is longer than what was
    typed); None when there is nothing to add."""
    where = split(text)
    if where is None:
        return None
    folder, prefix = where
    found = matching(rows, prefix)
    if not found:
        return None
    if len(found) == 1:
        return entry_text(child(folder, found[0].name))
    start = os.path.commonprefix([row.name for row in found])
    if len(start) <= len(prefix):
        return None
    return entry_text(folder) + start


def shortcuts(home: str | None, roots: Iterable[str]) -> list[tuple[str, str]]:
    """The side list: (label, path), the service's *home* first (label ""
    for the caller to name), then each distinct project root by its
    folder's name (sorted that way, case-insensitively), at most
    SHORTCUTS_MAX in all. A root that is not absolute is dropped, and so
    is the home itself."""
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
    """Why *name* can't be a new folder's name (a msgid, marked `N_` for
    the pot and translated by the caller), or
    None: the service's own rule (`projectfiles.name_error`), run first so
    a typo is not a round trip."""
    verdict = name_error(name.strip())
    if verdict is RenameError.EMPTY:
        return N_("Type a name for the folder")
    if verdict is not None:
        return N_("A folder takes a bare name")
    return None


class Listing:
    """Which `fs.dirs` answer the rows show. Each ask is numbered and an
    answer lands only while it is the newest one (a slow listing of a
    folder the person has typed past is dropped). *folder* is the text
    asked about (what the entry's folder is compared with), *path* the
    folder as the service listed it (`~` expanded: what *Up* and a row's
    click build on)."""

    def __init__(self) -> None:
        self.asked = 0
        self.landed = 0  # the token of the last answer applied
        self.folder: str | None = None
        self.hidden = False
        self.path: str | None = None
        self.rows: list[Folder] = []
        self.truncated = False
        self.error = ""

    @property
    def pending(self) -> bool:
        """An ask is in flight: there are no rows to show, click, complete
        against or make a folder in until it lands (the previous folder's
        are gone with `ask`)."""
        return self.asked != self.landed

    def needs(self, folder: str, hidden: bool) -> bool:
        """Whether the rows are not already *folder*'s with *hidden* as
        asked: a filter alone asks the service nothing."""
        return folder != self.folder or hidden != self.hidden

    def ask(self, folder: str, hidden: bool) -> int:
        """A new listing is asked for: the previous folder's rows, path,
        cut and refusal go now, so nothing acts on them under the new
        folder's name while the answer is on its way (the Fable review of
        PR 624, S1)."""
        self.asked += 1
        self.folder = folder
        self.hidden = hidden
        self.path = None
        self.rows = []
        self.truncated = False
        self.error = ""
        return self.asked

    def land(
        self,
        token: int,
        rows: list[Folder] | None,
        path: str | None = None,
        truncated: bool = False,
        error: str = "",
    ) -> bool:
        """Apply an answer: *rows* and the *path* the service listed, or
        None with *error* for a refusal. Whether it was the newest ask and
        was applied."""
        if token != self.asked:
            return False
        self.landed = token
        self.rows = list(rows or [])
        self.truncated = bool(truncated) and rows is not None
        self.error = error if rows is None else ""
        if rows is None:
            # A folder the service would not list: asked again when the
            # person comes back to it.
            self.folder = None
            self.path = None
        else:
            self.path = path or self.folder
        return True


# -- the three requests (blocking: a worker thread's, `remotefiles.off_main`) --------------


def list_folders(folder: str, hidden: bool) -> tuple[str, list[Folder], bool]:
    """`fs.dirs` of *folder* on the service: the folder as it listed it,
    its subfolders and whether the list was cut. Raises `RequestRefused`
    (a service without the request answers ``unknown``: shown as any
    refusal, D60)."""
    dirs = remotefiles.list_dirs(folder, hidden)
    return dirs.path, folders(dirs.folders), dirs.truncated


def make_folder(folder: str, name: str) -> str:
    """`fs.newdir` of *name* inside *folder*: the new folder's path as the
    service made it. Raises `RequestRefused` (its `reason` one of
    `protocol.FS_MKDIR_REASONS`)."""
    return remotefiles.new_dir(child(folder, name.strip()))


def stat_folder(path: str) -> str | None:
    """The folder at *path* on the service's machine as the service echoed
    it (`~` expanded, normalised, not resolved), or None when nothing, or
    no folder, is there. Raises `RequestRefused`."""
    found = remotefiles.stat_path(path)
    if not found.is_dir:
        return None
    return found.path or path
