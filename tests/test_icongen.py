"""Tests for icon generation prompt/reply handling (collins.icongen)."""

from collins import icongen, projecticons

_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><rect/></svg>'


# -- extract_svg --------------------------------------------------------------


def test_extract_bare_reply():
    assert icongen.extract_svg(_SVG) == _SVG.encode()


def test_extract_from_fenced_prose():
    reply = f"Here is your icon:\n```svg\n{_SVG}\n```\nEnjoy!"
    assert icongen.extract_svg(reply) == _SVG.encode()


def test_extract_nothing():
    assert icongen.extract_svg("I could not design an icon, sorry.") is None
    assert icongen.extract_svg("") is None


def test_extract_truncated_document():
    assert icongen.extract_svg('<svg xmlns="…"><rect/>') is None


def test_extract_closing_tag_before_opening():
    assert icongen.extract_svg("</svg> stray text <svg") is None


def test_extract_rejects_oversized():
    fat = _SVG.replace("<rect/>", "<rect/>" * 40_000)
    assert len(fat) > 256 * 1024
    assert icongen.extract_svg(fat) is None


def test_extract_rejects_active_content():
    # The prompt's "no scripts, no external URLs" is an instruction to a
    # model reading untrusted repo text; this is where it is enforced.
    for payload in (
        "<script>alert(1)</script>",
        '<image href="https://evil.example/x.png"/>',
        '<use xlink:href="https://evil.example/x.svg#a"/>',
        '<rect style="fill:url(http://evil.example/f)"/>',
        '<rect onclick="steal()"/>',
        "<style>@import url(https://evil.example/x.css)</style>",
    ):
        assert icongen.extract_svg(_SVG.replace("<rect/>", payload)) is None


def test_extract_rejects_data_uris():
    # Hand-shipped on-disk icons may embed data:image/* rasters, but a
    # generated icon is pure vector art: every data: href is refused here
    # even though usable_icon_bytes would let the image one through.
    for payload in (
        '<image href="data:image/png;base64,iVBORw0KGgo="/>',
        '<image xlink:href="data:image/png;base64,iVBORw0KGgo="/>',
        '<image href="data:text/html,x"/>',
    ):
        doc = _SVG.replace("<rect/>", payload)
        assert icongen.extract_svg(doc) is None
    assert projecticons.usable_icon_bytes(
        _SVG.replace("<rect/>", '<image href="data:image/png;base64,iVBORw0KGgo="/>').encode()
    )


def test_extract_keeps_internal_references():
    # Inline gradients and <use> reuse point at local fragments — the one
    # kind of reference a self-contained icon legitimately makes. The root
    # element's xmlns URL (in _SVG already) passes too.
    good = _SVG.replace(
        "<rect/>",
        '<defs><linearGradient id="g"/></defs><rect fill="url(#g)"/><use xlink:href="#g"/>',
    )
    assert icongen.extract_svg(good) == good.encode()


# -- build_prompt -------------------------------------------------------------


def test_prompt_names_project_and_lists_entries(tmp_path):
    (tmp_path / "main.py").write_text("print()")
    (tmp_path / "src").mkdir()
    (tmp_path / ".hidden").write_text("secret")
    prompt = icongen.build_prompt(tmp_path, "rocketeer")
    assert "Project name: rocketeer" in prompt
    assert "main.py" in prompt
    assert "src/" in prompt  # directories are marked as such
    assert ".hidden" not in prompt


def test_prompt_listing_is_capped(tmp_path):
    for i in range(icongen._MAX_LISTING_ENTRIES + 10):
        (tmp_path / f"file{i:03}.txt").write_text("x")
    prompt = icongen.build_prompt(tmp_path, "big")
    assert "…" in prompt
    assert f"file{icongen._MAX_LISTING_ENTRIES + 5:03}.txt" not in prompt


def test_prompt_includes_capped_readme(tmp_path):
    (tmp_path / "README.md").write_text("A tool for launching rockets. " * 200)
    prompt = icongen.build_prompt(tmp_path, "rocketeer")
    assert "A tool for launching rockets." in prompt
    start = prompt.index("<<<README\n") + len("<<<README\n")
    stop = prompt.index("\nREADME>>>")
    assert stop - start <= icongen._MAX_README_CHARS


def test_prompt_without_readme_has_no_block(tmp_path):
    assert "<<<README" not in icongen.build_prompt(tmp_path, "bare")


def test_prompt_revision_carries_previous_and_feedback(tmp_path):
    prompt = icongen.build_prompt(
        tmp_path, "rocketeer", feedback="make it\nblue", previous_svg=_SVG.encode()
    )
    assert _SVG in prompt
    assert "make it blue" in prompt  # feedback collapses to one line


def test_prompt_feedback_without_previous(tmp_path):
    prompt = icongen.build_prompt(tmp_path, "rocketeer", feedback="use a gear")
    assert "use a gear" in prompt
    assert "<<<SVG" not in prompt


def test_prompt_blank_feedback_is_omitted(tmp_path):
    prompt = icongen.build_prompt(tmp_path, "rocketeer", feedback="   ")
    assert "adjustment" not in prompt


# -- save_icon ----------------------------------------------------------------


def test_save_icon_round_trips_through_discovery(tmp_path):
    data = _SVG.encode()
    path = icongen.save_icon(tmp_path, data)
    assert path == tmp_path / projecticons.PROJECT_ICON_FILENAME
    assert path.read_bytes() == data
    # What Save wrote is exactly what the sidebar will pick up.
    assert projecticons.project_icon_data(tmp_path) == data


def test_save_icon_overwrites_existing(tmp_path):
    (tmp_path / projecticons.PROJECT_ICON_FILENAME).write_text("old")
    icongen.save_icon(tmp_path, _SVG.encode())
    assert projecticons.project_icon_data(tmp_path) == _SVG.encode()


# -- IconRun model selection ----------------------------------------------------


class _FakeProc:
    returncode = 0

    def communicate(self, _prompt=None, timeout=None):
        return _SVG, ""


def _capture_popen(monkeypatch):
    calls: list[list[str]] = []

    def popen(argv, **_kw):
        calls.append(argv)
        return _FakeProc()

    monkeypatch.setattr(icongen.subprocess, "Popen", popen)
    monkeypatch.setattr(icongen.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(icongen.AppState, "get_setting", lambda _self, _key: "claude-sonnet-5")
    return calls


def test_run_asks_for_the_preference_by_default(monkeypatch):
    calls = _capture_popen(monkeypatch)
    assert icongen.IconRun().run("brief") == _SVG.encode()
    assert calls[0][calls[0].index("--model") + 1] == "claude-sonnet-5"


def test_run_honours_the_dialogs_own_pick(monkeypatch):
    # The dialog's drop-down overrides the preference for one run only: the
    # setting is read, not written, and an explicit id needs no catalog.
    calls = _capture_popen(monkeypatch)
    icongen.IconRun().run("brief", model="claude-opus-5")
    assert calls[0][calls[0].index("--model") + 1] == "claude-opus-5"
