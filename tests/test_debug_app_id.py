# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Which app ids count as a debug instance (collins.is_debug_app_id): the
debug build and anything derived from its id, never the release build or a
capture run's generated id. GTK-free, like the package root."""

from collins import APP_ID, DEBUG_APP_ID, is_debug_app_id


def test_the_debug_build_is():
    assert is_debug_app_id(DEBUG_APP_ID)


def test_an_id_derived_from_it_is_too():
    assert is_debug_app_id(DEBUG_APP_ID + ".E2E.r1")


def test_the_release_build_is_not():
    assert not is_debug_app_id(APP_ID)


def test_a_capture_run_off_the_release_id_is_not():
    # com.episode6.Collins.E2E.<run> starts with the release id, not the
    # debug one; a screenshot run is not a debug instance.
    assert not is_debug_app_id(APP_ID + ".E2E.r1")


def test_no_id_is_not():
    assert not is_debug_app_id(None)
    assert not is_debug_app_id("")
