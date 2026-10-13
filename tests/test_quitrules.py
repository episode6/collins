"""The quit's GTK-free rules (PR 605's review, should-fixes 2 and 5)."""

from collins import quitrules
from collins.quitrules import DETACH, RELEASE, records_as_open, tab_fate


def fate(**facts):
    base = {"behavior": "detach", "has_pty": True, "cli_exited": False, "unstarted": False}
    return tab_fate(**{**base, **facts})


def open_entry(**facts):
    base = {"has_pty": True, "cli_exited": False, "unstarted": False, "settling": False}
    return records_as_open(**{**base, **facts})


def test_a_plain_quit_detaches_a_running_session_and_records_it():
    assert fate() == DETACH
    assert open_entry() is True


def test_stop_sessions_releases_every_pty():
    assert fate(behavior="exit") == RELEASE
    assert fate(behavior="exit", unstarted=True) == RELEASE


def test_a_shell_left_after_its_cli_is_released_whatever_the_setting():
    for behavior in ("detach", "ask", "hide", "background", "exit"):
        assert fate(behavior=behavior, cli_exited=True) == RELEASE
    assert open_entry(cli_exited=True) is False


def test_an_unstarted_new_thread_is_torn_down_and_never_recorded():
    """Should-fix 2: an unstarted CLI at its empty first prompt has a
    running command, so the old rule detached it and the next launch
    reopened a 'New session' row for every thread opened and abandoned."""
    assert fate(unstarted=True) == RELEASE
    assert fate(behavior="ask", unstarted=True) == RELEASE
    assert open_entry(unstarted=True) is False


def test_a_tab_whose_cli_is_still_coming_up_counts_as_open():
    """Should-fix 5: within the first second after a spawn or an attach the
    mirror's `running_command` is still False; the tab is open all the
    same, so a quit then keeps it and records it."""
    assert fate(cli_exited=False) == DETACH
    assert open_entry(cli_exited=False) is True


def test_a_tab_with_no_pty_or_mid_close_is_nothing_to_the_quit():
    assert fate(has_pty=False) is None
    assert open_entry(has_pty=False) is False
    assert open_entry(settling=True) is False


def test_the_module_is_gtk_free():
    import sys

    assert not any(name.startswith("gi.repository.Gtk") for name in sys.modules)
    assert quitrules.__doc__


# -- the dialog's words (should-fix 3) -----------------------------------------------


def test_the_heading_counts_the_agents_not_a_busy_panel():
    from collins.quitrules import quit_heading

    assert quit_heading(1, False) == "1 session keeps running in the Collins service"
    assert quit_heading(3, True) == "3 sessions keep running in the Collins service"
    # A tab busy only in its terminal panel is no session that keeps running.
    assert quit_heading(0, True) == "A command is running in a terminal panel"


def test_the_body_names_a_panels_command_only_when_one_runs():
    from collins.quitrules import quit_body

    quiet = quit_body(other_windows=False, panel_busy=False, tray_present=True, first_notice=False)
    busy = quit_body(other_windows=False, panel_busy=True, tray_present=True, first_notice=False)
    assert "terminal panel" not in quiet
    assert "terminal panel" in busy and "Only Keep Running keeps it" in busy
    assert busy.index("terminal panel") < busy.index("Keep Running hides")
    assert quiet.startswith("Quit closes Collins")
    assert quit_body(other_windows=True, panel_busy=False, tray_present=True,
                     first_notice=False).startswith("This window closes")


def test_the_bodys_optional_sentences():
    from collins.quitrules import quit_body

    body = quit_body(other_windows=False, panel_busy=False, tray_present=False, first_notice=True)
    assert "Without a status icon" in body and "status icon leaves with the window" in body
    assert body.index("Without a status icon") < body.index("status icon leaves")
    base = quit_body(other_windows=False, panel_busy=False, tray_present=True, first_notice=False)
    assert "Without a status icon" not in base and "leaves with the window" not in base
