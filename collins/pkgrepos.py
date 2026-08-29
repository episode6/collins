"""The package channel this machine could be getting Collins from, and isn't.

A Collins that came from the GitHub `.deb`, from PyPI or from a checkout
updates only when someone remembers to. On a distro we publish a repository
for, there is a better way in, and the sidebar menu offers it: one item,
"Add the Ubuntu PPA…", that runs the two commands the docs give. This module
decides whether that item belongs in the menu — is this distro one we have a
channel for, and is the channel already configured? — and names the channel's
commands. Running them is the window's job (they need sudo, so they run in a
terminal the user can answer).

Two channels: the Ubuntu PPA and the Fedora COPR. Debian (once it has a
repository of its own — Launchpad only serves Ubuntu) would be the third,
and each is a `Channel` entry in CHANNELS with its own `applies` and
`configured` checks; the menu and the window handle every entry the same
way.

Nothing here imports gi, so it is testable where GTK isn't.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# The Launchpad PPA, as apt's source files name it. `add-apt-repository`
# writes a deb822 `.sources` file on 23.10+ and a one-line `.list` before;
# the host moved from ppa.launchpad.net to ppa.launchpadcontent.net in 2022
# and both still resolve, so a file written either way counts.
PPA = "ppa:episode6/stable"
_PPA_URI = re.compile(r"ppa\.launchpad(?:content)?\.net/episode6/stable/")

# The COPR, as `dnf copr enable` writes it: a .repo file under yum.repos.d
# (named _copr:copr.fedorainfracloud.org:episode6:stable.repo by current dnf,
# _copr_episode6-stable.repo by older ones) whose baseurl and gpgkey both
# point under this path. The URL is what identifies it, whatever the file
# is called.
COPR = "episode6/stable"
_COPR_URI = re.compile(r"copr\.fedorainfracloud\.org/results/episode6/stable/")

OS_RELEASE = Path("/etc/os-release")
APT_SOURCES = Path("/etc/apt/sources.list")
APT_SOURCES_DIR = Path("/etc/apt/sources.list.d")
YUM_REPOS_DIR = Path("/etc/yum.repos.d")
# Present on every image-based (rpm-ostree / bootc) system: Silverblue,
# Kinoite and the other Fedora Atomic desktops, Bazzite and the rest of
# Universal Blue, CoreOS, IoT. They say ID=fedora too, but `dnf install`
# doesn't install anything there.
OSTREE_BOOTED = Path("/run/ostree-booted")
# The atomic desktops' VARIANT_IDs, for when the marker can't be consulted
# (the tests) or a spin forgot to set it.
_ATOMIC_VARIANTS = frozenset({"silverblue", "kinoite", "sericea", "onyx", "coreos", "iot"})


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


def is_fedora(release: dict[str, str], ostree_booted: Path | None = None) -> bool:
    """Fedora itself, any release — the COPR follows Fedora's branching, so
    every maintained one has a chroot — and the rest of the family from
    version 10 on: RHEL, CentOS Stream, AlmaLinux and Rocky (all of which say
    ID_LIKE=rhel centos fedora, or are rhel/centos themselves), plus Fedora
    derivatives like Nobara and Ultramarine, whose Fedora-numbered versions
    clear 10 by a mile. The floor is RHEL 10's: the first EL whose base repos
    carry Collins' GTK stack, and the only EPEL the COPR builds for — RHEL 9
    would get a chroot-not-found from `dnf copr enable`.

    Not the image-based variants, though: Silverblue and friends report
    ID=fedora, but packages go on through rpm-ostree (and a reboot), so the
    `dnf install` this channel offers would fail there. Any ostree-booted
    system is out, whatever its os-release says."""
    if (ostree_booted or OSTREE_BOOTED).exists():
        return False
    variant = release.get("VARIANT_ID", "").lower()
    if variant in _ATOMIC_VARIANTS or "atomic" in variant:
        return False
    family = _distro_family(release)
    if release.get("ID", "").lower() == "fedora":
        return True
    if not family & {"fedora", "rhel", "centos"}:
        return False
    major = release.get("VERSION_ID", "").split(".")[0]
    return major.isdigit() and int(major) >= 10


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


def _names_copr(text: str) -> bool:
    """Whether one .repo file enables the COPR.

    An ini file, one repository per [section]; `dnf copr disable` sets
    enabled=0 in place rather than deleting the file (and `dnf copr enable`
    flips it back), so a disabled section must read as not configured or the
    menu would never offer to turn it back on.
    """
    for section in re.split(r"^\[", text, flags=re.MULTILINE):
        lines = [line for line in section.splitlines() if not line.lstrip().startswith(("#", ";"))]
        if not any(_COPR_URI.search(line) for line in lines):
            continue
        enabled = next(
            (
                line.partition("=")[2].strip().lower()
                for line in lines
                if line.partition("=")[0].strip().lower() == "enabled"
            ),
            "1",
        )
        if enabled not in ("0", "no", "false"):
            return True
    return False


def copr_configured(repos_dir: Path | None = None) -> bool:
    """Whether dnf already pulls from the episode6/stable COPR."""
    try:
        files = sorted((repos_dir or YUM_REPOS_DIR).iterdir())
    except OSError:
        return False
    for path in files:
        if path.suffix != ".repo" or not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if _names_copr(text):
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
# The same recipe for dnf: the copr plugin picks the chroot from os-release
# (fedora-<N> on Fedora, epel-<N> on the RHEL family), so the one line serves
# every distro `is_fedora` admits.
COPR_COMMANDS = f"sudo dnf copr enable {COPR} && sudo dnf install collins"

CHANNELS: tuple[Channel, ...] = (
    Channel(
        id="ubuntu-ppa",
        applies=is_ubuntu,
        configured=ppa_configured,
        commands=PPA_COMMANDS,
    ),
    Channel(
        id="fedora-copr",
        applies=is_fedora,
        configured=copr_configured,
        commands=COPR_COMMANDS,
    ),
    # Next: a Debian repository (Channel "debian-apt"), once there is one to
    # point at.
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
