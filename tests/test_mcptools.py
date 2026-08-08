import json
import os
import stat
import sys
from pathlib import Path

import pytest

from collins import mcptools


# ---- the tool table ----------------------------------------------------------


def test_v1_serves_only_set_session_title():
    """The list is app-served, so nothing lands here before its handler does —
    advertising a dead tool would invite calls that can only fail."""
    assert [tool["name"] for tool in mcptools.TOOLS] == ["set_session_title"]


def test_every_tool_schema_is_a_closed_object():
    for tool in mcptools.TOOLS:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert tool["description"]


def test_tool_schema_lookup():
    assert mcptools.tool_schema("set_session_title")["name"] == "set_session_title"
    assert mcptools.tool_schema("no_such_tool") is None


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
