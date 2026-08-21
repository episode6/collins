from pathlib import Path

import pytest

from collins import pkgrepos

UBUNTU = 'NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nVERSION_CODENAME=resolute\n'
MINT = 'ID=linuxmint\nID_LIKE="ubuntu debian"\n'
DEBIAN = 'ID=debian\nVERSION_CODENAME=trixie\n'
FEDORA = "ID=fedora\nVERSION_ID=42\n"

DEB822 = """Types: deb
URIs: https://ppa.launchpadcontent.net/episode6/stable/ubuntu/
Suites: resolute
Components: main
Signed-By: /etc/apt/keyrings/episode6-ubuntu-stable.gpg
"""
LEGACY_LIST = "deb http://ppa.launchpad.net/episode6/stable/ubuntu noble main\n"


def write_release(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "os-release"
    path.write_text(text)
    return path


def test_os_release_parses_and_unquotes(tmp_path):
    fields = pkgrepos.os_release(write_release(tmp_path, UBUNTU + "# comment\n\nBARE=x\n"))
    assert fields["NAME"] == "Ubuntu"
    assert fields["ID"] == "ubuntu"
    assert fields["BARE"] == "x"
    assert "# comment" not in fields


def test_os_release_missing_is_empty(tmp_path):
    assert pkgrepos.os_release(tmp_path / "nope") == {}


@pytest.mark.parametrize(
    "text, expected",
    [(UBUNTU, True), (MINT, True), (DEBIAN, False), (FEDORA, False), ("", False)],
)
def test_is_ubuntu_covers_derivatives(tmp_path, text, expected):
    assert pkgrepos.is_ubuntu(pkgrepos.os_release(write_release(tmp_path, text))) is expected


def apt(tmp_path: Path, **files: str) -> tuple[Path, Path]:
    sources = tmp_path / "sources.list"
    sources_dir = tmp_path / "sources.list.d"
    sources_dir.mkdir()
    for name, text in files.items():
        (sources_dir / name).write_text(text)
    return sources, sources_dir


def test_ppa_configured_reads_deb822(tmp_path):
    assert pkgrepos.ppa_configured(*apt(tmp_path, **{"episode6.sources": DEB822}))


def test_ppa_configured_reads_legacy_list(tmp_path):
    assert pkgrepos.ppa_configured(*apt(tmp_path, **{"episode6.list": LEGACY_LIST}))


def test_ppa_configured_reads_main_sources_list(tmp_path):
    sources, sources_dir = apt(tmp_path)
    sources.write_text("deb http://archive.ubuntu.com/ubuntu noble main\n" + LEGACY_LIST)
    assert pkgrepos.ppa_configured(sources, sources_dir)


def test_ppa_configured_ignores_other_ppas_and_junk(tmp_path):
    files = {
        "other.sources": DEB822.replace("episode6/stable", "mozillateam/ppa"),
        "notes.txt": LEGACY_LIST,  # apt reads only .list and .sources
        "backup.list.save": LEGACY_LIST,
    }
    assert not pkgrepos.ppa_configured(*apt(tmp_path, **files))


def test_ppa_configured_false_with_no_apt_at_all(tmp_path):
    assert not pkgrepos.ppa_configured(tmp_path / "sources.list", tmp_path / "sources.list.d")


def test_disabled_entries_do_not_count(tmp_path):
    files = {
        "commented.list": "# " + LEGACY_LIST,
        "off.sources": DEB822 + "Enabled: no\n",
    }
    assert not pkgrepos.ppa_configured(*apt(tmp_path, **files))


def test_a_disabled_stanza_beside_an_enabled_one_counts(tmp_path):
    text = DEB822 + "Enabled: no\n\n" + DEB822
    assert pkgrepos.ppa_configured(*apt(tmp_path, **{"two.sources": text}))


def no_ppa(monkeypatch, tmp_path, configured: bool) -> None:
    """Point the module's apt paths at a tmp tree, with or without the PPA."""
    sources, sources_dir = apt(tmp_path, **({"e6.sources": DEB822} if configured else {}))
    monkeypatch.setattr(pkgrepos, "APT_SOURCES", sources)
    monkeypatch.setattr(pkgrepos, "APT_SOURCES_DIR", sources_dir)


def test_offer_is_the_ppa_on_ubuntu_without_it(monkeypatch, tmp_path):
    no_ppa(monkeypatch, tmp_path, configured=False)
    channel = pkgrepos.offer(pkgrepos.os_release(write_release(tmp_path, UBUNTU)))
    assert channel is not None and channel.id == "ubuntu-ppa"
    assert channel.commands == (
        "sudo add-apt-repository ppa:episode6/stable && sudo apt install collins"
    )
    # One line: a second line typed while sudo prompts becomes the password.
    assert "\n" not in channel.commands


def test_offer_is_nothing_once_configured(monkeypatch, tmp_path):
    no_ppa(monkeypatch, tmp_path, configured=True)
    assert pkgrepos.offer(pkgrepos.os_release(write_release(tmp_path, UBUNTU))) is None


@pytest.mark.parametrize("text", [DEBIAN, FEDORA, ""])
def test_offer_is_nothing_off_ubuntu_for_now(monkeypatch, tmp_path, text):
    no_ppa(monkeypatch, tmp_path, configured=False)
    assert pkgrepos.offer(pkgrepos.os_release(write_release(tmp_path, text))) is None


def test_offer_reads_the_real_os_release_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(pkgrepos, "OS_RELEASE", write_release(tmp_path, UBUNTU))
    no_ppa(monkeypatch, tmp_path, configured=False)
    assert pkgrepos.offer().id == "ubuntu-ppa"
