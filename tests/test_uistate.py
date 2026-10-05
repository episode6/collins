# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The tabs this device had open on a service (split-service spec §3.21,
PR-1.12c): `open_tabs` in the per-service block of ui-state.json, in tab
order, session ids and ``pty:<id>`` for an unresolved one, kept per
service, cleaned on the way in (rule 5), written through `AppState` and
its mirror alike."""

import json

from collins import uistate

SID_A = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
SID_B = "1f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
SERVICE = "a" * 32
OTHER = "b" * 32


def test_open_tabs_round_trip_in_tab_order(app_state):
    state = app_state.AppState()
    assert state.get_open_tabs() == []
    state.set_open_tabs([SID_B, "pty:7", SID_A])
    assert app_state.AppState().get_open_tabs() == [SID_B, "pty:7", SID_A]
    ui = json.loads(app_state._ui_state_file().read_text(encoding="utf-8"))
    assert ui["services"][state.service_id]["open_tabs"] == [SID_B, "pty:7", SID_A]
    # Nothing of it reaches the service's file.
    assert not app_state._STATE_FILE.exists() or "open_tabs" not in app_state._STATE_FILE.read_text()


def test_open_tabs_are_kept_per_service(tmp_path):
    path = tmp_path / "ui-state.json"
    ui = uistate.UiState(path, {})
    ui.service(SERVICE)["open_tabs"] = [SID_A]
    ui.service(OTHER)["open_tabs"] = ["pty:3"]
    ui.save()
    again = uistate.UiState(path, {})
    assert again.service(SERVICE)["open_tabs"] == [SID_A]
    assert again.service(OTHER)["open_tabs"] == ["pty:3"]


def test_open_tabs_from_the_file_are_cleaned(tmp_path):
    path = tmp_path / "ui-state.json"
    path.write_text(json.dumps({"services": {SERVICE: {"open_tabs": [
        SID_A, 7, "", "pty:", "pty:x", "pty:0", "pty:12", SID_A, "a\nb", "x" * 200, SID_B,
    ]}}}), encoding="utf-8")
    ui = uistate.UiState(path, {})
    assert ui.service(SERVICE)["open_tabs"] == [SID_A, "pty:12", SID_B]


def test_a_block_with_no_list_reads_empty(tmp_path):
    path = tmp_path / "ui-state.json"
    path.write_text(json.dumps({"services": {SERVICE: {"open_tabs": "nope"}}}), encoding="utf-8")
    assert uistate.UiState(path, {}).service(SERVICE)["open_tabs"] == []


def test_the_list_is_bounded():
    entries = [f"pty:{n}" for n in range(1, uistate.OPEN_TABS_MAX + 50)]
    assert len(uistate.clean_open_tabs(entries)) == uistate.OPEN_TABS_MAX


def test_a_pty_entry_names_its_pty():
    assert uistate.open_tab_pty("pty:42") == 42
    assert uistate.open_tab_pty(SID_A) is None
    assert uistate.open_tab_pty("pty:") is None


def test_an_unchanged_list_is_not_rewritten(app_state):
    state = app_state.AppState()
    state.set_open_tabs([SID_A])
    path = app_state._ui_state_file()
    before = path.stat().st_mtime_ns
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")  # a known mtime
    stamp = path.stat().st_mtime_ns
    state.set_open_tabs([SID_A])
    assert path.stat().st_mtime_ns == stamp
    assert stamp >= before


def test_the_mirror_reads_and_writes_this_devices_block(app_state, tmp_path):
    from collins.remotestate import RemoteState

    class _Link:
        def on(self, *_args):
            pass

    ui_path = tmp_path / "client-ui.json"
    ui_path.write_text(json.dumps({"services": {SERVICE: {"open_tabs": [SID_A, "pty:3"]}}}),
                       encoding="utf-8")
    ui = uistate.UiState(ui_path, app_state.device_defaults())
    state = RemoteState(_Link(), ui=ui)
    # No service named yet (the snapshot names it): nothing to read or write.
    assert state.get_open_tabs() == []
    state.set_open_tabs([SID_B])
    assert json.loads(ui_path.read_text(encoding="utf-8"))["services"][SERVICE]["open_tabs"] == [
        SID_A, "pty:3"
    ]
    state.service_id = SERVICE
    assert state.get_open_tabs() == [SID_A, "pty:3"]
    state.set_open_tabs([SID_B])
    saved = json.loads(ui_path.read_text(encoding="utf-8"))
    assert saved["services"][SERVICE]["open_tabs"] == [SID_B]


def test_a_pty_entry_outside_the_protocols_range_never_reaches_an_attach():
    huge = "pty:" + "9" * 100
    assert uistate.open_tab_pty(huge) is None
    assert uistate.open_tab_pty("pty:4294967296") is None  # 2**32
    assert uistate.open_tab_pty("pty:4294967295") == 2**32 - 1
    assert uistate.open_tab_pty("pty:\u0661") is None  # a non-ASCII digit
    assert uistate.clean_open_tabs([huge, "pty:4294967296", "pty:3"]) == ["pty:3"]
