import json
import os
import stat
import sys
from pathlib import Path

import pytest

from collins import diffmodel, diffnotes, mcptools

# ---- the tool table ----------------------------------------------------------


def test_serves_exactly_the_landed_tools():
    """The list is app-served, so nothing lands here before its handler does —
    advertising a dead tool would invite calls that can only fail."""
    assert [tool["name"] for tool in mcptools.TOOLS] == [
        "set_session_title",
        "open_in_editor",
        "show_diff",
        "diff_context",
        "annotate_diff",
        "highlight_diff",
        "clear_diff_marks",
        "show_image",
        "notify_user",
        "attach_pr",
        "start_session",
        "read_terminal",
        "run_in_terminal",
    ]


def test_every_tool_schema_is_a_closed_object():
    for tool in mcptools.TOOLS:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert tool["description"]


def test_tool_schema_lookup():
    assert mcptools.tool_schema("set_session_title")["name"] == "set_session_title"
    assert mcptools.tool_schema("no_such_tool") is None


# ---- the per-tool switches ---------------------------------------------------


def test_every_tool_has_a_setting_defaulting_on():
    defaults = mcptools.default_tool_settings()
    assert defaults == {f"mcp_tool_{tool['name']}": True for tool in mcptools.TOOLS}
    assert all(value is True for value in defaults.values())


def test_default_settings_carry_every_tool_switch():
    """The switches only exist because state folds them in — a tool added to
    the table with no setting behind it would read as off."""
    from collins import state

    for tool in mcptools.TOOLS:
        key = mcptools.tool_setting_key(tool["name"])
        assert state.DEFAULT_SETTINGS[key] is True


def test_enabled_tools_serves_only_what_is_switched_on():
    served = mcptools.enabled_tools(lambda name: name != "show_image")
    assert [tool["name"] for tool in served] == [
        "set_session_title",
        "open_in_editor",
        "show_diff",
        "diff_context",
        "annotate_diff",
        "highlight_diff",
        "clear_diff_marks",
        "notify_user",
        "attach_pr",
        "start_session",
        "read_terminal",
        "run_in_terminal",
    ]


def test_show_diff_is_served_on_the_switch_alone():
    """The tool needs nothing outside Collins (the diff view is native): the
    user's switch is the only gate, and there is no availability hook."""
    served = mcptools.enabled_tools(lambda _name: True)
    assert served == mcptools.TOOLS
    served = mcptools.enabled_tools(lambda name: name != "show_diff")
    assert "show_diff" not in [tool["name"] for tool in served]
    assert len(served) == len(mcptools.TOOLS) - 1
    assert not hasattr(mcptools, "REQUIRES_HUNK")


def test_show_diff_description_names_no_external_viewer():
    description = mcptools.tool_schema("show_diff")["description"]
    # No session id to quote, no install link, no viewer to name: the page is
    # Collins' own.
    assert "session id" not in description and ".dev" not in description
    assert "install" not in description
    assert "diff view" in description


def test_enabled_tools_can_serve_nothing_at_all():
    assert mcptools.enabled_tools(lambda _name: False) == []
    assert mcptools.enabled_tools(lambda _name: True) == mcptools.TOOLS


# ---- validation --------------------------------------------------------------


def test_valid_title_passes():
    assert mcptools.validate_args("set_session_title", {"title": "Fix the flaky test"}) is None


def test_unknown_tool_is_rejected():
    assert "Unknown tool" in mcptools.validate_args("no_such_tool", {})


def test_non_object_args_are_rejected():
    """The socket is reachable by any local process — never assume the CLI's
    schema enforcement already ran."""
    assert mcptools.validate_args("set_session_title", None) is not None
    assert mcptools.validate_args("set_session_title", ["title"]) is not None
    assert mcptools.validate_args("set_session_title", "title") is not None


def test_missing_required_argument_is_rejected():
    assert "title" in mcptools.validate_args("set_session_title", {})


def test_wrong_type_is_rejected():
    assert "string" in mcptools.validate_args("set_session_title", {"title": 7})
    assert "string" in mcptools.validate_args("set_session_title", {"title": None})


def test_empty_title_is_rejected():
    assert "empty" in mcptools.validate_args("set_session_title", {"title": ""})


def test_overlong_title_is_rejected():
    assert "200" in mcptools.validate_args("set_session_title", {"title": "x" * 201})
    assert mcptools.validate_args("set_session_title", {"title": "x" * 200}) is None


def test_unexpected_argument_is_rejected():
    error = mcptools.validate_args(
        "set_session_title", {"title": "ok", "surprise": True}
    )
    assert "surprise" in error


def test_open_in_editor_args():
    assert mcptools.validate_args("open_in_editor", {"path": "collins/app.py"}) is None
    assert (
        mcptools.validate_args("open_in_editor", {"path": "/tmp/x.py", "line": 12})
        is None
    )
    assert "path" in mcptools.validate_args("open_in_editor", {})
    assert "empty" in mcptools.validate_args("open_in_editor", {"path": ""})


def test_open_in_editor_line_must_be_a_positive_integer():
    """Lines are 1-based on the wire (matching how references are written);
    the handler converts to the editor's 0-based cursor."""
    assert "integer" in mcptools.validate_args(
        "open_in_editor", {"path": "x.py", "line": "12"}
    )
    assert "integer" in mcptools.validate_args(
        "open_in_editor", {"path": "x.py", "line": True}
    )
    assert "at least 1" in mcptools.validate_args(
        "open_in_editor", {"path": "x.py", "line": 0}
    )


def test_show_diff_args():
    assert mcptools.validate_args("show_diff", {"what": "unstaged"}) is None
    assert mcptools.validate_args("show_diff", {"what": "HEAD~1"}) is None
    assert (
        mcptools.validate_args(
            "show_diff", {"what": "branch", "file": "collins/app.py", "line": 12}
        )
        is None
    )
    assert "what" in mcptools.validate_args("show_diff", {})
    assert "empty" in mcptools.validate_args("show_diff", {"what": ""})
    assert "128" in mcptools.validate_args("show_diff", {"what": "x" * 129})
    assert "empty" in mcptools.validate_args("show_diff", {"what": "staged", "file": ""})


def test_show_diff_line_must_be_a_positive_integer():
    """1-based on the wire, like open_in_editor's; the handler hands it to
    the view's reveal as it is. Whether it needs a file is the handler's
    check (the schema has no way to say so)."""
    assert "integer" in mcptools.validate_args(
        "show_diff", {"what": "staged", "file": "x.py", "line": "12"}
    )
    assert "at least 1" in mcptools.validate_args(
        "show_diff", {"what": "staged", "file": "x.py", "line": 0}
    )
    assert "mode" in mcptools.validate_args("show_diff", {"what": "staged", "mode": "x"})


def test_show_diff_takes_a_side_and_a_hunk():
    """The native view's additions: a side for the line, a 1-based hunk as
    an alternative address. Their exclusivity and their needing a file are
    the handler's checks."""
    ok = mcptools.validate_args
    assert ok("show_diff", {"what": "staged", "file": "x.py", "line": 2, "side": "old"}) is None
    assert ok("show_diff", {"what": "staged", "file": "x.py", "hunk": 2}) is None
    assert "one of: old, new" in ok("show_diff", {"what": "staged", "file": "x.py", "side": "left"})
    assert "at least 1" in ok("show_diff", {"what": "staged", "file": "x.py", "hunk": 0})
    assert "integer" in ok("show_diff", {"what": "staged", "file": "x.py", "hunk": "2"})
    description = mcptools.tool_schema("show_diff")["description"]
    assert "hunk" in description and "diff_context" in description and "annotate_diff" in description


def test_diff_tools_descriptions_name_show_diff_as_the_opener():
    for name in ("diff_context", "annotate_diff", "highlight_diff"):
        assert "show_diff" in mcptools.tool_schema(name)["description"], name
    assert "show_diff" in mcptools.PAGE_NOT_OPEN


def test_diff_context_args():
    ok = mcptools.validate_args
    assert ok("diff_context", {}) is None
    assert ok("diff_context", {"files": False, "patch": True, "notes": True}) is None
    assert "true or false" in ok("diff_context", {"patch": "yes"})
    assert "Unexpected" in ok("diff_context", {"file": "x"})


def test_annotate_diff_args():
    ok = mcptools.validate_args
    note = {"file": "a.py", "line": 3, "summary": "Off by one"}
    assert ok("annotate_diff", {"notes": [note]}) is None
    full = dict(note, side="old", rationale="Why", author="reviewer")
    assert ok("annotate_diff", {"notes": [full]}) is None
    by_hunk = {"file": "a.py", "hunk": 1, "summary": "s"}
    assert ok("annotate_diff", {"notes": [by_hunk], "focus": True}) is None
    assert "notes" in ok("annotate_diff", {})
    assert "list" in ok("annotate_diff", {"notes": note})
    assert "at least 1" in ok("annotate_diff", {"notes": []})
    assert "at most 100" in ok("annotate_diff", {"notes": [note] * 101})
    assert "notes[0]" in ok("annotate_diff", {"notes": ["x"]})
    assert "missing 'summary'" in ok("annotate_diff", {"notes": [{"file": "a.py", "line": 1}]})
    assert "notes[1]" in ok("annotate_diff", {"notes": [note, {"file": "a.py", "line": 1}]})
    assert "unexpected 'lines'" in ok("annotate_diff", {"notes": [dict(note, lines=1)]})
    assert "notes[0].line" in ok("annotate_diff", {"notes": [dict(note, line=0)]})
    assert "notes[0].side" in ok("annotate_diff", {"notes": [dict(note, side="left")]})
    assert "notes[0].summary" in ok("annotate_diff", {"notes": [dict(note, summary="")]})
    assert "4000" in ok("annotate_diff", {"notes": [dict(note, rationale="r" * 4001)]})
    assert "80" in ok("annotate_diff", {"notes": [dict(note, author="a" * 81)]})
    assert "true or false" in ok("annotate_diff", {"notes": [note], "focus": 1})


def test_highlight_diff_args():
    ok = mcptools.validate_args
    mark = {"file": "a.py", "line": 3, "start": 0, "end": 4}
    assert ok("highlight_diff", {"marks": [mark]}) is None
    assert ok("highlight_diff", {"marks": [dict(mark, side="old", tone="warning")], "focus": False}) is None
    assert "at most 500" in ok("highlight_diff", {"marks": [mark] * 501})
    assert "missing 'end'" in ok("highlight_diff", {"marks": [{"file": "a.py", "line": 3, "start": 0}]})
    assert "at least 0" in ok("highlight_diff", {"marks": [dict(mark, start=-1)]})
    assert "at least 1" in ok("highlight_diff", {"marks": [dict(mark, end=0)]})
    assert "one of: match, current, info, warning, error, dim" in ok(
        "highlight_diff", {"marks": [dict(mark, tone="loud")]}
    )
    assert "unexpected 'hunk'" in ok("highlight_diff", {"marks": [dict(mark, hunk=1)]})


def test_clear_diff_marks_args():
    ok = mcptools.validate_args
    assert ok("clear_diff_marks", {}) is None
    assert ok("clear_diff_marks", {"file": "a.py", "notes": True, "user": True, "highlights": False}) is None
    assert "empty" in ok("clear_diff_marks", {"file": ""})
    assert "true or false" in ok("clear_diff_marks", {"user": "yes"})
    assert "Unexpected" in ok("clear_diff_marks", {"all": True})


def test_every_diff_tool_has_a_switch_label():
    """tokensettings' labels are GTK, but their table is what a switch is
    titled with: a tool without one falls back to its bare name."""
    source = (Path(__file__).parent.parent / "collins" / "tokensettings.py").read_text(encoding="utf-8")
    for name in ("diff_context", "annotate_diff", "highlight_diff", "clear_diff_marks"):
        assert f'"{name}": (' in source, name


# ---- the diff tools' GTK-free half --------------------------------------------

_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 3b18e51..a1b2c3d 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,5 @@ def main():
 import os
-import sys
+import sys, re
+import json

 print(os.name)
@@ -20,3 +21,3 @@ class App:
     def run(self):
-        return 1
+        return 2
     # end
diff --git a/img/logo.png b/img/logo.png
index 3b18e51..a1b2c3d 100644
Binary files a/img/logo.png and b/img/logo.png differ
"""


def _files():
    return diffmodel.parse(_DIFF)


def _resolve(raw: str) -> str | None:
    return None if raw.startswith("/") or raw.startswith("..") else raw


def test_note_specs_shape_the_arguments_and_default_the_side():
    specs = mcptools.note_specs(
        [
            {"file": "src/app.py", "line": 3, "summary": "s", "rationale": "r", "author": "bot"},
            {"file": "src/app.py", "hunk": 2, "side": "old", "summary": "t"},
        ],
        _resolve,
    )
    assert specs == [
        diffnotes.NoteSpec("src/app.py", "s", rationale="r", author="bot", side=None, line=3, hunk=None),
        diffnotes.NoteSpec("src/app.py", "t", side="old", hunk=2),
    ]


def test_note_specs_refuse_a_path_outside_the_repository_naming_the_entry():
    got = mcptools.note_specs(
        [
            {"file": "src/app.py", "line": 3, "summary": "s"},
            {"file": "../etc/passwd", "line": 1, "summary": "s"},
        ],
        _resolve,
    )
    assert got == "notes[1] (../etc/passwd): 'file' must be a path inside the repository"


def test_note_specs_want_exactly_one_address():
    both = mcptools.note_specs([{"file": "src/app.py", "line": 3, "hunk": 1, "summary": "s"}], _resolve)
    assert both == "notes[0] (src/app.py): give exactly one of 'line' and 'hunk'"
    neither = mcptools.note_specs([{"file": "src/app.py", "summary": "s"}], _resolve)
    assert neither == "notes[0] (src/app.py): give exactly one of 'line' and 'hunk'"


def test_highlight_specs_shape_the_arguments():
    specs = mcptools.highlight_specs(
        [{"file": "src/app.py", "line": 3, "start": 0, "end": 6, "tone": "error", "side": "new"}], _resolve
    )
    assert specs == [diffnotes.HighlightSpec("src/app.py", 3, 0, 6, side="new", tone="error")]
    got = mcptools.highlight_specs([{"file": "/abs/x", "line": 1, "start": 0, "end": 1}], _resolve)
    assert got == "marks[0] (/abs/x): 'file' must be a path inside the repository"


def test_a_batch_with_one_bad_address_lands_nothing_and_names_the_offender():
    """The rule the tools promise: validated whole against the loaded diff
    (diffnotes.MarkStore over diffmodel.locate) before anything lands."""
    files = _files()
    store = diffnotes.MarkStore()
    specs = mcptools.note_specs(
        [
            {"file": "src/app.py", "line": 3, "summary": "fine"},
            {"file": "src/app.py", "line": 10, "summary": "in the gap"},
            {"file": "src/app.py", "line": 22, "summary": "fine too"},
        ],
        _resolve,
    )
    assert store.add_notes(files, specs, diffnotes.AGENT) == (
        "line 10 (new) of src/app.py is not in a hunk of the loaded diff"
    )
    assert store.notes() == []
    missing = mcptools.note_specs(
        [
            {"file": "src/app.py", "line": 3, "summary": "fine"},
            {"file": "gone.py", "line": 1, "summary": "x"},
        ],
        _resolve,
    )
    assert store.add_notes(files, missing, diffnotes.AGENT) == "gone.py is not in the loaded diff"
    binary = mcptools.note_specs([{"file": "img/logo.png", "hunk": 1, "summary": "x"}], _resolve)
    assert store.add_notes(files, binary, diffnotes.AGENT) == "img/logo.png has no hunk 1 (it has 0)"
    assert store.notes() == []
    marks = mcptools.highlight_specs(
        [
            {"file": "src/app.py", "line": 3, "start": 0, "end": 6},
            {"file": "src/app.py", "line": 3, "start": 0, "end": 99},
        ],
        _resolve,
    )
    assert "range [0, 99)" in store.add_highlights(files, marks)
    assert store.highlights() == []
    good = mcptools.note_specs(
        [
            {"file": "src/app.py", "line": 3, "summary": "fine"},
            {"file": "src/app.py", "hunk": 2, "summary": "x"},
        ],
        _resolve,
    )
    added = store.add_notes(files, good, diffnotes.AGENT)
    assert [note.id for note in added] == ["n1", "n2"]
    assert mcptools.annotate_reply([note.id for note in added]) == "Added 2 notes: n1, n2."
    assert mcptools.annotate_reply(["n3"]) == "Added 1 note: n3."
    assert mcptools.highlight_reply(1) == "Added 1 highlight."
    assert mcptools.highlight_reply(3) == "Added 3 highlights."


def test_diff_context_reply_is_one_json_object_with_1_based_hunks():
    files = _files()
    context = mcptools.DiffContext(
        loaded="unstaged",
        breadcrumb="working tree · unstaged",
        files=tuple(files),
        path="src/app.py",
        hunk=1,
        selection=("src/app.py", 0, 1, 3),
        notes=(diffnotes.Note("n1", "agent", "src/app.py", "new", 3, "s", "r", "bot", "k"),),
        highlights=(diffnotes.Highlight("h1", "src/app.py", "new", 3, 0, 6, "match", "k"),),
    )
    reply = json.loads(mcptools.diff_context_reply(context))
    assert reply["loaded"] == "unstaged"
    assert reply["breadcrumb"] == "working tree · unstaged"
    assert reply["current"] == {
        "file": "src/app.py",
        "hunk": 2,
        "header": "@@ -20,3 +21,3 @@ class App:",
        "old": [20, 22],
        "new": [21, 23],
    }
    assert reply["selection"] == {
        "file": "src/app.py",
        "hunk": 1,
        "lines": 3,
        "old": [2, 2],
        "new": [2, 3],
        "text": "-import sys\n+import sys, re\n+import json\n",
    }
    assert [entry["path"] for entry in reply["files"]] == ["src/app.py", "img/logo.png"]
    app = reply["files"][0]
    assert (app["kind"], app["additions"], app["deletions"]) == ("change", 3, 2)
    assert [hunk["hunk"] for hunk in app["hunks"]] == [1, 2]
    assert app["hunks"][0] == {
        "hunk": 1,
        "header": "@@ -1,4 +1,5 @@ def main():",
        "old": [1, 4],
        "new": [1, 5],
    }
    assert reply["files"][1]["hunks"] == [] and reply["files"][1]["kind"] == "binary"
    assert "patch" not in app and "notes" not in reply
    assert "previous_path" not in app


def test_diff_context_reply_options_and_the_empty_page():
    files = _files()
    context = mcptools.DiffContext(loaded={"show": "abc"}, breadcrumb="abc Subject", files=tuple(files))
    reply = json.loads(mcptools.diff_context_reply(context, files=False, notes=True))
    assert reply["loaded"] == {"show": "abc"}
    assert reply["current"] is None and reply["selection"] is None
    assert "files" not in reply
    assert reply["notes"] == [] and reply["highlights"] == []
    with_notes = json.loads(
        mcptools.diff_context_reply(
            mcptools.DiffContext(
                "staged",
                "x",
                (),
                notes=(diffnotes.Note("n1", "user", "a", "old", 2, "s", None, None, "k"),),
                highlights=(diffnotes.Highlight("h1", "a", "new", 3, 0, 6, "dim", "k"),),
            ),
            notes=True,
        )
    )
    assert with_notes["notes"] == [
        {"id": "n1", "source": "user", "file": "a", "side": "old", "line": 2, "summary": "s"}
    ]
    assert with_notes["highlights"] == [
        {"id": "h1", "file": "a", "side": "new", "line": 3, "start": 0, "end": 6, "tone": "dim"}
    ]
    assert with_notes["files"] == []


def test_diff_context_reply_carries_patches_up_to_the_cap():
    files = _files()
    context = mcptools.DiffContext("unstaged", "x", tuple(files))
    reply = json.loads(mcptools.diff_context_reply(context, patch=True))
    assert reply["files"][0]["patch"] == files[0].patch
    assert reply["files"][1]["patch"] == files[1].patch
    assert "patch_truncated" not in reply
    budget = len(files[0].patch.encode("utf-8"))
    capped = json.loads(mcptools.diff_context_reply(context, patch=True, patch_budget=budget))
    assert capped["files"][0]["patch"] == files[0].patch
    assert "patch" not in capped["files"][1] and capped["files"][1]["patch_omitted"] is True
    assert "patch_truncated" in capped
    assert mcptools.DIFF_CONTEXT_PATCH_BYTES == 200_000


def _framed(text: str) -> bytes:
    """The reply as mcpserver._send puts it on the wire."""
    return mcptools.encode_message({"id": 1, "ok": True, "message": text})


def _big_file(name: str = "big.txt", hunks: int = 1, width: int = 300_000) -> diffmodel.File:
    huge = "x" * width
    lines = tuple(diffmodel.Line(diffmodel.ADD, huge, None, i + 1) for i in range(4))
    hunk = diffmodel.Hunk(0, "@@ -0,0 +1,4 @@", 0, 0, 1, 4, "", lines)
    return diffmodel.File(name, None, "new", None, None, None, False, (hunk,) * hunks, 4, 0, "+" + huge, "h")


def test_diff_context_reply_always_fits_one_wire_frame():
    """A reply over MAX_LINE closes the shim's connection, so an oversize one
    shrinks — the patches first, then the hunk lists, then the notes, then
    the files, then the selection's text — and says so. The measure is
    the framed reply, not the raw text: encode_message escapes every
    quote and newline of the indented JSON once more."""
    file = _big_file()
    notes = tuple(
        diffnotes.Note(f"n{i}", "agent", "big.txt", "new", 1, "s" * 4000, "r" * 4000, None, "k")
        for i in range(200)
    )
    context = mcptools.DiffContext("unstaged", "x", (file,) * 3, notes=notes)
    text = mcptools.diff_context_reply(context, patch=True, notes=True, patch_budget=10_000_000)
    assert len(_framed(text)) <= mcptools.MAX_LINE
    reply = json.loads(text)
    assert "truncated" in reply
    assert "files" in reply and "patch" not in reply["files"][0]
    small = mcptools.diff_context_reply(mcptools.DiffContext("unstaged", "x", (file,)), patch=True)
    assert "truncated" not in json.loads(small)
    assert len(_framed(small)) <= mcptools.MAX_LINE


def test_diff_context_reply_measures_the_framed_reply():
    """The two replies that fit the raw measure but not the frame: notes
    full of quotes, tabs and newlines (their escapes double in the frame),
    and a plain files-only list of thousands of hunks (the indentation's
    newlines do the same)."""
    noisy = ('"\t\n' * 1000)[:4000]
    notes = tuple(
        diffnotes.Note(f"n{i}", "agent", "a.txt", "new", 1, noisy, noisy, None, "k") for i in range(116)
    )
    files = _files()
    context = mcptools.DiffContext("unstaged", "x", tuple(files), notes=notes)
    text = mcptools.diff_context_reply(context, files=True, notes=True)
    assert len(_framed(text)) <= mcptools.MAX_LINE
    reply = json.loads(text)
    assert "truncated" in reply and "notes" not in reply

    many = _big_file("many.txt", hunks=9000, width=1)
    context = mcptools.DiffContext("unstaged", "x", (many,))
    text = mcptools.diff_context_reply(context)
    assert len(_framed(text)) <= mcptools.MAX_LINE
    reply = json.loads(text)
    assert "truncated" in reply
    entry = reply["files"][0]
    assert "hunks" not in entry and entry["hunks_omitted"] is True and entry["hunk_count"] == 9000
    assert entry["path"] == "many.txt"


def test_diff_context_reply_drops_the_selection_text_last():
    """A selection across a hunk of huge lines is the one unbounded thing
    the bare object carries: its text goes when nothing else is left."""
    file = _big_file(width=400_000)
    context = mcptools.DiffContext("unstaged", "x", (file,), selection=("big.txt", 0, 0, 3))
    text = mcptools.diff_context_reply(context, files=False)
    assert len(_framed(text)) <= mcptools.MAX_LINE
    reply = json.loads(text)
    assert "truncated" in reply
    assert reply["selection"]["text_omitted"] is True and "text" not in reply["selection"]
    assert reply["selection"]["lines"] == 4 and reply["selection"]["new"] == [1, 4]


def test_diff_context_hunk_ranges_are_null_on_an_empty_side():
    """A new file's hunk (`@@ -0,0 +1,4 @@`) has no old line for a note to
    land on: the view pads that side to a row, the tool says null."""
    file = _big_file(width=3)
    reply = json.loads(mcptools.diff_context_reply(mcptools.DiffContext("unstaged", "x", (file,))))
    assert reply["files"][0]["hunks"][0]["old"] is None
    assert reply["files"][0]["hunks"][0]["new"] == [1, 4]
    app = _files()[0]
    reply = json.loads(mcptools.diff_context_reply(mcptools.DiffContext("unstaged", "x", (app,))))
    assert reply["files"][0]["hunks"][0]["old"] == [1, 4]


def test_note_schema_caps_match_diffnotes():
    schema = mcptools.tool_schema("annotate_diff")
    note = schema["inputSchema"]["properties"]["notes"]["items"]["properties"]
    assert note["summary"]["maxLength"] == diffnotes.NOTE_MAX_CHARS
    assert note["rationale"]["maxLength"] == diffnotes.NOTE_MAX_CHARS
    assert mcptools.NOTE_MAX_CHARS == diffnotes.NOTE_MAX_CHARS


def test_reveal_reply_names_the_load_the_spot_and_the_hunk():
    files = _files()
    app, logo = files
    assert mcptools.reveal_reply("working tree · unstaged") == (
        "Loaded working tree · unstaged in the session's git page."
    )
    assert mcptools.reveal_reply("x", "src/app.py", "new", 3, None, app, 0, True) == (
        "Loaded x in the session's git page.\nRevealed src/app.py, line 3 (new side): hunk 1 of 2."
    )
    assert mcptools.reveal_reply("x", "src/app.py", "old", 21, None, app, 1, True).endswith(
        "Revealed src/app.py, line 21 (old side): hunk 2 of 2."
    )
    nearest = mcptools.reveal_reply("x", "src/app.py", "new", 10, None, app, 0, False).split("\n")
    assert nearest[1] == "Revealed src/app.py, line 10 (new side): hunk 1 of 2."
    assert nearest[2] == (
        "Line 10 (new side) isn't in a changed region of that diff; the nearest hunk is shown."
    )
    assert mcptools.reveal_reply("x", "src/app.py", "new", None, 2, app, 1, True).endswith(
        "Revealed src/app.py, hunk 2 of 2."
    )
    assert mcptools.reveal_reply("x", "src/app.py", "new", None, None, app, 0, True).endswith(
        "Revealed src/app.py: hunk 1 of 2."
    )
    assert mcptools.reveal_reply("x", "img/logo.png", "new", None, None, logo, None, True).endswith(
        "Revealed img/logo.png (it is binary)."
    )


def test_hunk_refusal_words():
    app, logo = _files()
    assert mcptools.hunk_refusal("src/app.py", 1, app) is None
    assert mcptools.hunk_refusal("src/app.py", 2, app) is None
    assert mcptools.hunk_refusal("src/app.py", 3, app) == "src/app.py has 2 hunks in that diff, not 3"
    assert mcptools.hunk_refusal("img/logo.png", 1, logo) == (
        "img/logo.png has no hunks in that diff (it is binary)"
    )
    assert mcptools.hunk_refusal("gone", 1, None) == "gone isn't in that diff"


def test_clear_reply_words():
    assert mcptools.clear_reply(2, 3, None) == "Cleared 2 notes and 3 highlights."
    assert mcptools.clear_reply(1, None, "a.py") == "Cleared 1 note from a.py."
    assert mcptools.clear_reply(None, 1, None) == "Cleared 1 highlight."
    assert mcptools.clear_reply(0, 0, None) == "Cleared 0 notes and 0 highlights."
    assert mcptools.clear_reply(None, None, None) == "Nothing cleared."
    assert mcptools.clear_reply(None, None, "a.txt") == "Nothing cleared from a.txt."


def test_clear_targets_reads_one_flag_as_the_other_kind():
    """Neither flag clears both; one alone names what to clear, an explicit
    false the other kind; both false is refused, never 'Cleared .'."""
    assert mcptools.clear_targets({}) == (True, True)
    assert mcptools.clear_targets({"file": "a.txt"}) == (True, True)
    assert mcptools.clear_targets({"notes": True}) == (True, False)
    assert mcptools.clear_targets({"highlights": True}) == (False, True)
    assert mcptools.clear_targets({"notes": False}) == (False, True)
    assert mcptools.clear_targets({"highlights": False}) == (True, False)
    assert mcptools.clear_targets({"notes": True, "highlights": True}) == (True, True)
    assert mcptools.clear_targets({"notes": True, "highlights": False}) == (True, False)
    assert mcptools.clear_targets({"notes": False, "highlights": False}) == mcptools.CLEAR_NOTHING


def test_show_image_args():
    assert mcptools.validate_args("show_image", {"path": "shot.png"}) is None
    assert "path" in mcptools.validate_args("show_image", {})
    assert "line" in mcptools.validate_args(
        "show_image", {"path": "shot.png", "line": 3}
    )


def test_show_image_caption_args():
    assert (
        mcptools.validate_args(
            "show_image", {"path": "shot.png", "caption": "Before the fix"}
        )
        is None
    )
    assert "empty" in mcptools.validate_args(
        "show_image", {"path": "shot.png", "caption": ""}
    )
    assert "string" in mcptools.validate_args(
        "show_image", {"path": "shot.png", "caption": 7}
    )
    assert "300" in mcptools.validate_args(
        "show_image", {"path": "shot.png", "caption": "x" * 301}
    )


def test_notify_user_args():
    assert mcptools.validate_args("notify_user", {"message": "Ready to push?"}) is None
    assert "message" in mcptools.validate_args("notify_user", {})
    assert "empty" in mcptools.validate_args("notify_user", {"message": ""})
    assert "string" in mcptools.validate_args("notify_user", {"message": 7})


def test_attach_pr_args():
    assert (
        mcptools.validate_args(
            "attach_pr", {"url": "https://github.com/episode6/collins/pull/55"}
        )
        is None
    )
    assert "url" in mcptools.validate_args("attach_pr", {})
    assert "empty" in mcptools.validate_args("attach_pr", {"url": ""})
    assert "string" in mcptools.validate_args("attach_pr", {"url": 55})


def test_overlong_attach_pr_url_is_rejected():
    """The schema only bounds the string — whether it is a PR URL at all is
    the handler's question (prstatus.parse_pr_url), so a rejection can name
    the value."""
    url = "https://github.com/o/r/pull/" + "5" * 300
    assert "300" in mcptools.validate_args("attach_pr", {"url": url})


def test_overlong_notification_is_rejected():
    """A body no notification shell would show in full is a mistake worth
    telling the agent about, not silently truncating."""
    assert mcptools.validate_args("notify_user", {"message": "x" * 500}) is None
    assert "500" in mcptools.validate_args("notify_user", {"message": "x" * 501})


def test_start_session_minimal_args():
    """Only the prompt is required; the cwd, worktree, and mode all default."""
    assert mcptools.validate_args("start_session", {"prompt": "Fix the build"}) is None
    assert "prompt" in mcptools.validate_args("start_session", {})
    assert "empty" in mcptools.validate_args("start_session", {"prompt": ""})
    assert "50000" in mcptools.validate_args("start_session", {"prompt": "x" * 50_001})


def test_start_session_cwd_is_an_optional_string():
    assert (
        mcptools.validate_args(
            "start_session", {"prompt": "go", "cwd": "/home/me/project"}
        )
        is None
    )
    assert "string" in mcptools.validate_args(
        "start_session", {"prompt": "go", "cwd": 7}
    )


def test_start_session_worktree_must_be_a_boolean():
    """The validator grew a boolean kind for this — an int isn't a bool, and
    a bare string never was one."""
    assert (
        mcptools.validate_args("start_session", {"prompt": "go", "worktree": True})
        is None
    )
    assert (
        mcptools.validate_args("start_session", {"prompt": "go", "worktree": False})
        is None
    )
    assert "true or false" in mcptools.validate_args(
        "start_session", {"prompt": "go", "worktree": "yes"}
    )
    assert "true or false" in mcptools.validate_args(
        "start_session", {"prompt": "go", "worktree": 1}
    )


def test_start_session_permission_mode_is_enum_constrained():
    """The schema bounds it to a fixed set; the handler narrows that further
    (bypass is refused there, not here)."""
    for mode in ("plan", "acceptEdits", "bypassPermissions"):
        assert (
            mcptools.validate_args(
                "start_session", {"prompt": "go", "permission_mode": mode}
            )
            is None
        )
    error = mcptools.validate_args(
        "start_session", {"prompt": "go", "permission_mode": "whatever"}
    )
    assert "one of" in error and "plan" in error


def test_start_session_rejects_unexpected_arguments():
    assert "surprise" in mcptools.validate_args(
        "start_session", {"prompt": "go", "surprise": 1}
    )


def test_inherited_mode_passes_the_callers_mode_through():
    """A spawn with no explicit mode works the way its spawner does — every
    mode the CLI records comes through as-is, the curated dialog list
    notwithstanding."""
    for mode in ("plan", "acceptEdits", "auto", "default", "manual", "dontAsk"):
        assert mcptools.inherited_permission_mode(mode) == mode


def test_inherited_mode_caps_bypass_at_accept_edits():
    """A bypass-mode caller has no permission prompt gating the call, so
    inheritance grants at most what the tool grants explicitly."""
    assert mcptools.inherited_permission_mode("bypassPermissions") == "acceptEdits"


def test_inherited_mode_drops_junk_to_the_default():
    """Whatever isn't a plain mode token never reaches a command line."""
    for junk in (None, "", "rm -rf /", "a b", "mode-1", "x" * 33, "café"):
        assert mcptools.inherited_permission_mode(junk) == ""


def test_start_session_model_is_a_bounded_string():
    assert (
        mcptools.validate_args("start_session", {"prompt": "go", "model": "opus"})
        is None
    )
    assert "empty" in mcptools.validate_args(
        "start_session", {"prompt": "go", "model": ""}
    )
    assert "at most" in mcptools.validate_args(
        "start_session", {"prompt": "go", "model": "m" * 81}
    )


def test_valid_model_takes_aliases_and_full_ids():
    for model in (
        "opus",
        "sonnet",
        "claude-opus-4-1-20250805",
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ):
        assert mcptools.valid_model(model), model
        assert mcptools.inherited_model(model) == model


def test_inherited_model_drops_junk_to_the_default():
    """Whatever isn't a plain model token never reaches a command line."""
    for junk in (None, "", "-opus", "opus; rm -rf /", "a b", "o" * 81, "café", "[1m]"):
        assert not mcptools.valid_model(junk)
        assert mcptools.inherited_model(junk) == ""


def test_start_session_effort_is_enum_constrained():
    for level in mcptools.EFFORT_LEVELS:
        assert (
            mcptools.validate_args("start_session", {"prompt": "go", "effort": level})
            is None
        )
    assert "effort" in mcptools.validate_args(
        "start_session", {"prompt": "go", "effort": "ultra"}
    )
    assert "effort" in mcptools.validate_args(
        "start_session", {"prompt": "go", "effort": 3}
    )


def test_inherited_effort_passes_the_cli_levels_and_drops_the_rest():
    """A stamped level carries over; an unstamped transcript or a level
    this build doesn't name falls back to the CLI's default."""
    for level in mcptools.EFFORT_LEVELS:
        assert mcptools.inherited_effort(level) == level
    for junk in (None, "", "ultra", "high; rm -rf /", "HIGH"):
        assert mcptools.inherited_effort(junk) == ""


def test_read_terminal_args_all_default():
    """Both arguments are optional: the bare call reads every panel tab."""
    assert mcptools.validate_args("read_terminal", {}) is None
    assert mcptools.validate_args("read_terminal", {"terminal": 2}) is None
    assert mcptools.validate_args("read_terminal", {"lines": 40}) is None


def test_read_terminal_terminal_is_a_positive_integer():
    assert "integer" in mcptools.validate_args("read_terminal", {"terminal": "2"})
    assert "integer" in mcptools.validate_args("read_terminal", {"terminal": True})
    assert "at least 1" in mcptools.validate_args("read_terminal", {"terminal": 0})


def test_read_terminal_lines_are_capped():
    """The schema's maximum is enforced — the first integer bound above, so
    the validator's `maximum` branch exists for it."""
    top = mcptools.TERMINAL_MAX_LINES
    assert mcptools.validate_args("read_terminal", {"lines": top}) is None
    assert f"at most {top}" in mcptools.validate_args(
        "read_terminal", {"lines": top + 1}
    )
    assert "at least 1" in mcptools.validate_args("read_terminal", {"lines": 0})


def test_run_in_terminal_args():
    assert mcptools.validate_args("run_in_terminal", {"command": "make test"}) is None
    assert (
        mcptools.validate_args("run_in_terminal", {"command": "ls", "terminal": 1})
        is None
    )
    assert "command" in mcptools.validate_args("run_in_terminal", {})
    assert "empty" in mcptools.validate_args("run_in_terminal", {"command": ""})
    assert "10000" in mcptools.validate_args(
        "run_in_terminal", {"command": "x" * 10_001}
    )
    assert "at least 1" in mcptools.validate_args(
        "run_in_terminal", {"command": "ls", "terminal": 0}
    )


# ---- the read_terminal reply -------------------------------------------------


def test_terminal_reply_names_each_terminal_and_its_state():
    reply = mcptools.terminal_reply(
        [(1, False, "$ echo hi\nhi"), (3, True, "$ sleep 99")], lines=200
    )
    assert "── Terminal 1 (idle) ──\n$ echo hi\nhi" in reply
    assert "── Terminal 3 (command running) ──\n$ sleep 99" in reply


def test_terminal_reply_tails_to_the_asked_lines():
    dump = "\n".join(f"line {i}" for i in range(100))
    reply = mcptools.terminal_reply([(1, False, dump)], lines=3)
    assert reply == "── Terminal 1 (idle) ──\nline 97\nline 98\nline 99"


def test_terminal_reply_spends_the_tail_on_output_not_blanks():
    """A VTE dump ends in the screen's unused rows; the tail must not."""
    dump = "real output\n" + "\n" * 40
    reply = mcptools.terminal_reply([(1, False, dump)], lines=2)
    assert reply.endswith("real output")


def test_terminal_reply_says_empty_for_a_blank_terminal():
    reply = mcptools.terminal_reply([(2, False, "")], lines=200)
    assert reply == "── Terminal 2 (idle) ──\n(empty)"


def test_terminal_reply_returns_even_when_headers_alone_blow_the_budget():
    """The halving loop's termination guard: an empty tail can't shrink, so
    a dock with enough tabs that the bare headers exceed the frame budget
    must cut the reply off rather than loop forever."""
    reply = mcptools.terminal_reply([(n, False, "") for n in range(1, 20_001)], lines=200)
    frame = mcptools.encode_message(
        {"id": 1, "result": {"content": [{"type": "text", "text": reply}]}}
    )
    assert len(frame) <= mcptools.MAX_LINE
    assert reply.startswith("── Terminal 1 (idle) ──\n(empty)")


def test_terminal_reply_always_fits_one_wire_frame():
    """An oversize reply doesn't degrade, it closes the shim's connection —
    so even a pathological dump (control bytes escape six-to-one in JSON)
    must come back under MAX_LINE once framed."""
    dump = ("\x07" * 100 + "\n") * mcptools.TERMINAL_MAX_LINES
    reply = mcptools.terminal_reply(
        [(n, False, dump) for n in (1, 2, 3)], lines=mcptools.TERMINAL_MAX_LINES
    )
    frame = mcptools.encode_message(
        {"id": 1, "result": {"content": [{"type": "text", "text": reply}]}}
    )
    assert len(frame) <= mcptools.MAX_LINE
    assert "── Terminal 2" in reply  # shrunk, not dropped


# ---- the dispatch skeleton ---------------------------------------------------
#
# app.py's _mcp_dispatch delegates its branching here so the order and error
# strings are pinned without a Gtk.Application: validation always runs first,
# identity second, and only then a handler.


def _rename_ok(found, args):
    return True, f"renamed {found} to {args['title']}"


def test_run_tool_call_reaches_the_handler():
    ok, message = mcptools.run_tool_call(
        "set_session_title", {"title": "hi"},
        find_tab=lambda: "tab-a",
        handlers={"set_session_title": _rename_ok},
    )
    assert (ok, message) == (True, "renamed tab-a to hi")


def test_run_tool_call_validates_before_resolving_identity():
    """A bad call must fail identically whoever makes it — resolving the
    caller first would leak whether a tab owns it through the error shape."""
    walked = []

    def find_tab():
        walked.append(True)
        return "tab-a"

    ok, message = mcptools.run_tool_call(
        "set_session_title", {}, find_tab=find_tab, handlers={}
    )
    assert ok is False
    assert "title" in message
    assert walked == []  # never resolved


def test_run_tool_call_rejects_unknown_tools_without_resolving():
    ok, message = mcptools.run_tool_call(
        "no_such_tool", {}, find_tab=lambda: "tab-a", handlers={}
    )
    assert ok is False
    assert "Unknown tool" in message


def test_run_tool_call_unowned_caller_gets_the_identity_error():
    ok, message = mcptools.run_tool_call(
        "set_session_title", {"title": "hi"},
        find_tab=lambda: None,
        handlers={"set_session_title": _rename_ok},
    )
    assert (ok, message) == (False, mcptools.NOT_FROM_TAB_ERROR)


def test_run_tool_call_advertised_but_unhandled_tool_is_a_clean_error():
    """A TOOLS entry whose handler hasn't landed must error, not crash."""
    ok, message = mcptools.run_tool_call(
        "set_session_title", {"title": "hi"}, find_tab=lambda: "tab-a", handlers={}
    )
    assert ok is False
    assert "Unknown tool" in message


def test_run_tool_call_passes_a_deferred_answer_through():
    """A handler that needs a worker thread (show_image fetching a URL)
    returns the promise instead of the pair; the skeleton must hand it back
    untouched for the service to wait on."""
    pending = mcptools.DeferredResult()
    result = mcptools.run_tool_call(
        "show_image", {"path": "https://example.com/a.png"},
        find_tab=lambda: "tab-a",
        handlers={"show_image": lambda found, args: pending},
    )
    assert result is pending


# ---- deferred answers --------------------------------------------------------


def test_deferred_result_reaches_a_watcher_registered_first():
    seen = []
    pending = mcptools.DeferredResult()
    pending.watch(lambda ok, text: seen.append((ok, text)))
    assert pending.resolved is False
    pending.resolve(True, "Image shown.")
    assert seen == [(True, "Image shown.")]
    assert pending.resolved is True


def test_deferred_result_reaches_a_watcher_registered_late():
    """The fetch can beat the service to it (a cached, instant answer); the
    watcher still gets called, rather than waiting forever for a result that
    already landed."""
    seen = []
    pending = mcptools.DeferredResult()
    pending.resolve(False, "The server answered 404")
    pending.watch(lambda ok, text: seen.append((ok, text)))
    assert seen == [(False, "The server answered 404")]


def test_deferred_result_keeps_its_first_answer():
    """One call, one reply: a worker that answers twice must not put a second
    frame on a wire the shim has already moved past."""
    seen = []
    pending = mcptools.DeferredResult()
    pending.watch(lambda ok, text: seen.append((ok, text)))
    pending.resolve(True, "Image shown.")
    pending.resolve(False, "too late")
    assert seen == [(True, "Image shown.")]


def test_run_tool_call_refuses_a_switched_off_tool_without_resolving():
    """A session handed the tool before the switch was flipped keeps calling
    it; the call is refused here, and the caller is never resolved."""
    walked = []

    ok, message = mcptools.run_tool_call(
        "set_session_title", {"title": "hi"},
        find_tab=lambda: walked.append(True) or "tab-a",
        handlers={"set_session_title": _rename_ok},
        is_enabled=lambda _name: False,
    )
    assert ok is False
    assert message == mcptools.disabled_error("set_session_title")
    assert "set_session_title" in message and "Collins" in message
    assert walked == []


def test_run_tool_call_switch_is_per_tool():
    ok, message = mcptools.run_tool_call(
        "set_session_title", {"title": "hi"},
        find_tab=lambda: "tab-a",
        handlers={"set_session_title": _rename_ok},
        is_enabled=lambda name: name == "set_session_title",
    )
    assert (ok, message) == (True, "renamed tab-a to hi")


def test_run_tool_call_validates_before_consulting_the_switch():
    """A malformed call fails on its arguments whether the tool is on or off:
    the switch is app state, and the error shape shouldn't leak it."""
    asked = []

    ok, message = mcptools.run_tool_call(
        "set_session_title", {},
        find_tab=lambda: "tab-a",
        handlers={},
        is_enabled=lambda name: asked.append(name) or False,
    )
    assert ok is False
    assert "title" in message
    assert asked == []


def test_run_tool_call_returns_the_handlers_failure():
    ok, message = mcptools.run_tool_call(
        "set_session_title", {"title": "hi"},
        find_tab=lambda: "tab-a",
        handlers={"set_session_title": lambda found, args: (False, "not resolved yet")},
    )
    assert (ok, message) == (False, "not resolved yet")


# ---- wire framing ------------------------------------------------------------


def test_encode_decode_round_trip():
    message = {"op": "call", "id": 3, "tool": "set_session_title", "args": {"title": "hi"}}
    data = mcptools.encode_message(message)
    assert data.endswith(b"\n")
    assert b"\n" not in data[:-1]
    assert mcptools.decode_message(data) == message


def test_decode_accepts_str_lines():
    assert mcptools.decode_message('{"op": "hello", "pid": 1}') == {"op": "hello", "pid": 1}


def test_decode_rejects_oversize_lines():
    with pytest.raises(ValueError):
        mcptools.decode_message(b"x" * (mcptools.MAX_LINE + 1))


def test_decode_rejects_malformed_json():
    with pytest.raises(ValueError):
        mcptools.decode_message(b"{nope")


def test_decode_rejects_non_objects():
    with pytest.raises(ValueError):
        mcptools.decode_message(b"[1, 2]")
    with pytest.raises(ValueError):
        mcptools.decode_message(b'"hello"')


def test_encode_rejects_oversize_messages():
    with pytest.raises(ValueError):
        mcptools.encode_message({"blob": "x" * mcptools.MAX_LINE})


# ---- runtime paths and the config file ---------------------------------------


def test_paths_live_under_the_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    base = tmp_path / "collins" / "org.example.Collins"
    assert mcptools.runtime_dir("org.example.Collins") == str(base)
    assert mcptools.socket_path("org.example.Collins") == str(base / "mcp.sock")
    assert mcptools.config_path("org.example.Collins") == str(base / "mcp.json")


def test_paths_fall_back_to_the_system_tempdir(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    import tempfile

    assert mcptools.runtime_dir("x").startswith(tempfile.gettempdir() + os.sep)


def test_distinct_app_ids_get_disjoint_dirs(monkeypatch, tmp_path):
    """Debug instances generate fresh app ids precisely so their sessions
    can't talk to the real app."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert mcptools.runtime_dir("real") != mcptools.runtime_dir("debug-4242")


def test_stable_ids_get_a_persistent_config_dir(monkeypatch, tmp_path):
    """The CLI daemon records the --mcp-config path verbatim in a bg job's
    respawn flags, so for real instances it must survive a reboot — while
    the socket stays on the runtime dir, whose paths are short enough for
    the kernel's unix-socket limit."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for app_id in sorted(mcptools.STABLE_APP_IDS):
        base = tmp_path / "data" / "collins" / app_id
        assert mcptools.config_path(app_id) == str(base / "mcp.json")
        assert mcptools.socket_path(app_id) == str(
            tmp_path / "run" / "collins" / app_id / "mcp.sock"
        )


def test_generated_ids_keep_the_tmpfs_config_dir(monkeypatch, tmp_path):
    """A capture run's id is minted fresh every time; persistent directories
    for those would only accumulate. The E2E shape shares the app's prefix
    on purpose — the split must be an allowlist, not a prefix match."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for app_id in ("com.episode6.Collins.E2E.abc123", "org.example.Collins"):
        assert mcptools.config_path(app_id) == str(
            tmp_path / "run" / "collins" / app_id / "mcp.json"
        )


def test_persistent_config_dir_falls_back_to_local_share(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert mcptools.config_dir("com.episode6.Collins") == str(
        tmp_path / ".local" / "share" / "collins" / "com.episode6.Collins"
    )


def test_write_config_for_a_stable_id_lands_in_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    path = mcptools.write_config("com.episode6.Collins")
    assert path == str(tmp_path / "data" / "collins" / "com.episode6.Collins" / "mcp.json")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    server = config["mcpServers"]["collins"]
    # The config moved; the socket it points at did not.
    assert server["env"]["COLLINS_MCP_SOCKET"] == str(
        tmp_path / "run" / "collins" / "com.episode6.Collins" / "mcp.sock"
    )
    assert stat.S_IMODE(os.stat(Path(path).parent).st_mode) == 0o700


def test_write_config_produces_the_shim_invocation(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = mcptools.write_config("org.example.Collins")
    assert path == mcptools.config_path("org.example.Collins")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    server = config["mcpServers"]["collins"]
    assert server["type"] == "stdio"
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "collins.mcp_shim"]
    assert server["env"]["COLLINS_MCP_SOCKET"] == mcptools.socket_path("org.example.Collins")
    # PYTHONPATH points at the directory *containing* the collins package, so
    # `-m collins.mcp_shim` resolves for editable checkouts and debug
    # instances, not only installed packages.
    package_parent = Path(server["env"]["PYTHONPATH"])
    assert (package_parent / "collins" / "mcp_shim.py").is_file()


def test_infrastructure_cmdlines_match_the_written_config(monkeypatch, tmp_path):
    """The busy poll ignores exactly what the CLI is told to spawn: the two
    are derived from one server table, and this pins that they can't drift —
    a server added to the config without a matching ignore entry would keep
    every session's busy pole up forever."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = mcptools.write_config("org.example.Collins")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = {
        " ".join([server["command"], *server["args"]])
        for server in config["mcpServers"].values()
        if server["type"] == "stdio"
    }
    assert mcptools.infrastructure_cmdlines() == expected


def test_infrastructure_cmdlines_cover_the_shim():
    assert any("collins.mcp_shim" in c for c in mcptools.infrastructure_cmdlines())


def test_write_config_keeps_the_runtime_dir_private(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = mcptools.write_config("org.example.Collins")
    mode = stat.S_IMODE(os.stat(Path(path).parent).st_mode)
    assert mode == 0o700


def test_write_config_is_atomic(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = Path(mcptools.write_config("org.example.Collins"))
    assert not list(path.parent.glob("*.tmp"))


def test_write_config_failure_reports_none(monkeypatch, tmp_path):
    """Best-effort: an unwritable location means launches simply go out
    without the flag, so the caller must see the failure to skip it."""
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(blocked))
    assert mcptools.write_config("org.example.Collins") is None
