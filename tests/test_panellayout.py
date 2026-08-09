# New in the ghackett fork of agent-session-manager (GPL-3.0).

import collins.panellayout as panellayout


def _strip(pages, *, home=False, open_=True, selected=0):
    return {"strip": {"open": open_, "home": home, "selected": selected, "pages": pages}}


def _shell(hist):
    return {"kind": "shell", "hist": hist}


GOOD_TREE = {
    "split": "h",
    "size": 900,
    "managed": "b",
    "a": {
        "split": "v",
        "size": 640,
        "managed": "b",
        "a": {"terminal": True},
        "b": _strip([_shell(0), _shell(2)], home=True, selected=1),
    },
    "b": _strip([{"kind": "pr", "url": "https://github.com/o/r/pull/1"}]),
}


# -- validate ----------------------------------------------------------------


def test_validate_good_entry_roundtrips():
    entry = {"mode": "right", "sizes": {"bottom": 300, "right": 420}, "tree": GOOD_TREE}
    assert panellayout.validate(entry) == entry


def test_validate_rejects_non_dict():
    assert panellayout.validate(None) is None
    assert panellayout.validate("bottom") is None
    assert panellayout.validate([GOOD_TREE]) is None


def test_validate_normalizes_mode_and_sizes():
    entry = {"mode": "sideways", "sizes": {"bottom": -4, "right": "wide", "up": 9}}
    assert panellayout.validate(entry) == {"mode": "bottom"}


def test_validate_bool_is_not_a_size():
    assert panellayout.validate({"sizes": {"bottom": True}}) == {"mode": "bottom"}


def test_validate_drops_malformed_tree_keeps_memory():
    for bad_tree in (
        "nope",
        {},
        {"terminal": True},  # no strips: the fresh default, not a layout
        {"split": "d", "a": {"terminal": True}, "b": _strip([_shell(0)])},
        {"split": "h", "a": {"terminal": True}, "b": {"strip": {"pages": []}}},
        {"split": "h", "a": {"terminal": True}, "b": _strip(["shell"])},
        {"split": "h", "a": {"terminal": True}, "b": _strip([{"hist": 0}])},
        {"split": "h", "a": {"terminal": True}, "b": _strip([_shell(-1)])},
        {"split": "h", "a": {"terminal": True}, "b": _strip([_shell(True)])},
        {"split": "h", "a": {"terminal": True}, "b": {"terminal": True}},  # two terminals
        _strip([_shell(0)]),  # no terminal at all
        {  # two home strips
            "split": "h",
            "a": {"split": "v", "a": {"terminal": True}, "b": _strip([_shell(0)], home=True)},
            "b": _strip([_shell(1)], home=True),
        },
    ):
        entry = panellayout.validate({"mode": "right", "sizes": {"right": 500}, "tree": bad_tree})
        assert entry == {"mode": "right", "sizes": {"right": 500}}, bad_tree


def test_validate_fills_split_defaults():
    tree = {"split": "v", "a": {"terminal": True}, "b": _strip([_shell(0)])}
    clean = panellayout.validate({"tree": tree})["tree"]
    assert clean["size"] == 0
    assert clean["managed"] == "b"


def test_validate_only_home_strip_may_hide():
    tree = {
        "split": "h",
        "a": {"split": "v", "a": {"terminal": True}, "b": _strip([_shell(0)], home=True, open_=False)},
        "b": _strip([_shell(1)], open_=False),  # satellite claiming hidden
    }
    clean = panellayout.validate({"tree": tree})["tree"]
    assert clean["a"]["b"]["strip"]["open"] is False  # home may hide
    assert clean["b"]["strip"]["open"] is True  # satellite may not


def test_validate_keeps_unknown_page_kinds():
    page = {"kind": "pr", "url": "https://github.com/o/r/pull/7", "extra": 1}
    tree = {"split": "h", "a": {"terminal": True}, "b": _strip([page])}
    clean = panellayout.validate({"tree": tree})["tree"]
    assert clean["b"]["strip"]["pages"] == [page]


# -- prune -------------------------------------------------------------------


def test_prune_keeps_supported_kinds():
    entry = panellayout.validate({"mode": "right", "tree": GOOD_TREE})
    assert panellayout.prune(entry, {"shell", "pr"}) == entry


def test_prune_drops_unknown_kind_and_collapses_strip():
    entry = panellayout.validate({"mode": "right", "tree": GOOD_TREE})
    pruned = panellayout.prune(entry, {"shell"})
    # The pr strip emptied, so the outer split dissolved; the inner
    # terminal/home split promoted to the root.
    assert pruned["tree"] == {
        "split": "v",
        "size": 640,
        "managed": "b",
        "a": {"terminal": True},
        "b": _strip([_shell(0), _shell(2)], home=True, selected=1),
    }


def test_prune_clamps_selected_after_dropping_pages():
    tree = {
        "split": "h",
        "a": {"terminal": True},
        "b": _strip([_shell(0), {"kind": "pr", "url": "u"}], selected=1),
    }
    entry = panellayout.validate({"tree": tree})
    pruned = panellayout.prune(entry, {"shell"})
    strip = pruned["tree"]["b"]["strip"]
    assert strip["pages"] == [_shell(0)]
    assert strip["selected"] == 0


def test_prune_drops_tree_when_no_pages_survive():
    entry = panellayout.validate(
        {"mode": "right", "sizes": {"right": 500}, "tree": GOOD_TREE}
    )
    pruned = panellayout.prune(entry, {"chart"})
    assert pruned == {"mode": "right", "sizes": {"right": 500}}


def test_prune_without_tree_is_identity():
    entry = {"mode": "bottom", "sizes": {"bottom": 200}}
    assert panellayout.prune(entry, {"shell"}) == entry


# -- from_legacy -------------------------------------------------------------


def test_from_legacy_open_bottom_builds_v_split():
    entry = panellayout.from_legacy(
        {"open": True, "mode": "bottom", "sizes": {"bottom": 300, "right": 420}}, [0, 2]
    )
    assert entry == {
        "mode": "bottom",
        "sizes": {"bottom": 300, "right": 420},
        "tree": {
            "split": "v",
            "size": 300,
            "managed": "b",
            "a": {"terminal": True},
            "b": _strip([_shell(0), _shell(2)], home=True),
        },
    }
    assert panellayout.validate(entry) == entry  # migration output is valid


def test_from_legacy_open_right_builds_h_split():
    entry = panellayout.from_legacy({"open": True, "mode": "right"}, [])
    assert entry["tree"]["split"] == "h"
    assert entry["tree"]["size"] == 0
    # No history files: one blank shell, exactly what show_panel spawned.
    assert entry["tree"]["b"]["strip"]["pages"] == [_shell(0)]


def test_from_legacy_closed_panel_keeps_memory_only():
    entry = panellayout.from_legacy(
        {"open": False, "mode": "right", "sizes": {"right": 512}}, [0]
    )
    assert entry == {"mode": "right", "sizes": {"right": 512}}


def test_from_legacy_garbage():
    assert panellayout.from_legacy("nope", []) is None
    assert panellayout.from_legacy({"open": True, "mode": 7, "sizes": "x"}, [-3]) == {
        "mode": "bottom",
        "tree": {
            "split": "v",
            "size": 0,
            "managed": "b",
            "a": {"terminal": True},
            "b": _strip([_shell(0)], home=True),
        },
    }
