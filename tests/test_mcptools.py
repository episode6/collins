import json
import os
import stat
import sys
from pathlib import Path

import pytest

from collins import mcptools

# ---- the tool table ----------------------------------------------------------


def test_serves_exactly_the_landed_tools():
    """The list is app-served, so nothing lands here before its handler does —
    advertising a dead tool would invite calls that can only fail."""
    assert [tool["name"] for tool in mcptools.TOOLS] == [
        "set_session_title",
        "open_in_editor",
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
        "notify_user",
        "attach_pr",
        "start_session",
        "read_terminal",
        "run_in_terminal",
    ]


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
