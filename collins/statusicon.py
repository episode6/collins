# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The status icon itself: a StatusNotifierItem, spoken straight over D-Bus.

GTK4 dropped `GtkStatusIcon` and `libayatana-appindicator3` is GTK3-only — it
cannot be loaded into this process at all — so the item is put on the bus by
hand with `Gio.DBusConnection`, which needs no toolkit. Two interfaces:
`org.kde.StatusNotifierItem` (the item, at /StatusNotifierItem) and
`com.canonical.dbusmenu` (its menu, at /MenuBar).

Everything this module *decides* it asks traymodel for; what is left here is
plumbing, and most of it is shaped by how the host on the target desktop —
GNOME's `ubuntu-appindicators@ubuntu.com` — actually behaves:

- **Property changes travel as `New*` signals.** The host maps `NewStatus` →
  `Status` and re-`Get`s it; `PropertiesChanged` is ignored outright ("The
  Author of the spec didn't like the PropertiesChanged signal, so he invented
  his own", appIndicator.js). Emit the signal or nothing updates.
- **A `Passive` item defers its property refreshes** until it goes `Active`
  again, which is why traymodel.status_for lets unread outrank a session count
  of zero rather than reporting a badge nobody would see.
- **Click routing keys on introspection finding `Activate`**, not on
  `ItemIsMenu`: with it exported, left single click opens the menu, left
  double click calls `Activate`, middle calls `SecondaryActivate`. There is no
  left-click-to-show to be had; the menu is the primary interface.
- **Icons are resolved in the host's process**, whose icon theme knows nothing
  about the `data/icons` path a source checkout adds to its own. So the
  artwork ships as `IconPixmap` — decoded here, handed over as ARGB32 — with
  `IconName` and `IconThemePath` alongside as a courtesy to hosts that would
  rather look it up themselves.

The unread badge is composited into those pixmaps rather than sent as text:
the shell has no badge slot, and the one text affordance (`XAyatanaLabel`)
renders *beside* the icon as a panel-widening label. The count is drawn fresh
at every exported size so the host never scales a numeral, and the same
repaint broadcasts `com.canonical.Unity.LauncherEntry.Update` so Ubuntu Dock
badges the launcher icon too.

Where there is no watcher on the bus there is no host, and nothing appears.
That is a supported outcome rather than a bug to work around: `available()`
says so, Preferences repeats it, and the item quietly waits for a watcher to
show up (an extension being switched on, an X11 shell restart) through the
same name watch it re-registers from.
"""

from __future__ import annotations

import logging
import math
import os
import sys

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import traymodel  # noqa: E402
from .traymodel import TrayView  # noqa: E402

log = logging.getLogger(__name__)

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_INTERFACE = "org.kde.StatusNotifierItem"
ITEM_PATH = "/StatusNotifierItem"
MENU_INTERFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"

# The sizes the item exports. Panels ask for 22 or 24 logical pixels and
# double both on HiDPI; handing over all four means the host never scales one
# of ours — the badge is drawn fresh at each size, and a scaled numeral is a
# smeared one.
ICON_SIZES = (22, 24, 32, 44)

# The badge inherits nothing: it has to read against whatever is behind it —
# a light panel, a dark one, the icon's own art underneath — so it carries
# its own background (GNOME's destructive red) and its own numeral color.
BADGE_FILL = (0.878, 0.106, 0.141)  # #e01b24
BADGE_INK = (1.0, 1.0, 1.0)

# Ubuntu Dock (and KDE's task manager) listen for this broadcast and badge
# the launcher icon with `count`. Subscribers key on the app uri in the
# signal's body, not on the sender's object path, so the path is just ours.
LAUNCHER_INTERFACE = "com.canonical.Unity.LauncherEntry"
LAUNCHER_PATH = "/com/canonical/unity/launcherentry/collins"

# What a session row says about itself, in the one column a shell menu offers:
# its label. Filled for working (the barber pole is moving), hollow for a run
# that finished and hasn't been looked at.
MARKER_GLYPHS = {
    traymodel.MARKER_WORKING: "●",
    traymodel.MARKER_UNREAD: "○",
}

_ITEM_XML = f"""
<node>
  <interface name="{ITEM_INTERFACE}">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <method name="ProvideXdgActivationToken">
      <arg name="token" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewIconThemePath">
      <arg name="icon_theme_path" type="s"/>
    </signal>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
    <signal name="NewMenu"/>
  </interface>
</node>
"""

_MENU_XML = f"""
<node>
  <interface name="{MENU_INTERFACE}">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{{sv}}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{{sv}})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{{sv}})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""


def register_object(bus: Gio.DBusConnection, path: str, xml: str, on_call, on_get) -> int:
    """Export one hand-written interface, on whichever binding this PyGObject
    has. `register_object` still works everywhere and is deprecated in favour
    of `register_object_with_closures2`, which only newer builds have."""
    info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
    register = getattr(bus, "register_object_with_closures2", bus.register_object)
    return register(path, info, on_call, on_get, None)


# -- availability -------------------------------------------------------------


def available() -> bool:
    """Whether a status-icon host is on the bus right now.

    A synchronous round trip, for the one caller that needs the answer before
    it can draw: Preferences, whose row would otherwise open blank and fill in
    a frame later. Everything else follows `watch_availability` instead.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (WATCHER_NAME,)),
            GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error:
        return False
    return bool(reply.unpack()[0])


def watch_availability(on_change) -> int:
    """Follow whether a status-icon host is on the bus, calling *on_change*
    with the answer whenever it moves (and once with the starting state).

    Live rather than read once at startup because it genuinely moves under a
    running app: GNOME extensions get switched on and off from the Extensions
    app, and an X11 shell restart takes the watcher away and brings it back.
    Returns a watch id for `unwatch`.
    """
    return Gio.bus_watch_name(
        Gio.BusType.SESSION,
        WATCHER_NAME,
        Gio.BusNameWatcherFlags.NONE,
        lambda *_a: on_change(True),
        lambda *_a: on_change(False),
    )


def unwatch(watch_id: int) -> None:
    if watch_id:
        Gio.bus_unwatch_name(watch_id)


# -- artwork ------------------------------------------------------------------


def _argb_bytes(pixbuf: GdkPixbuf.Pixbuf) -> bytes:
    """One pixmap's pixels as the protocol wants them: ARGB32 in network byte
    order, rows packed tight (hosts assume a stride of width * 4)."""
    if not pixbuf.get_has_alpha():
        pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
    width, height = pixbuf.get_width(), pixbuf.get_height()
    stride = pixbuf.get_rowstride()
    pixels = pixbuf.get_pixels()
    out = bytearray(width * height * 4)
    for y in range(height):
        src = y * stride
        dst = y * width * 4
        for _x in range(width):
            out[dst] = pixels[src + 3]  # a
            out[dst + 1] = pixels[src]  # r
            out[dst + 2] = pixels[src + 1]  # g
            out[dst + 3] = pixels[src + 2]  # b
            src += 4
            dst += 4
    return bytes(out)


def _load_icon(icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """The app's icon, decoded at *size*, or None if the theme hasn't got it.

    Decoded from the theme's own file rather than rendered off a paintable:
    the file is as likely to be the scalable SVG as a PNG, and the pixbuf
    loader rasterizes either one straight to the size asked for.
    """
    display = Gdk.Display.get_default()
    if display is None:
        return None
    theme = Gtk.IconTheme.get_for_display(display)
    if not theme.has_icon(icon_name):
        return None
    paintable = theme.lookup_icon(
        icon_name, None, size, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.FORCE_REGULAR
    )
    gfile = paintable.get_file() if paintable is not None else None
    if gfile is None:
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_stream_at_scale(gfile.read(None), size, size, True, None)
    except GLib.Error:
        log.debug("status icon: %s not decodable at %dpx", icon_name, size)
        return None


def _surface_argb_bytes(surface: cairo.ImageSurface) -> bytes:
    """A rendered surface's pixels as the protocol wants them: ARGB32 in
    network byte order, straight alpha, rows packed tight.

    Cairo keeps its pixels premultiplied in native endianness, so each one is
    unpremultiplied and reordered on the way out — handing them over as-is
    would darken every translucent edge on the host's side.
    """
    width, height = surface.get_width(), surface.get_height()
    stride = surface.get_stride()
    pixels = surface.get_data()
    little = sys.byteorder == "little"
    out = bytearray(width * height * 4)
    dst = 0
    for y in range(height):
        row = y * stride
        for x in range(width):
            i = row + x * 4
            if little:
                b, g, r, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
            else:
                a, r, g, b = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
            if 0 < a < 255:
                r = min(255, r * 255 // a)
                g = min(255, g * 255 // a)
                b = min(255, b * 255 // a)
            out[dst], out[dst + 1], out[dst + 2], out[dst + 3] = a, r, g, b
            dst += 4
    return bytes(out)


def _draw_badge(ctx: cairo.Context, badge: str, size: int) -> None:
    """The unread count, drawn into the bottom-right quadrant.

    A filled disc that widens into a pill when the text does ("9+"), sized
    off the icon so every exported resolution gets its own crisp numeral. The
    text is measured before the shape is drawn: the shape fits the number,
    never the other way around.
    """
    diameter = max(round(size * 0.55), 11)
    layout = PangoCairo.create_layout(ctx)
    # Grayscale antialiasing, never the desktop's subpixel kind: LCD fringes
    # baked into a pixmap travel with it, and the panel it lands on is not
    # the display geometry they were hinted for.
    options = cairo.FontOptions()
    options.set_antialias(cairo.ANTIALIAS_GRAY)
    PangoCairo.context_set_font_options(layout.get_context(), options)
    desc = Pango.FontDescription("Sans Bold")
    desc.set_absolute_size(round(diameter * 0.76) * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(badge, -1)
    _ink, logical = layout.get_pixel_extents()
    width = min(max(diameter, logical.width + max(round(diameter * 0.45), 5)), size)
    x, y = size - width, size - diameter
    radius = diameter / 2
    ctx.new_path()
    ctx.arc(x + radius, y + radius, radius, 0.5 * math.pi, 1.5 * math.pi)
    ctx.arc(x + width - radius, y + radius, radius, 1.5 * math.pi, 0.5 * math.pi)
    ctx.close_path()
    ctx.set_source_rgb(*BADGE_FILL)
    ctx.fill()
    ctx.set_source_rgb(*BADGE_INK)
    ctx.move_to(
        x + (width - logical.width) / 2 - logical.x,
        y + (diameter - logical.height) / 2 - logical.y,
    )
    PangoCairo.show_layout(ctx, layout)


def _badged_surface(pixbuf: GdkPixbuf.Pixbuf, badge: str, size: int) -> cairo.ImageSurface:
    """The icon with the badge composited on: the artwork centered on a
    size×size canvas, the count drawn over its bottom-right corner."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(
        ctx,
        pixbuf,
        (size - pixbuf.get_width()) // 2,
        (size - pixbuf.get_height()) // 2,
    )
    ctx.paint()
    _draw_badge(ctx, badge, size)
    surface.flush()
    return surface


def icon_pixmaps(icon_name: str, badge: str = "") -> list[tuple[int, int, bytes]]:
    """The item's artwork at every exported size, largest last, with *badge*
    (if any) composited onto each. Empty when the icon can't be found at all,
    which leaves the host to try `IconName`."""
    pixmaps = []
    for size in ICON_SIZES:
        pixbuf = _load_icon(icon_name, size)
        if pixbuf is None:
            continue
        if badge:
            pixmaps.append((size, size, _surface_argb_bytes(_badged_surface(pixbuf, badge, size))))
        else:
            pixmaps.append((pixbuf.get_width(), pixbuf.get_height(), _argb_bytes(pixbuf)))
    return pixmaps


# -- the item -----------------------------------------------------------------


def menu_label(entry: traymodel.MenuEntry) -> str:
    """A menu row's text as DBusMenu wants it.

    Underscores mark mnemonics in this protocol, so a literal one is doubled —
    a project called `my_project` would otherwise lose it. The marker rides in
    the label because the alternative, `icon-name`, is resolved in the host's
    icon theme, which is exactly the lookup `IconPixmap` exists to sidestep.
    """
    label = entry.label.replace("_", "__")
    glyph = MARKER_GLYPHS.get(entry.marker, "")
    return f"{label}  {glyph}" if glyph else label


class StatusIcon:
    """One StatusNotifierItem, for as long as the setting says to have one.

    The app builds it, calls `start()`, and calls `refresh()` whenever the
    sessions move; `stop()` takes it off the bus. Nothing here reaches into
    the app — it asks `view_provider` for the state and the callbacks for the
    actions, so a headless test can drive the whole surface with five lambdas.
    """

    def __init__(
        self,
        *,
        app_id: str,
        title: str,
        icon_name: str,
        icon_theme_path: str = "",
        view_provider=None,
        on_show=None,
        on_focus=None,
        on_new_window=None,
        on_quit=None,
    ) -> None:
        self._app_id = app_id
        self._title = title
        self._icon_name = icon_name
        self._icon_theme_path = icon_theme_path
        self._view_provider = view_provider
        self._view = TrayView()
        self._on_show = on_show
        self._on_focus = on_focus
        self._on_new_window = on_new_window
        self._on_quit = on_quit

        self._bus: Gio.DBusConnection | None = None
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._name_id = 0
        self._watch_id = 0
        self._item_reg = 0
        self._menu_reg = 0
        self._owned = False
        self._registered = False
        self._revision = 1
        self._pixmaps: list[tuple[int, int, bytes]] | None = None
        self._pixmap_badge = ""
        # What the dock was last told, None for "nothing yet": the first
        # refresh always broadcasts, so a badge left behind by a crashed run
        # is cleared even when this one starts with nothing unread.
        self._dock_count: int | None = None
        self._launcher_uri = f"application://{app_id}.desktop"

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        """Put the item on the bus. False when the session bus itself is
        unreachable — the only failure that leaves nothing to retry."""
        if self._bus is not None:
            return True
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            log.warning("status icon: no session bus")
            return False
        try:
            self._item_reg = register_object(
                bus, ITEM_PATH, _ITEM_XML, self._item_call, self._item_get
            )
            self._menu_reg = register_object(
                bus, MENU_PATH, _MENU_XML, self._menu_call, self._menu_get
            )
        except GLib.Error:
            log.exception("status icon: objects not exportable")
            # The item may have gone up before the menu failed; a path left
            # exported with nothing behind it would answer a host that found
            # it later.
            for reg in (self._item_reg, self._menu_reg):
                if reg:
                    bus.unregister_object(reg)
            self._item_reg = self._menu_reg = 0
            return False
        self._bus = bus
        # Ask once before anyone can: the host reads every property the moment
        # it hears about us, and an item that starts out Passive would have to
        # be told twice over to become the Active one it already was.
        self.refresh()
        self._name_id = Gio.bus_own_name_on_connection(
            bus,
            self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            self._on_name_acquired,
            self._on_name_lost,
        )
        # The same watch does double duty: it re-registers with a watcher that
        # comes back (an extension re-enabled, an X11 shell restarted) and it
        # is what makes the first registration wait for one to exist.
        self._watch_id = watch_availability(self._on_watcher_changed)
        return True

    def stop(self) -> None:
        """Take the item off the bus. The host notices the name go away and
        drops the icon; there is no Unregister call in the protocol.

        The dock is told separately: its badge is keyed to the desktop id,
        not to a bus name, so nothing clears it for us when we go."""
        if self._bus is not None:
            self._dock_update(0)
        self._dock_count = None
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
            self._name_id = 0
        bus, self._bus = self._bus, None
        if bus is not None:
            for reg in (self._item_reg, self._menu_reg):
                if reg:
                    bus.unregister_object(reg)
        self._item_reg = self._menu_reg = 0
        self._owned = self._registered = False

    def _on_name_acquired(self, *_a) -> None:
        self._owned = True
        self._register_item()

    def _on_name_lost(self, *_a) -> None:
        self._owned = False
        self._registered = False

    def _on_watcher_changed(self, present: bool) -> None:
        """A watcher came or went. Only a departure clears the flag: the watch
        also fires once on the way up, for a watcher that may already have our
        registration, and clearing it there would register a second time."""
        if not present:
            self._registered = False
            return
        self._register_item()

    def _register_item(self) -> None:
        """Hand our bus name to the watcher. Called from both sides of the
        race — the name landing and the watcher appearing — because either can
        happen first, and the guard makes the second one a no-op."""
        if self._bus is None or not self._owned or self._registered:
            return
        self._registered = True
        self._bus.call(
            WATCHER_NAME,
            WATCHER_PATH,
            WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._bus_name,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_registered,
        )

    def _on_registered(self, bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error as err:
            # No watcher on this desktop, or one that refused us: leave the
            # flag down so the name watch can try again if one turns up.
            self._registered = False
            log.debug("status icon: not registered (%s)", err.message)

    # -- state -------------------------------------------------------------

    def refresh(self, announce: bool = True) -> bool:
        """Re-read the app's state and tell the host what moved. Returns
        whether the menu's layout changed.

        Only the properties that actually changed are announced: the host
        re-`Get`s a property for every `New*` it sees, and the menu's layout
        revision has to keep climbing for a rebuild to be believed. `announce`
        goes off for the one caller that reports the change in its own reply
        instead — AboutToShow, whose `needUpdate` is exactly this answer.
        """
        if self._view_provider is None:
            return False
        old, self._view = self._view, self._view_provider()
        moved = self._view.menu != old.menu
        if moved:
            self._revision += 1
        if self._bus is None or not announce:
            return moved
        if self._view.status != old.status:
            self._emit_item("NewStatus", GLib.Variant("(s)", (self._view.status,)))
        if self._view.badge != old.badge:
            # Both artworks carry the badge, and a host reads whichever its
            # Status picks — so both are announced when the count moves.
            self._emit_item("NewIcon", None)
            self._emit_item("NewAttentionIcon", None)
        if self._view.tooltip != old.tooltip:
            self._emit_item("NewToolTip", None)
        if moved:
            self._emit_menu("LayoutUpdated", GLib.Variant("(ui)", (self._revision, 0)))
        self._dock_update(self._view.unread)
        return moved

    def _dock_update(self, count: int) -> None:
        """Tell Ubuntu Dock (and anything else listening for LauncherEntry
        broadcasts) the unread count, once per change. The dock draws its own
        numeral over the launcher icon — a properly rendered badge for one
        signal — and hides it again on count-visible going false."""
        if count == self._dock_count:
            return
        self._dock_count = count
        self._emit(
            LAUNCHER_PATH,
            LAUNCHER_INTERFACE,
            "Update",
            GLib.Variant(
                "(sa{sv})",
                (
                    self._launcher_uri,
                    {
                        "count": GLib.Variant("x", count),
                        "count-visible": GLib.Variant("b", count > 0),
                    },
                ),
            ),
        )

    def _emit_item(self, signal: str, params: GLib.Variant | None) -> None:
        self._emit(ITEM_PATH, ITEM_INTERFACE, signal, params)

    def _emit_menu(self, signal: str, params: GLib.Variant | None) -> None:
        self._emit(MENU_PATH, MENU_INTERFACE, signal, params)

    def _emit(self, path: str, interface: str, signal: str, params) -> None:
        if self._bus is None:
            return
        try:
            self._bus.emit_signal(None, path, interface, signal, params)
        except GLib.Error:
            log.debug("status icon: %s not emitted", signal)

    # -- org.kde.StatusNotifierItem ----------------------------------------

    def _pixmap_variant(self) -> GLib.Variant:
        # Rendered when a host asks and kept until the badge moves — checked
        # here rather than invalidated on refresh, so a badge that changed in
        # an unannounced refresh (AboutToShow's) still reads back right.
        if self._pixmaps is None or self._pixmap_badge != self._view.badge:
            self._pixmaps = icon_pixmaps(self._icon_name, self._view.badge)
            self._pixmap_badge = self._view.badge
        return GLib.Variant(
            "a(iiay)", [(w, h, list(data)) for w, h, data in self._pixmaps]
        )

    def _item_get(self, _conn, _sender, _path, _iface, prop: str):
        view = self._view
        if prop == "Category":
            return GLib.Variant("s", "ApplicationStatus")
        if prop == "Id":
            return GLib.Variant("s", self._app_id)
        if prop == "Title":
            return GLib.Variant("s", self._title)
        if prop == "Status":
            return GLib.Variant("s", view.status)
        if prop == "WindowId":
            return GLib.Variant("i", 0)
        if prop == "IconThemePath":
            return GLib.Variant("s", self._icon_theme_path)
        if prop == "Menu":
            return GLib.Variant("o", MENU_PATH)
        if prop == "ItemIsMenu":
            # False so the host looks for Activate and routes a double click
            # to it; with it True the menu is all there is.
            return GLib.Variant("b", False)
        if prop in ("IconName", "AttentionIconName"):
            return GLib.Variant("s", self._icon_name)
        if prop in ("IconPixmap", "AttentionIconPixmap"):
            # One artwork for both states, badge and all: NeedsAttention and
            # a non-empty badge are the same fact (unread > 0), so whichever
            # property the host's Status sends it to, it shows the count.
            return self._pixmap_variant()
        if prop == "OverlayIconName":
            return GLib.Variant("s", "")
        if prop == "OverlayIconPixmap":
            return GLib.Variant("a(iiay)", [])
        if prop == "AttentionMovieName":
            return GLib.Variant("s", "")
        if prop == "ToolTip":
            # (icon name, icon pixmaps, title, description). GNOME's
            # appindicator host reads none of it — its proxy leaves ToolTip
            # out of the interface entirely — but KDE's tray and the Ayatana
            # hosts show it, and it costs one string.
            return GLib.Variant("(sa(iiay)ss)", ("", [], view.tooltip, ""))
        return None

    def _item_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method in ("Activate", "SecondaryActivate", "ContextMenu"):
            # Left double click, middle click, and a host with nowhere else to
            # send a right click: all of them mean "put Collins on screen".
            self._dispatch(self._on_show)
        elif method == "ProvideXdgActivationToken":
            # Wayland's "this click is allowed to raise a window" token. The
            # app presents through GTK, which asks for its own, so the token
            # is accepted and dropped rather than refused with an error.
            pass
        invocation.return_value(None)

    # -- com.canonical.dbusmenu --------------------------------------------

    def _menu_get(self, _conn, _sender, _path, _iface, prop: str):
        if prop == "Version":
            return GLib.Variant("u", 3)
        if prop == "TextDirection":
            rtl = Gtk.Widget.get_default_direction() == Gtk.TextDirection.RTL
            return GLib.Variant("s", "rtl" if rtl else "ltr")
        if prop == "Status":
            return GLib.Variant("s", "normal")
        if prop == "IconThemePath":
            return GLib.Variant("as", [self._icon_theme_path] if self._icon_theme_path else [])
        return None

    def _entry(self, item_id: int) -> traymodel.MenuEntry | None:
        return next((e for e in self._view.menu if e.id == item_id), None)

    @staticmethod
    def _properties(entry: traymodel.MenuEntry) -> dict[str, GLib.Variant]:
        if entry.separator:
            return {"type": GLib.Variant("s", "separator")}
        return {"label": GLib.Variant("s", menu_label(entry))}

    def _root_layout(self) -> tuple:
        """The whole menu as one `(ia{sv}av)` value — a plain tuple, because it
        is nested inside GetLayout's own reply tuple. Only the `av` children
        are boxed as variants; box the root as well and the outer construction
        refuses it."""
        children = [
            GLib.Variant("(ia{sv}av)", (entry.id, self._properties(entry), []))
            for entry in self._view.menu
        ]
        return (0, {"children-display": GLib.Variant("s", "submenu")}, children)

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method == "GetLayout":
            parent_id = params.unpack()[0]
            # Flat menu: only the root has children, and anything else asked
            # for answers for itself with none.
            layout = self._root_layout() if parent_id == 0 else (parent_id, {}, [])
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self._revision, layout)))
            return
        if method == "GetGroupProperties":
            ids = params.unpack()[0]
            rows = [
                (entry.id, self._properties(entry))
                for entry in self._view.menu
                if not ids or entry.id in ids
            ]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (rows,)))
            return
        if method == "GetProperty":
            item_id, name = params.unpack()[:2]
            entry = self._entry(item_id)
            value = self._properties(entry).get(name) if entry is not None else None
            invocation.return_value(
                GLib.Variant("(v)", (value if value is not None else GLib.Variant("s", ""),))
            )
            return
        if method == "Event":
            item_id, event_id = params.unpack()[:2]
            if event_id == "clicked":
                self._activate(item_id)
            invocation.return_value(None)
            return
        if method == "EventGroup":
            for item_id, event_id, _data, _ts in params.unpack()[0]:
                if event_id == "clicked":
                    self._activate(item_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
            return
        if method == "AboutToShow":
            # The menu is rebuilt from live state as it opens, so a session
            # that arrived since the last repaint is in the list the user
            # sees. Whether it changed is the answer this call is for, so the
            # refresh keeps its LayoutUpdated to itself.
            invocation.return_value(GLib.Variant("(b)", (self.refresh(announce=False),)))
            return
        if method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
            return
        invocation.return_value(None)

    # -- dispatch ----------------------------------------------------------

    def _activate(self, item_id: int) -> None:
        entry = self._entry(item_id)
        if entry is None or entry.separator:
            return
        if entry.action == traymodel.ACTION_SHOW:
            self._dispatch(self._on_show)
        elif entry.action == traymodel.ACTION_FOCUS:
            self._dispatch(self._on_focus, entry.target)
        elif entry.action == traymodel.ACTION_NEW_WINDOW:
            self._dispatch(self._on_new_window)
        elif entry.action == traymodel.ACTION_QUIT:
            self._dispatch(self._on_quit)

    @staticmethod
    def _dispatch(callback, *args) -> None:
        """Run a menu action after the D-Bus reply, never inside it: Quit puts
        up dialogs and waits for them, and a method call that hasn't returned
        holds the host's menu open behind them."""
        if callback is None:
            return
        GLib.idle_add(lambda: (callback(*args), GLib.SOURCE_REMOVE)[1])
