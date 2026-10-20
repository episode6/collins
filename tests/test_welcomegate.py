# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The welcome dialog's gate (collins.welcomegate): once per install, and on
every launch that can't find the CLI. GTK-free, like the module."""

from collins import welcomegate


class _State:
    def __init__(self, **settings):
        self.settings = settings

    def get_setting(self, key):
        return self.settings.get(key)


def test_a_fresh_install_owes_the_welcome():
    assert welcomegate.should_show(_State(), cli_found=True)


def test_an_install_without_the_key_owes_it_too():
    # An upgrade from before the setting existed: no key, same as unseen —
    # the disclosure is as new to that install as to a fresh one.
    assert welcomegate.should_show(_State(gh_welcome_dismissed=True), cli_found=True)


def test_a_seen_welcome_stays_down():
    assert not welcomegate.should_show(_State(welcome_seen=True), cli_found=True)


def test_a_missing_cli_asks_again_seen_or_not():
    # The CLI ask has no "later": a launch that can't find claude shows the
    # dialog whatever welcome_seen says.
    assert welcomegate.should_show(_State(welcome_seen=True), cli_found=False)
    assert welcomegate.should_show(_State(), cli_found=False)


def test_cli_found_is_the_services_word_when_not_given(monkeypatch):
    """Not this process's PATH: a window launched from the desktop lacks
    ~/.local/bin while its service runs claude fine (the every-launch
    welcome this replaced)."""
    monkeypatch.setattr(welcomegate.remotecli, "_found", "")
    assert welcomegate.should_show(_State(welcome_seen=True))
    monkeypatch.setattr(welcomegate.remotecli, "_found", "/home/u/.local/bin/claude")
    monkeypatch.setenv("PATH", "/nowhere")
    assert not welcomegate.should_show(_State(welcome_seen=True))


def test_a_service_that_never_said_is_not_missing_a_cli(monkeypatch):
    # An older service (no `cli` in its status): no ask it couldn't take.
    monkeypatch.setattr(welcomegate.remotecli, "_found", None)
    assert not welcomegate.should_show(_State(welcome_seen=True))


def test_the_setting_name_is_the_one_state_defaults():
    from collins.state import DEFAULT_SETTINGS

    assert welcomegate.SEEN_SETTING == "welcome_seen"
    assert DEFAULT_SETTINGS[welcomegate.SEEN_SETTING] is False
