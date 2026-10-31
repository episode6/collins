# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Connections (collins.connections, spec §3.24, PR-3.1): the list in
ui-state.json read and validated (rule 5), the target grammar, the local
connection, `find` by name, `last_connection`, and the one writer."""

import json

import pytest

from collins import connections, uistate

ID_A = "a" * 32
ID_B = "b" * 32


def _ui(tmp_path, rows=None, last=""):
    path = tmp_path / "ui-state.json"
    if rows is not None or last:
        path.write_text(json.dumps({"connections": rows or [], "last_connection": last}))
    return uistate.UiState(path, {})


# ---- the target grammar -------------------------------------------------------------


@pytest.mark.parametrize(
    "text, host, user, port",
    [
        ("build.example.net", "build.example.net", "", None),
        ("collins@localhost:2222", "localhost", "collins", 2222),
        ("me@10.0.0.7", "10.0.0.7", "me", None),
        ("box:22", "box", "", 22),
        ("a-b_c@h-1.x", "h-1.x", "a-b_c", None),
    ],
)
def test_parse_target_takes_user_host_port(text, host, user, port):
    target = connections.parse_target(text)
    assert target == connections.Target(host=host, user=user, port=port)
    assert target.destination == (f"{user}@{host}" if user else host)


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        42,
        "-oProxyCommand=evil host",  # an option, not a host
        "user@-host",
        "host:0",
        "host:65536",
        "host:22x",
        "a b",
        "host;rm -rf",
        "host/path",
        "user@",
        "@host",
        "host.",
        "ho..st",
        "[::1]:22",  # IPv6 is not taken (brackets)
        "x" * 400,
        "user\n@host",
    ],
)
def test_parse_target_refuses_what_ssh_or_a_shell_could_misread(text):
    assert connections.parse_target(text) is None


def test_names_are_bounded_and_printable():
    assert connections.valid_name("build box")
    assert not connections.valid_name("")
    assert not connections.valid_name("   ")
    assert not connections.valid_name("a\x00b")
    assert not connections.valid_name("x" * (connections.NAME_MAX + 1))
    assert not connections.valid_name(3)


# ---- rows off the file ----------------------------------------------------------------


def test_rows_are_validated_and_bad_ones_dropped(tmp_path):
    rows = [
        {"id": ID_A, "name": "box", "kind": "ssh", "target": "me@box"},
        {"id": ID_B, "name": "two", "kind": "ssh", "target": "me@two:2222", "remote_app_id": "com.x.Y"},
        {"id": "short", "name": "bad id", "kind": "ssh", "target": "h"},
        {"id": "c" * 32, "name": "", "kind": "ssh", "target": "h"},
        {"id": "d" * 32, "name": "bad target", "kind": "ssh", "target": "-oFoo"},
        {"id": "e" * 32, "name": "local never stored", "kind": "local", "target": ""},
        {"id": "f" * 32, "name": "unknown kind", "kind": "tcp", "target": "h"},
        {"id": "a" * 31 + "0", "name": "bad app id", "kind": "ssh", "target": "h",
         "remote_app_id": "no dots"},
        {"id": ID_A, "name": "duplicate id", "kind": "ssh", "target": "h"},
        "not a dict",
        None,
    ]
    got = connections.clean(rows)
    assert [c.id for c in got] == [ID_A, ID_B]
    assert got[0].target == "me@box" and got[0].remote_app_id == ""
    assert got[1].remote_app_id == "com.x.Y" and got[1].parsed_target().port == 2222
    assert connections.clean("nope") == [] and connections.clean(None) == []


def test_the_list_is_bounded(tmp_path):
    rows = [
        {"id": f"{i:032x}", "name": f"n{i}", "kind": "ssh", "target": "h"}
        for i in range(connections.CONNECTIONS_MAX + 10)
    ]
    assert len(connections.clean(rows)) == connections.CONNECTIONS_MAX


def test_to_record_round_trips():
    connection = connections.new_ssh("box", "me@box:2222", "com.x.Y")
    assert connections.from_record(connection.to_record()) == connection
    assert connection.to_record()["remote_app_id"] == "com.x.Y"
    record = connections.new_ssh(" box ", "me@box").to_record()
    assert set(record) == {"id", "name", "kind", "target"} and record["name"] == "box"
    assert len(record["id"]) == 32


def test_new_ssh_refuses_bad_input():
    with pytest.raises(ValueError, match="name"):
        connections.new_ssh("", "me@box")
    with pytest.raises(ValueError, match="target"):
        connections.new_ssh("box", "-oFoo")
    with pytest.raises(ValueError, match="remote_app_id"):
        connections.new_ssh("box", "me@box", "not an id")


# ---- find ---------------------------------------------------------------------------


def test_find_by_name_then_case_then_id():
    a = connections.Connection(id=ID_A, name="Build", kind="ssh", target="h")
    b = connections.Connection(id=ID_B, name="build", kind="ssh", target="h")
    rows = [a, b]
    assert connections.find(rows, "build") is b  # exact first
    assert connections.find(rows, "BUILD") is a  # then case-insensitive, first wins
    assert connections.find(rows, ID_B) is b
    assert connections.find(rows, "nope") is None
    assert connections.find(rows, "") is None
    assert connections.find(rows, "local") is connections.LOCAL
    assert connections.find(rows, "Local") is connections.LOCAL
    assert connections.LOCAL.is_local and not a.is_local


# ---- the file's one writer ------------------------------------------------------------


def test_connections_add_remove_and_last(tmp_path):
    ui = _ui(tmp_path)
    store = connections.Connections(ui)
    assert store.all() == [] and store.last is None
    added = store.add(connections.new_ssh("box", "me@box:2222"))
    assert store.get(added.id) == added and store.find("box") == added
    assert store.get(connections.LOCAL_ID) is connections.LOCAL
    on_disk = json.loads((tmp_path / "ui-state.json").read_text())
    assert on_disk["connections"] == [added.to_record()]
    # Reading the file again sees the row (another UiState over it).
    assert connections.Connections(uistate.UiState(tmp_path / "ui-state.json", {})).all() == [added]
    store.set_last(added)
    assert json.loads((tmp_path / "ui-state.json").read_text())["last_connection"] == added.id
    assert store.last == added
    # The local connection clears last.
    store.set_last(connections.LOCAL)
    assert store.last is None and ui.last_connection == ""
    store.set_last(added)
    # Removing the one in use clears last too.
    assert store.remove(added.id) is True
    assert store.all() == [] and store.last is None
    assert store.remove(added.id) is False


def test_add_replaces_by_id_and_rename(tmp_path):
    store = connections.Connections(_ui(tmp_path))
    added = store.add(connections.new_ssh("box", "me@box"))
    renamed = store.rename(added.id, "the box")
    assert renamed.id == added.id and renamed.name == "the box"
    assert [c.name for c in store.all()] == ["the box"]
    assert store.rename(added.id, "") is None
    assert store.rename(connections.LOCAL_ID, "x") is None
    assert store.rename("nope", "x") is None


def test_add_refuses_local_and_a_full_list(tmp_path):
    store = connections.Connections(_ui(tmp_path))
    with pytest.raises(ValueError):
        store.add(connections.LOCAL)
    for i in range(connections.CONNECTIONS_MAX):
        store.add(connections.new_ssh(f"n{i}", "h"))
    with pytest.raises(ValueError, match="full"):
        store.add(connections.new_ssh("one more", "h"))


def test_last_names_only_a_connection_that_exists(tmp_path):
    store = connections.Connections(_ui(tmp_path, rows=[], last=ID_A))
    assert store.last is None
    store = connections.Connections(
        _ui(tmp_path, rows=[{"id": ID_A, "name": "box", "kind": "ssh", "target": "h"}], last=ID_A)
    )
    assert store.last is not None and store.last.id == ID_A


def test_names_refuse_unicode_format_characters():
    """Nit 8 of PR 623's review: a right-to-left override or a zero-width
    joiner would render in the banner and the dialog as it is."""
    assert not connections.valid_name("box‮")
    assert not connections.valid_name("b‍ox")
    assert connections.valid_name("Kästchen 🙂")
    assert connections.from_record(
        {"id": ID_A, "name": "x‮", "kind": "ssh", "target": "h"}
    ) is None
