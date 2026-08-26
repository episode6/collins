# New in the ghackett fork of agent-session-manager (GPL-3.0).

from collins import newchat


def test_draft_ids_are_distinct_and_recognisable():
    a, b = newchat.new_draft_id(), newchat.new_draft_id()
    assert a != b
    assert newchat.is_draft_id(a) and newchat.is_draft_id(b)
    assert not newchat.is_draft_id("placeholder-3")
    assert not newchat.is_draft_id("4f3c1c2e-0000-4000-8000-000000000000")
    assert not newchat.is_draft_id(newchat.DRAFT_PREFIX)  # a bare prefix names nothing
    assert not newchat.is_draft_id(None)


def test_draft_worthy_needs_text_or_a_panel():
    assert not newchat.draft_worthy("", False)
    assert not newchat.draft_worthy("  \n\t", False)  # an emptied box
    assert newchat.draft_worthy("fix the tests", False)
    assert newchat.draft_worthy("", True)  # a shell open beside the screen


def test_draft_record_omits_untouched_slots():
    record = newchat.draft_record("/p", "claude", "hi", None, None, 12.5)
    assert record == {"cwd": "/p", "provider": "claude", "text": "hi", "created": 12.5}
    record = newchat.draft_record("/p", "claude", "", False, {"mode": "bottom"}, 1)
    assert record["worktree"] is False
    assert record["layout"] == {"mode": "bottom"}
    assert record["created"] == 1.0


def test_valid_draft_roundtrips_a_good_record():
    record = newchat.draft_record("/p", "claude", "hi", True, {"mode": "right"}, 3.0)
    assert newchat.valid_draft(record) == record


def test_valid_draft_drops_malformed_records():
    assert newchat.valid_draft(None) is None
    assert newchat.valid_draft("nope") is None
    assert newchat.valid_draft({}) is None  # no directory to open it in
    assert newchat.valid_draft({"cwd": ""}) is None
    assert newchat.valid_draft({"cwd": 3}) is None


def test_valid_draft_repairs_optional_slots():
    clean = newchat.valid_draft(
        {
            "cwd": "/p",
            "text": 7,  # not a string
            "provider": "",  # empty
            "worktree": "yes",  # not a bool
            "layout": [],  # not a dict
            "created": "then",  # not a number
        }
    )
    assert clean == {"cwd": "/p", "provider": "claude", "text": "", "created": 0.0}


def test_draft_label_is_the_first_line_with_words():
    assert newchat.draft_label("\n  \nFix   the\tbug\nmore", "Draft") == "Fix the bug"
    assert newchat.draft_label("", "Draft") == "Draft"
    assert newchat.draft_label("   \n\n", "Draft") == "Draft"


def test_draft_label_caps_a_long_line():
    label = newchat.draft_label("x" * 200, "Draft")
    assert len(label) == newchat._LABEL_CHARS
    assert label.endswith("…")
    exact = "y" * newchat._LABEL_CHARS
    assert newchat.draft_label(exact, "Draft") == exact


def test_effective_worktree_follows_choice_then_default_never_outside_git():
    assert newchat.effective_worktree(None, True, True)
    assert not newchat.effective_worktree(None, False, True)
    assert newchat.effective_worktree(True, False, True)
    assert not newchat.effective_worktree(False, True, True)
    assert not newchat.effective_worktree(True, True, False)


def test_draft_record_keeps_a_model_pick_only():
    record = newchat.draft_record("/p", "claude", "hi", None, None, 1.0, model="")
    assert "model" not in record  # the default is re-read, not kept
    record = newchat.draft_record("/p", "claude", "hi", None, None, 1.0, model="claude-opus-5")
    assert record["model"] == "claude-opus-5"
    assert newchat.valid_draft(record) == record


def test_valid_draft_repairs_the_model_slot():
    assert "model" not in newchat.valid_draft({"cwd": "/p", "model": 3})
    assert "model" not in newchat.valid_draft({"cwd": "/p", "model": "  "})
    assert newchat.valid_draft({"cwd": "/p", "model": " sonnet "})["model"] == "sonnet"
