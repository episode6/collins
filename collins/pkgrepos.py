"""The package channel this machine could be getting Collins from, and isn't.

A Collins that came from the GitHub `.deb`, from PyPI or from a checkout
updates only when someone remembers to. On a distro we publish a repository
for, there is a better way in, and the sidebar menu offers it: one item,
"Add the Ubuntu PPA…", that runs the two commands the docs give. This module
decides whether that item belongs in the menu — is this distro one we have a
channel for, and is the channel already configured? — and names the channel's
commands. Running them is the window's job (they need sudo, so they run in a
terminal the user can answer).

One channel today, the Ubuntu PPA. Debian (once it has a repository of its
own — Launchpad only serves Ubuntu) and Fedora (once there are rpms) are the
next two, and each is a `Channel` entry in CHANNELS with its own `applies`
and `configured` checks; the menu and the window handle every entry the same
way.

Nothing here imports gi, so it is testable where GTK isn't.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# The Launchpad PPA, as apt's source files name it. `add-apt-repository`
# writes a deb822 `.sources` file on 23.10+ and a one-line `.list` before;
# the host moved from ppa.launchpad.net to ppa.launchpadcontent.net in 2022
# and both still resolve, so a file written either way counts.
PPA = "ppa:episode6/stable"
_PPA_URI = re.compile(r"ppa\.launchpad(?:content)?\.net/episode6/stable/")

OS_RELEASE = Path("/etc/os-release")
APT_SOURCES = Path("/etc/apt/sources.list")
APT_SOURCES_DIR = Path("/etc/apt/sources.list.d")


@dataclass(frozen=True)
class Channel:
    """A repository Collins ships through, and how to tell whether this
    machine already has it.

    `commands` is what a user runs to get on the channel, as one shell line
    — shown in the dialog verbatim and typed into a terminal as-is.
    """

    id: str
    applies: Callable[[dict[str, str]], bool]
    configured: Callable[[], bool]
    commands: str


def os_release(path: Path | None = None) -> dict[str, str]:
    """/etc/os-release as a dict: KEY=value lines, quotes stripped.

    Empty when the file is missing — macOS, or a container image stripped to
    the bone — which every `applies` check reads as "not mine".
    """
    try:
        text = (path or OS_RELEASE).read_text()
    except OSError:
        return {}
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip()] = value
    return fields


def _distro_family(release: dict[str, str]) -> set[str]:
    """ID plus everything in ID_LIKE: {"ubuntu", "debian"} for Ubuntu itself
    and for Mint, Pop!_OS, elementary and Zorin, which all say ID_LIKE=ubuntu
    (some with debian after it) and all take a PPA."""
    ids = {release.get("ID", "").lower()}
    ids.update(release.get("ID_LIKE", "").lower().split())
    ids.discard("")
    return ids


def is_ubuntu(release: dict[str, str]) -> bool:
    return "ubuntu" in _distro_family(release)


def _apt_source_files(sources: Path, sources_dir: Path) -> list[Path]:
    files = [sources]
    try:
        files.extend(sorted(sources_dir.iterdir()))
    except OSError:
        pass
    return [f for f in files if f.suffix in (".list", ".sources") and f.is_file()]


def _names_ppa(text: str, suffix: str) -> bool:
    """Whether one apt source file enables the PPA.

    A `.list` file is one source per line, a leading `#` disabling it. A
    deb822 `.sources` file is stanzas separated by blank lines, one disabled
    by an `Enabled: no` field — `apt` and the Software & Updates dialog both
    turn a PPA off that way rather than deleting the file, so a disabled
    stanza must read as not configured or the menu would never offer to turn
    it back on.
    """
    if suffix == ".list":
        return any(
            _PPA_URI.search(line) and not line.lstrip().startswith("#")
            for line in text.splitlines()
        )
    for stanza in re.split(r"\n\s*\n", text):
        lines = [line for line in stanza.splitlines() if not line.lstrip().startswith("#")]
        if not any(_PPA_URI.search(line) for line in lines):
            continue
        enabled = next(
            (line.partition(":")[2].strip().lower() for line in lines if line.lower().startswith("enabled:")),
            "yes",
        )
        if enabled not in ("no", "false", "0"):
            return True
    return False


def ppa_configured(sources: Path | None = None, sources_dir: Path | None = None) -> bool:
    """Whether apt already pulls from ppa:episode6/stable."""
    for path in _apt_source_files(sources or APT_SOURCES, sources_dir or APT_SOURCES_DIR):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if _names_ppa(text, path.suffix):
            return True
    return False


# The docs' install recipe (docs/index.md, docs/guide/getting-started.md):
# adding the PPA alone changes nothing until the package is installed from
# it, and on a machine running the GitHub .deb the install is the upgrade
# onto the channel. One line joined by &&, not the docs' two: the commands
# are typed into a shell, and a second line arriving while sudo waits for a
# password is read *as* the password — the first one fails to authenticate
# and the second never runs.
PPA_COMMANDS = f"sudo add-apt-repository {PPA} && sudo apt install collins"

CHANNELS: tuple[Channel, ...] = (
    Channel(
        id="ubuntu-ppa",
        applies=is_ubuntu,
        configured=ppa_configured,
        commands=PPA_COMMANDS,
    ),
    # Next: a Debian repository (Channel "debian-apt") and a Fedora COPR
    # ("fedora-copr"), once those channels exist to point at.
)


def offer(release: dict[str, str] | None = None) -> Channel | None:
    """The channel the sidebar menu should offer, or None.

    The first channel whose distro this is and whose repository this machine
    doesn't have yet. Decided once at launch, like the desktop-icon offer: the
    answer only changes when someone adds the repository, and the window that
    runs the commands for them drops the item itself.
    """
    if release is None:
        release = os_release()
    for channel in CHANNELS:
        if channel.applies(release) and not channel.configured():
            return channel
    return None
