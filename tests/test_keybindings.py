# New in the ghackett fork of agent-session-manager (GPL-3.0).

import pytest

from collins import keybindings as kb


def test_every_default_is_canonical_and_unique():
    # The controllers get the default strings verbatim, so they must already
    # be in the one spelling the comparisons use.
    for binding in kb.BINDINGS:
        for accelerator in binding.defaults:
            assert kb.canonical(accelerator) == accelerator, binding.action
    assert kb.conflicts({}) == {}


def test_catalogue_actions_are_prefixed_and_unique():
    actions = [b.action for b in kb.BINDINGS]
    assert len(actions) == len(set(actions))
    assert all(a.split(".", 1)[0] in {"win", "app", "editor", "git", "terminal"} for a in actions)
    assert all(b.group in kb.GROUP_LABELS for b in kb.BINDINGS)


def test_git_page_keys_are_page_local_vim_letters():
    # The diff view's chords: vim-shaped bare letters included — page-local,
    # so they never reach the terminal.
    git = {b.action: b.defaults for b in kb.BINDINGS if b.group == kb.GROUP_GIT}
    assert git["git.next-hunk"] == ("bracketright",)
    assert git["git.prev-hunk"] == ("bracketleft",)
    assert git["git.next-file"] == ("period",)
    assert git["git.prev-file"] == ("comma",)
    assert git["git.cursor-down"] == ("j",)
    assert git["git.cursor-up"] == ("k",)
    assert git["git.next-note"] == ("braceright",)
    assert git["git.prev-note"] == ("braceleft",)
    assert git["git.expand-gap"] == ("z",)
    # The staging keys (the extension's x / X / D); `v` is gone — the
    # view's own selection replaced the anchor.
    assert git["git.stage"] == ("x",)
    assert git["git.stage-file"] == ("<Shift>x",)
    assert git["git.discard"] == ("<Shift>d",)
    assert "git.anchor" not in git
    layouts = (git["git.layout-auto"], git["git.layout-split"], git["git.layout-stack"])
    assert layouts == (("0",), ("1",), ("2",))
    assert git["git.line-numbers"] == ("l",)
    assert git["git.wrap"] == ("w",)
    # The notes: `c` adds, `E` edits (Shift, spelled for GTK's matcher), `a`
    # folds the agent's cards.
    assert git["git.add-note"] == ("c",)
    assert git["git.edit-note"] == ("<Shift>e",)
    assert git["git.agent-notes"] == ("a",)
    assert git["git.refresh"] == ("r",)
    assert git["git.filter"] == ("slash",)
    assert git["git.find"] == ("<Control>f",)
    assert git["git.help"] == ("question",)
    assert git["git.open-editor"] == ("e",)
    assert git["git.close"] == ("q",)
    assert all(a.startswith("git.") for a in git)
    assert kb.GROUP_LABELS[kb.GROUP_GIT] == "Git page"


def test_two_local_scopes_may_share_a_chord():
    # Ctrl+F finds in the editor and in the diff: the two controllers fire
    # only with the keyboard inside their own widget, so neither eats the
    # other's press — no conflict, and no holder to warn about.
    assert kb.may_overlap("editor.find", "git.find") is False
    assert kb.may_overlap("editor.find", "win.quick-switch") is True
    assert kb.may_overlap("git.close", "terminal.copy") is True
    assert kb.holders({}, "<Control>f", except_action="git.find") == []
    assert kb.holders({}, "<Control>f", except_action="editor.find") == []
    # Without a reference action every holder is listed.
    assert sorted(kb.holders({}, "<Control>f")) == ["editor.find", "git.find"]
    # A window chord rebound onto a diff key is still a conflict.
    custom = {"win.quick-switch": ["q"]}
    assert kb.conflicts(custom) == {"q": ["win.quick-switch", "git.close"]}
    assert kb.holders(custom, "q", except_action="git.close") == ["win.quick-switch"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("<Control><Shift>t", "<Control><Shift>t"),
        ("<Shift><Control>T", "<Control><Shift>t"),
        ("<Primary>comma", "<Control>comma"),
        ("<ctrl><alt>Delete", "<Control><Alt>Delete"),
        ("F9", "F9"),
        ("  <Control>k ", "<Control>k"),
    ],
)
def test_canonical(raw, expected):
    assert kb.canonical(raw) == expected


@pytest.mark.parametrize("raw", ["", "<Control>", "<Bogus>x", "<Control>a b", None, 3])
def test_invalid_accelerators_raise(raw):
    with pytest.raises(kb.InvalidAccelerator):
        kb.canonical(raw)


@pytest.mark.parametrize(
    "accelerator, text",
    [
        ("<Control><Shift>t", "Ctrl+Shift+T"),
        ("<Control>Page_Down", "Ctrl+PgDn"),
        ("<Control>semicolon", "Ctrl+;"),
        ("<Control>apostrophe", "Ctrl+'"),
        ("<Shift>Return", "Shift+Enter"),
        ("<Control>KP_Add", "Ctrl+Keypad +"),
        ("<Alt><Control>F2", "Ctrl+Alt+F2"),
        ("F7", "F7"),
    ],
)
def test_label(accelerator, text):
    assert kb.label(accelerator) == text


def test_newline_covers_every_enter_keysym():
    # Shift+Enter inserted a newline from the keypad and ISO_Enter too,
    # before the chords moved into the catalogue.
    assert kb.resolve({})["terminal.newline"] == (
        "<Shift>Return", "<Shift>KP_Enter", "<Shift>ISO_Enter"
    )
    assert kb.label("<Shift>ISO_Enter") == "Shift+ISO Enter"


def test_label_parts_keeps_a_plus_key_whole():
    assert kb.label_parts("<Control>plus") == ["Ctrl", "+"]
    assert kb.label_parts("<Control>KP_Add") == ["Ctrl", "Keypad +"]


def test_sanitize_drops_what_it_cannot_honour():
    raw = {
        "win.new-session": ["<Control><Shift>T", "<Control><Shift>t", "", "<Nope>x"],
        "win.no-such-action": ["<Control>x"],
        "win.close-tab": "<Control>w",  # not a list
        "app.quit": [],
    }
    assert kb.sanitize(raw) == {"win.new-session": ["<Control><Shift>t"], "app.quit": []}
    assert kb.sanitize("garbage") == {}
    assert kb.sanitize(None) == {}


def test_resolve_applies_overrides_and_unbinds():
    custom = {"win.close-tab": ["<Control><Shift>w"], "app.quit": []}
    resolved = kb.resolve(custom)
    assert resolved["win.close-tab"] == ("<Control><Shift>w",)
    assert resolved["app.quit"] == ()
    assert resolved["win.new-session"] == ("<Control><Shift>t",)
    assert set(resolved) == {b.action for b in kb.BINDINGS}


def test_with_binding_records_only_changes():
    custom = kb.with_binding({}, "win.close-tab", ["<Control>F4"])
    assert custom == {"win.close-tab": ["<Control>F4"]}
    # Putting the default back removes the entry instead of spelling it out.
    assert kb.with_binding(custom, "win.close-tab", ["<Control>w"]) == {}
    assert kb.with_binding(custom, "win.close-tab", ["<Primary>W"]) == {}
    # Unbinding is an explicit empty list.
    assert kb.with_binding({}, "win.close-tab", []) == {"win.close-tab": []}
    assert kb.is_customized({"win.close-tab": []}, "win.close-tab")
    assert not kb.is_customized({"win.close-tab": ["<Control>w"]}, "win.close-tab")
    assert not kb.is_customized({}, "win.close-tab")


def test_holders_and_conflicts_cross_every_scope():
    # Rebinding the editor's save onto the window's quick switcher chord is
    # a conflict: the window's capture-phase controller eats it first.
    custom = {"editor.save": ["<Control>k"]}
    assert kb.holders(custom, "<Control>k", except_action="editor.save") == ["win.quick-switch"]
    assert kb.holders(custom, "<Primary>K", except_action="win.quick-switch") == ["editor.save"]
    assert kb.conflicts(custom) == {"<Control>k": ["win.quick-switch", "editor.save"]}
    assert kb.holders({}, "<Control>k", except_action="win.quick-switch") == []


def test_hint_follows_the_current_overrides():
    kb.set_current({})
    assert kb.hint("win.toggle-panel") == "Ctrl+J"
    assert kb.with_hint("Show/hide terminal panel", "win.toggle-panel") == (
        "Show/hide terminal panel (Ctrl+J)"
    )
    kb.set_current({"win.toggle-panel": ["<Control>grave"]})
    assert kb.hint("win.toggle-panel") == "Ctrl+`"
    kb.set_current({"win.toggle-panel": []})
    assert kb.hint("win.toggle-panel") == ""
    assert kb.with_hint("Show/hide terminal panel", "win.toggle-panel") == "Show/hide terminal panel"
    kb.set_current({})
