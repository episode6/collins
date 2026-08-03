# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""File-type icon and color lookup for the editor's file tree.

GTK-free (like editorfiles.py) so the mapping stays unit-testable headless;
filetree.py owns turning the answer into `Gtk.Image` calls. The icons are the
bundled `ft-*-symbolic` Octicons (data/icons); the color names are CSS classes
whose actual values live in app.py's scheme-following provider, one shade per
light/dark, inspired by VS Code's Seti file-icon theme.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# The palette's CSS class names (app.py defines the colors). None means the
# icon keeps the row's normal foreground, like any other symbolic icon.
_BLUE = "ft-blue"
_YELLOW = "ft-yellow"
_ORANGE = "ft-orange"
_GREEN = "ft-green"
_RED = "ft-red"
_PURPLE = "ft-purple"
_PINK = "ft-pink"
_GREY = "ft-grey"

_CODE = "ft-file-code-symbolic"
_MARKUP = "ft-code-symbolic"
_MARKDOWN = "ft-markdown-symbolic"
_IMAGE = "ft-image-symbolic"
_VIDEO = "ft-video-symbolic"
_ARCHIVE = "ft-file-zip-symbolic"
_BINARY = "ft-file-binary-symbolic"
_DATABASE = "ft-database-symbolic"
_GEAR = "ft-gear-symbolic"
_TERMINAL = "ft-terminal-symbolic"
_BOOK = "ft-book-symbolic"
_LAW = "ft-law-symbolic"
_LOCK = "ft-lock-symbolic"
_PACKAGE = "ft-package-symbolic"
_CONTAINER = "ft-container-symbolic"
_TABLE = "ft-table-symbolic"
_GIT = "ft-diff-ignored-symbolic"
_FILE = "ft-file-symbolic"

_DEFAULT = (_FILE, None)

# Exact (casefolded) filenames first: manifests, lockfiles, tooling files.
# These outrank the suffix table — package.json is a manifest before it is
# JSON — mirroring how VS Code's icon themes resolve names before extensions.
_NAMES: dict[str, tuple[str, str | None]] = {
    "package.json": (_PACKAGE, _RED),
    "package-lock.json": (_LOCK, _GREY),
    "npm-shrinkwrap.json": (_LOCK, _GREY),
    "yarn.lock": (_LOCK, _GREY),
    "pnpm-lock.yaml": (_LOCK, _GREY),
    "pyproject.toml": (_PACKAGE, _BLUE),
    "cargo.toml": (_PACKAGE, _ORANGE),
    "go.mod": (_PACKAGE, _BLUE),
    "go.sum": (_LOCK, _GREY),
    "gemfile": (_PACKAGE, _RED),
    "pom.xml": (_PACKAGE, _ORANGE),
    "build.gradle": (_PACKAGE, _GREEN),
    "build.gradle.kts": (_PACKAGE, _GREEN),
    "settings.gradle": (_PACKAGE, _GREEN),
    "settings.gradle.kts": (_PACKAGE, _GREEN),
    "makefile": (_GEAR, _ORANGE),
    "gnumakefile": (_GEAR, _ORANGE),
    "justfile": (_GEAR, _ORANGE),
    "cmakelists.txt": (_GEAR, _ORANGE),
    "meson.build": (_GEAR, _ORANGE),
    ".gitignore": (_GIT, _ORANGE),
    ".gitattributes": (_GIT, _ORANGE),
    ".gitmodules": (_GIT, _ORANGE),
    ".dockerignore": (_CONTAINER, _GREY),
    ".editorconfig": (_GEAR, _GREY),
}

# Prefix rules, tried after exact names: README.md, LICENSE-MIT, COPYING,
# Dockerfile.debug and friends all keep their identity past the suffix.
_PREFIXES: tuple[tuple[str, tuple[str, str | None]], ...] = (
    ("readme", (_BOOK, _BLUE)),
    ("license", (_LAW, _YELLOW)),
    ("copying", (_LAW, _YELLOW)),
    ("notice", (_LAW, _YELLOW)),
    ("dockerfile", (_CONTAINER, _BLUE)),
    ("containerfile", (_CONTAINER, _BLUE)),
    (".env", (_GEAR, _YELLOW)),
)

_SUFFIXES: dict[str, tuple[str, str | None]] = {
    # code
    ".py": (_CODE, _BLUE),
    ".pyi": (_CODE, _BLUE),
    ".js": (_CODE, _YELLOW),
    ".mjs": (_CODE, _YELLOW),
    ".cjs": (_CODE, _YELLOW),
    ".jsx": (_CODE, _YELLOW),
    ".ts": (_CODE, _BLUE),
    ".tsx": (_CODE, _BLUE),
    ".go": (_CODE, _BLUE),
    ".rs": (_CODE, _ORANGE),
    ".rb": (_CODE, _RED),
    ".erb": (_CODE, _RED),
    ".java": (_CODE, _RED),
    ".kt": (_CODE, _PURPLE),
    ".kts": (_CODE, _PURPLE),
    ".c": (_CODE, _BLUE),
    ".h": (_CODE, _BLUE),
    ".cpp": (_CODE, _BLUE),
    ".cc": (_CODE, _BLUE),
    ".cxx": (_CODE, _BLUE),
    ".hpp": (_CODE, _BLUE),
    ".cs": (_CODE, _PURPLE),
    ".php": (_CODE, _PURPLE),
    ".swift": (_CODE, _ORANGE),
    ".scala": (_CODE, _RED),
    ".lua": (_CODE, _BLUE),
    ".r": (_CODE, _BLUE),
    ".dart": (_CODE, _BLUE),
    ".vue": (_CODE, _GREEN),
    ".svelte": (_CODE, _ORANGE),
    ".gradle": (_CODE, _GREEN),
    ".groovy": (_CODE, _GREEN),
    ".css": (_CODE, _BLUE),
    ".scss": (_CODE, _PINK),
    ".sass": (_CODE, _PINK),
    ".less": (_CODE, _PURPLE),
    ".json": (_CODE, _YELLOW),
    ".jsonc": (_CODE, _YELLOW),
    ".json5": (_CODE, _YELLOW),
    ".ipynb": (_CODE, _ORANGE),
    # markup
    ".html": (_MARKUP, _ORANGE),
    ".htm": (_MARKUP, _ORANGE),
    ".xml": (_MARKUP, _ORANGE),
    # prose
    ".md": (_MARKDOWN, _BLUE),
    ".markdown": (_MARKDOWN, _BLUE),
    ".rst": (_BOOK, _BLUE),
    ".txt": (_FILE, None),
    ".pdf": (_FILE, _RED),
    ".doc": (_FILE, _BLUE),
    ".docx": (_FILE, _BLUE),
    ".odt": (_FILE, _BLUE),
    # shell
    ".sh": (_TERMINAL, _GREEN),
    ".bash": (_TERMINAL, _GREEN),
    ".zsh": (_TERMINAL, _GREEN),
    ".fish": (_TERMINAL, _GREEN),
    ".ps1": (_TERMINAL, _BLUE),
    ".bat": (_TERMINAL, _GREEN),
    ".cmd": (_TERMINAL, _GREEN),
    # config
    ".yaml": (_GEAR, _PURPLE),
    ".yml": (_GEAR, _PURPLE),
    ".toml": (_GEAR, _ORANGE),
    ".ini": (_GEAR, _GREY),
    ".cfg": (_GEAR, _GREY),
    ".conf": (_GEAR, _GREY),
    ".properties": (_GEAR, _GREY),
    ".desktop": (_GEAR, _GREY),
    # data
    ".sql": (_DATABASE, _PINK),
    ".db": (_DATABASE, _PINK),
    ".sqlite": (_DATABASE, _PINK),
    ".sqlite3": (_DATABASE, _PINK),
    ".csv": (_TABLE, _GREEN),
    ".tsv": (_TABLE, _GREEN),
    ".xls": (_TABLE, _GREEN),
    ".xlsx": (_TABLE, _GREEN),
    ".ods": (_TABLE, _GREEN),
    ".log": (_FILE, _GREY),
    # images and media
    ".png": (_IMAGE, _PURPLE),
    ".jpg": (_IMAGE, _PURPLE),
    ".jpeg": (_IMAGE, _PURPLE),
    ".gif": (_IMAGE, _PURPLE),
    ".webp": (_IMAGE, _PURPLE),
    ".bmp": (_IMAGE, _PURPLE),
    ".ico": (_IMAGE, _PURPLE),
    ".tiff": (_IMAGE, _PURPLE),
    ".avif": (_IMAGE, _PURPLE),
    ".svg": (_IMAGE, _ORANGE),
    ".mp4": (_VIDEO, _PINK),
    ".mkv": (_VIDEO, _PINK),
    ".webm": (_VIDEO, _PINK),
    ".mov": (_VIDEO, _PINK),
    ".avi": (_VIDEO, _PINK),
    ".mp3": (_VIDEO, _PINK),
    ".wav": (_VIDEO, _PINK),
    ".ogg": (_VIDEO, _PINK),
    ".flac": (_VIDEO, _PINK),
    ".m4a": (_VIDEO, _PINK),
    # archives
    ".zip": (_ARCHIVE, _GREY),
    ".tar": (_ARCHIVE, _GREY),
    ".gz": (_ARCHIVE, _GREY),
    ".tgz": (_ARCHIVE, _GREY),
    ".bz2": (_ARCHIVE, _GREY),
    ".xz": (_ARCHIVE, _GREY),
    ".7z": (_ARCHIVE, _GREY),
    ".rar": (_ARCHIVE, _GREY),
    ".deb": (_ARCHIVE, _GREY),
    ".rpm": (_ARCHIVE, _GREY),
    ".jar": (_ARCHIVE, _GREY),
    ".whl": (_ARCHIVE, _GREY),
    # binaries
    ".exe": (_BINARY, _GREY),
    ".dll": (_BINARY, _GREY),
    ".so": (_BINARY, _GREY),
    ".o": (_BINARY, _GREY),
    ".a": (_BINARY, _GREY),
    ".bin": (_BINARY, _GREY),
    ".class": (_BINARY, _GREY),
    ".pyc": (_BINARY, _GREY),
    ".pyd": (_BINARY, _GREY),
    ".dylib": (_BINARY, _GREY),
    ".wasm": (_BINARY, _GREY),
    ".ttf": (_BINARY, _GREY),
    ".otf": (_BINARY, _GREY),
    ".woff": (_BINARY, _GREY),
    ".woff2": (_BINARY, _GREY),
}


def icon_for(name: str, is_dir: bool = False) -> tuple[str, str | None]:
    """The `(icon_name, color_class)` a file-tree row should show for *name*
    (a bare filename, no directory part). Resolution order: exact name, then
    name prefix, then `*.lock`, then suffix — so `package.json` is a manifest
    before it is JSON, and `LICENSE-MIT` a license before an unknown suffix.
    Unknowns get the plain-file icon uncolored; directories all keep the
    stock folder icon."""
    if is_dir:
        return ("folder-symbolic", None)
    folded = name.casefold()
    if folded in _NAMES:
        return _NAMES[folded]
    for prefix, result in _PREFIXES:
        if folded.startswith(prefix):
            return result
    if folded.endswith(".lock"):
        return (_LOCK, _GREY)
    suffix = PurePosixPath(folded).suffix
    return _SUFFIXES.get(suffix, _DEFAULT)
