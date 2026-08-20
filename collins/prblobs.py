# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""One file's bytes out of a pull request, so the Files view can show a picture.

A diff says what changed in a text file. For an image it says
``Binary files a/icon.png and b/icon.png differ`` — or, for an SVG, a screen
of path data nobody can read as a shape. The Files view renders those as
images instead (`prfileimages` builds the widgets), and this is the half
that goes and gets them: the blob at one commit, saved to a file on disk the
decoder and the lightbox can both work from.

The fetch goes through `gh api` rather than a plain download, for the same
reason every other PR call does: it is already authenticated (a private
repository's raw URL is a 404 to `urllib`), it already knows which host the
user is signed in to (GitHub Enterprise included), and it keeps the whole PR
stack on one transport. The contents endpoint with the ``raw`` media type
hands back the file itself, undecoded — hence `prstatus.gh_bytes`, which is
the only call in the stack that doesn't decode gh's stdout as text.

Everything naming the blob is repository content and is gated before it can
become part of a request: the repository must be ``owner/name``, the ref a
full commit sha (never a branch name — see prdetail._oid), the path a
relative one with no traversal in it, and the suffix one of the image kinds
the viewer can decode. What comes back is capped at `MAX_BLOB_BYTES` and
lands in the cache directory beside the other fetched images, pruned on the
same 24-hour clock.

GTK-free, like prdetail and prstatus beside it: CI runs with no GTK, and the
gates are the half worth testing.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import editorfiles, prdetail, prstatus, remoteimages
from .dropimages import cache_directory, save_copy

# What a blob may weigh before it is dropped unfetched. Smaller than
# remoteimages' 25 MB download cap: this one is an image a *review* is
# looking at, on the action timeout, and a repository that keeps 25 MB
# screenshots in git shouldn't get to stall a page opening on one.
MAX_BLOB_BYTES = 10 * 1024 * 1024

# The gates. A repository is owner/name as GitHub spells one; a ref is a
# commit sha, already held to that shape by prdetail — checked again here
# because this is the module that builds the request.
_REPOSITORY = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
_REF = re.compile(r"[0-9a-f]{7,40}")


# gh's word for what happened to a file (prdetail.PrFile.change_type),
# grouped by which sides of it exist. A rename is in the first list because
# gh's file list carries the new name and not the old one: there is nothing
# to ask the before of.
_NO_BEFORE = ("ADDED", "COPIED", "RENAMED")
_NO_AFTER = ("REMOVED", "DELETED")


class BlobError(Exception):
    """Why a blob didn't arrive, in words a stand-in can carry as a tooltip."""


@dataclass(frozen=True)
class Side:
    """One picture a changed image has: which side of the diff it is, and the
    blob to fetch for it."""

    before: bool  # False: the after-side, at the head commit
    repository: str  # owner/name — a fork's PR fetches its after from the fork
    ref: str  # the commit, never a branch name
    path: str

    @property
    def key(self) -> str:
        """This blob's `cache_key` — what `pictures.fetch` remembers it by."""
        return cache_key(self.repository, self.ref, self.path)


def sides(file: prdetail.PrFile, detail: prdetail.PullRequestDetail) -> tuple[Side, ...]:
    """The pictures *file* has in this PR: before, after, both, or neither.

    Empty for a file that isn't an image by name, and for one whose commits
    the reply didn't carry — a preview must name a commit (an unreadable oid
    reads as none), so a page that can't say which one shows no picture at
    all rather than whatever the branch has since become.
    """
    if not is_image(file.path):
        return ()
    base_repository = detail.summary.repository or ""
    head_repository = detail.head_repository or base_repository
    change = change_type(file)
    found = []
    if base_repository and detail.base_oid and change not in _NO_BEFORE:
        found.append(Side(True, base_repository, detail.base_oid, file.path))
    if head_repository and detail.head_oid and change not in _NO_AFTER:
        found.append(Side(False, head_repository, detail.head_oid, file.path))
    return tuple(found)


def change_type(file: prdetail.PrFile) -> str:
    """gh's changeType for *file*, or the same answer read off its patch when
    the reply didn't carry one.

    git's ``new file mode`` / ``deleted file mode`` header lines say it just
    as plainly, and a stat-only file that says neither is treated as modified
    — both sides asked for, and a before that isn't there degrading to its
    stand-in.
    """
    if file.change_type:
        return file.change_type
    patch = file.patch or ""
    if patch.startswith("new file mode ") or "\nnew file mode " in patch:
        return "ADDED"
    if patch.startswith("deleted file mode ") or "\ndeleted file mode " in patch:
        return "DELETED"
    return "MODIFIED"


def is_image(path: str) -> bool:
    """Whether *path* names a file the Files view should render as a picture.

    By suffix, which is all a diff gives us — the same set the editor's image
    viewer opens (`editorfiles.IMAGE_SUFFIXES`), so anything answered True
    here is something a decoder downstream has a real chance with.
    """
    return image_suffix(path) is not None


def image_suffix(path: str) -> str | None:
    """*path*'s lowercased image suffix, or None when it hasn't got one."""
    if not isinstance(path, str) or not path:
        return None
    suffix = PurePosixPath(path).suffix.lower()
    return suffix if suffix in editorfiles.IMAGE_SUFFIXES else None


def cache_key(repository: str, ref: str, path: str) -> str:
    """The key one blob is remembered under for the run.

    A URL-ish string because `pictures.fetch` keys its cache by one, and
    naming the commit rather than the branch is what makes it safe to keep:
    a push moves the branch, mints a new key, and the next load fetches the
    new bytes instead of showing the old ones.
    """
    return f"gh://{repository}/{ref}/{path}"


def default_directory() -> Path:
    """Where fetched blobs are kept: a sibling of the remote-image downloads,
    under the cache directory, on the same 24-hour prune."""
    return cache_directory() / "pr-blobs"


def fetch_to_file(
    repository: str, ref: str, path: str, directory: Path | None = None
) -> Path:
    """Fetch *path* at commit *ref* of *repository*, and return its file.

    The whole blocking half, so a worker thread has one call to make: gate,
    prune, fetch, save. Raises `BlobError` — a file that isn't there at that
    commit (the before-side of a file the PR adds, a rename's old name) is a
    404 like any other failure, and the caller shows its stand-in. Never call
    on the main thread.
    """
    if directory is None:
        directory = default_directory()
    suffix = image_suffix(path)
    if suffix is None:
        raise BlobError(f"Not an image Collins can show: {path}")
    if not _REPOSITORY.fullmatch(repository or ""):
        raise BlobError(f"Not a repository: {repository}")
    if not _REF.fullmatch(ref or ""):
        raise BlobError(f"Not a commit: {ref}")
    if not _safe_path(path):
        raise BlobError(f"Not a path in the repository: {path}")
    remoteimages.prune_stale(directory)
    data = prstatus.gh_bytes(
        ["api", _endpoint(repository, ref, path),
         "-H", "Accept: application/vnd.github.raw"],
        max_bytes=MAX_BLOB_BYTES,
    )
    if data is None:
        raise BlobError(f"GitHub wouldn't hand over {path} at {ref[:7]}")
    if not data:
        raise BlobError(f"GitHub sent an empty file for {path}")
    try:
        return save_copy(data, directory, "pr-blob", suffix)
    except OSError as error:
        raise BlobError(f"Couldn't save what GitHub sent: {error}") from None


def _endpoint(repository: str, ref: str, path: str) -> str:
    """The contents-API path for one blob, every segment percent-encoded.

    The path travels as one encoded query-free segment run: a file called
    ``a b#c.png`` is a real name, and it must reach GitHub as itself rather
    than as a fragment that truncates the request.
    """
    quoted = urllib.parse.quote(path, safe="/")
    return f"repos/{repository}/contents/{quoted}?ref={ref}"


def _safe_path(path: str) -> bool:
    """Whether *path* is a plain relative path inside the repository."""
    if not path or len(path) > 500 or path.startswith("/"):
        return False
    if "\\" in path or "\x00" in path:
        return False
    return ".." not in PurePosixPath(path).parts
