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
    assert all(a.split(".", 1)[0] in {"win", "app", "editor", "terminal"} for a in actions)
    assert all(b.group in kb.GROUP_LABELS for b in kb.BINDINGS)


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
