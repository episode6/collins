from pathlib import Path

import pytest

from collins import pkgrepos

UBUNTU = 'NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nVERSION_CODENAME=resolute\n'
MINT = 'ID=linuxmint\nID_LIKE="ubuntu debian"\n'
DEBIAN = 'ID=debian\nVERSION_CODENAME=trixie\n'
FEDORA = "ID=fedora\nVERSION_ID=42\n"
CENTOS10 = 'ID="centos"\nID_LIKE="rhel fedora"\nVERSION_ID="10"\n'
ALMA10 = 'ID="almalinux"\nID_LIKE="rhel centos fedora"\nVERSION_ID="10.1"\n'
RHEL9 = 'ID="rhel"\nID_LIKE="fedora"\nVERSION_ID="9.6"\n'
NOBARA = "ID=nobara\nID_LIKE=fedora\nVERSION_ID=43\n"
SILVERBLUE = "ID=fedora\nVERSION_ID=44\nVARIANT_ID=silverblue\n"
KINOITE = 'ID=fedora\nVERSION_ID=44\nVARIANT_ID="kinoite"\n'

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


@pytest.mark.parametrize("text", [DEBIAN, ""])
def test_offer_is_nothing_on_a_distro_without_a_channel(monkeypatch, tmp_path, text):
    no_ppa(monkeypatch, tmp_path, configured=False)
    assert pkgrepos.offer(pkgrepos.os_release(write_release(tmp_path, text))) is None


def test_offer_reads_the_real_os_release_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(pkgrepos, "OS_RELEASE", write_release(tmp_path, UBUNTU))
    no_ppa(monkeypatch, tmp_path, configured=False)
    assert pkgrepos.offer().id == "ubuntu-ppa"


COPR_REPO = """[copr:copr.fedorainfracloud.org:episode6:stable]
name=Copr repo for stable owned by episode6
baseurl=https://download.copr.fedorainfracloud.org/results/episode6/stable/fedora-$releasever-$basearch/
type=rpm-md
skip_if_unavailable=True
gpgcheck=1
gpgkey=https://download.copr.fedorainfracloud.org/results/episode6/stable/pubkey.gpg
repo_gpgcheck=0
enabled=1
enabled_metadata=1
"""


@pytest.mark.parametrize(
    "text, expected",
    [
        (FEDORA, True),
        (NOBARA, True),
        (CENTOS10, True),
        (ALMA10, True),
        (RHEL9, False),
        (SILVERBLUE, False),
        (KINOITE, False),
        (UBUNTU, False),
        (DEBIAN, False),
        ("", False),
    ],
)
def test_is_fedora_is_fedora_and_el10(tmp_path, text, expected):
    release = pkgrepos.os_release(write_release(tmp_path, text))
    assert pkgrepos.is_fedora(release, ostree_booted=tmp_path / "not-ostree") is expected


def test_is_fedora_excludes_any_ostree_booted_system(tmp_path):
    """Bazzite, Aurora and the rest of Universal Blue don't say fedora in
    VARIANT_ID; the boot marker is what rules them out."""
    marker = tmp_path / "ostree-booted"
    marker.touch()
    bazzite = 'ID=bazzite\nID_LIKE="fedora"\nVERSION_ID=44\nVARIANT_ID=bazzite\n'
    for text in (FEDORA, bazzite, CENTOS10):
        release = pkgrepos.os_release(write_release(tmp_path, text))
        assert pkgrepos.is_fedora(release, ostree_booted=marker) is False
        assert pkgrepos.is_fedora(release, ostree_booted=tmp_path / "gone") is True


def yum(tmp_path: Path, **files: str) -> Path:
    repos_dir = tmp_path / "yum.repos.d"
    repos_dir.mkdir()
    for name, text in files.items():
        (repos_dir / name).write_text(text)
    return repos_dir


def test_copr_configured_reads_the_repo_file(tmp_path):
    assert pkgrepos.copr_configured(
        yum(tmp_path, **{"_copr:copr.fedorainfracloud.org:episode6:stable.repo": COPR_REPO})
    )


def test_copr_configured_by_url_whatever_the_file_is_called(tmp_path):
    assert pkgrepos.copr_configured(yum(tmp_path, **{"_copr_episode6-stable.repo": COPR_REPO}))


def test_copr_disabled_is_not_configured(tmp_path):
    disabled = COPR_REPO.replace("enabled=1\n", "enabled=0\n")
    assert not pkgrepos.copr_configured(yum(tmp_path, **{"episode6.repo": disabled}))


def test_copr_other_projects_and_non_repo_files_do_not_count(tmp_path):
    other = COPR_REPO.replace("episode6/stable", "someone/else")
    assert not pkgrepos.copr_configured(
        yum(tmp_path, **{"other.repo": other, "notes.txt": COPR_REPO, "fedora.repo": "[fedora]\nenabled=1\n"})
    )


def test_copr_configured_missing_dir(tmp_path):
    assert not pkgrepos.copr_configured(tmp_path / "nope")


def test_offer_fedora_copr(tmp_path, monkeypatch):
    release = pkgrepos.os_release(write_release(tmp_path, FEDORA))
    monkeypatch.setattr(pkgrepos, "YUM_REPOS_DIR", tmp_path / "empty")
    monkeypatch.setattr(pkgrepos, "OSTREE_BOOTED", tmp_path / "not-ostree")
    channel = pkgrepos.offer(release)
    assert channel is not None and channel.id == "fedora-copr"
    assert channel.commands == "sudo dnf copr enable episode6/stable && sudo dnf install collins"
    monkeypatch.setattr(pkgrepos, "YUM_REPOS_DIR", yum(tmp_path, **{"e6.repo": COPR_REPO}))
    assert pkgrepos.offer(release) is None
