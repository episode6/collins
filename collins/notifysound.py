# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The notification sound: played beside an in-app card, and nowhere else.

Playback goes through GStreamer — a `playbin` on a file URI, one pipeline
kept and reused, put back to NULL when the file ends or fails — behind a
soft dependency: the typelib is imported inside a try, and without it
`play()` falls back to the display's beep and `available()` says so, so the
preferences row can say why. Why GStreamer rather than the two nearer
options: Gtk.MediaFile needs a GTK media backend module that a fresh desktop
does not have (Ubuntu's libgtk-4-media-gstreamer is pulled in by nothing),
while `gir1.2-gstreamer-1.0` and `gir1.2-gst-plugins-base-1.0` arrive with
the desktop's own GNOME apps on Ubuntu and Fedora alike; and GSound
(libcanberra) would be a hard dependency for a feature that has to degrade
gracefully.

Which file to play is notifycenter.sound_file's decision — "default" is the
desktop's own message sound, walked from its sound theme at play time —
and this module only asks the desktop two things on its way there: the
theme's name, and whether event sounds are switched on at all, both read
off org.gnome.desktop.sound when that schema exists. A desktop with no such
schema plays everything; the desktop's own "mute UI sounds" mutes ours.

Two rules keep a burst of bells from being a burst of chimes. play() is
*debounced*: a second call within DEBOUNCE_MS of the first is dropped. And
it is *single-flight*: while the pipeline is still playing, another call is
dropped too. The preferences row's ▶ button passes `force` to hear the
choice regardless. Nothing here blocks the main loop: GStreamer runs its
own threads, and the bus watch is a GLib source on ours.
"""

from __future__ import annotations

import logging
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib  # noqa: E402

try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402
except (ImportError, ValueError):  # no typelib: the beep does the job
    Gst = None

from . import notifycenter  # noqa: E402

log = logging.getLogger(__name__)

# A second play() this soon after the first is the same burst.
DEBOUNCE_MS = 300
# If GStreamer never reports the end of a file (a sink that stalls), the
# single-flight gate opens again on its own after this long; every sound
# this plays is a chime, not a track.
STUCK_SECONDS = 10

# The desktop's sound settings, when the desktop has them.
SOUND_SCHEMA = "org.gnome.desktop.sound"
THEME_KEY = "theme-name"
EVENT_SOUNDS_KEY = "event-sounds"

# The package the preferences row names when the typelib is missing: the
# Debian/Ubuntu name, which is where the beep fallback is likeliest to be
# met (Fedora's gstreamer1 is Recommended by the RPM and installed by dnf).
GSTREAMER_PACKAGE = "gir1.2-gstreamer-1.0"

# What play() did — for the headless check and the row's ▶, which can't
# hear anything.
PLAYED = "played"  # the file is playing
BEEPED = "beeped"  # no file, or no GStreamer: the display's beep
SILENT = "silent"  # the setting asks for nothing at all
MUTED = "muted"  # the desktop's event sounds are off
DEBOUNCED = "debounced"  # too soon after the last one
BUSY = "busy"  # the last one is still playing


def available() -> bool:
    """Whether GStreamer is here to play a file. False means every play()
    is the beep, and the preferences row says so."""
    return Gst is not None


def _sound_settings() -> Gio.Settings | None:
    """org.gnome.desktop.sound, or None where the desktop has no such
    schema — looked up rather than constructed, because Gio.Settings.new on
    a schema that isn't installed aborts the process."""
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SOUND_SCHEMA, True) is None:
        return None
    return Gio.Settings.new(SOUND_SCHEMA)


def theme_name() -> str:
    """The desktop's sound theme ("Yaru", "freedesktop"), "" without one."""
    settings = _sound_settings()
    if settings is None or not settings.get_property("settings-schema").has_key(THEME_KEY):
        return ""
    return settings.get_string(THEME_KEY) or ""


def event_sounds_enabled() -> bool:
    """The desktop's event-sounds switch; on where there is no switch."""
    settings = _sound_settings()
    if settings is None or not settings.get_property("settings-schema").has_key(EVENT_SOUNDS_KEY):
        return True
    return bool(settings.get_boolean(EVENT_SOUNDS_KEY))


def beep() -> None:
    """The display's beep: what a bell was before there was a sound, and
    what a sound falls back to."""
    display = Gdk.Display.get_default()
    if display is not None:
        display.beep()


class _Player:
    """The one pipeline, and the two gates in front of it."""

    def __init__(self) -> None:
        self._pipeline = None
        self._last_play = 0.0  # monotonic seconds
        self._playing = False
        self._generation = 0  # which play() the stuck-guard belongs to

    def play(self, value, *, force: bool = False) -> str:
        if notifycenter.sound_is_silent(value):
            return SILENT
        if not event_sounds_enabled():
            return MUTED
        now = time.monotonic()
        if not force:
            if now - self._last_play < DEBOUNCE_MS / 1000:
                return DEBOUNCED
            if self._playing:
                return BUSY
        self._last_play = now
        path = notifycenter.sound_file(value, theme_name())
        if not path or Gst is None:
            beep()
            return BEEPED
        try:
            self._start(path)
        except (GLib.Error, RuntimeError) as failure:
            log.warning("notification sound %s failed to start: %s", path, failure)
            self._playing = False
            beep()
            return BEEPED
        return PLAYED

    def _start(self, path: str) -> None:
        if self._pipeline is None:
            if not Gst.is_initialized():
                Gst.init(None)
            pipeline = Gst.ElementFactory.make("playbin", "collins-notification-sound")
            if pipeline is None:
                raise RuntimeError("no playbin element (gstreamer1.0-plugins-base missing?)")
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_message)
            self._pipeline = pipeline
        # A restart (the ▶ button mid-chime) has to pass through NULL: a
        # playbin's uri can only be set while it is stopped.
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.set_property("uri", Gst.filename_to_uri(path))
        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("the pipeline refused to play")
        self._playing = True
        self._generation += 1
        generation = self._generation
        GLib.timeout_add_seconds(STUCK_SECONDS, self._unstick, generation)

    def _unstick(self, generation: int) -> bool:
        if generation == self._generation and self._playing:
            log.warning("notification sound never finished; releasing the pipeline")
            self._stop()
        return GLib.SOURCE_REMOVE

    def _on_message(self, _bus, message) -> None:
        kind = message.type
        if kind == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            log.warning("notification sound error: %s (%s)", error, debug)
            self._stop()
        elif kind == Gst.MessageType.EOS:
            self._stop()

    def _stop(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
        self._playing = False

    @property
    def playing(self) -> bool:
        return self._playing


_player = _Player()


def play(value, *, force: bool = False) -> str:
    """Play the notification_sound setting's choice — "default", "none", or
    a path — and say what happened (one of the constants above). `force`
    skips the debounce and the single-flight gate, for the preferences
    row's ▶: the user asked to hear it now. It never skips the desktop's
    mute, or a "none" the user chose."""
    return _player.play(value, force=force)


def playing() -> bool:
    """Whether the pipeline is mid-sound (the single-flight gate's state),
    for the headless check."""
    return _player.playing
