#!/usr/bin/env python3
"""End-to-end check for the status icon's D-Bus surface — run on a dev machine.

Drives a real `statusicon.StatusIcon` against a real bus, with a stub
`org.kde.StatusNotifierWatcher` standing in for the desktop's own. That stub is
what makes the item testable at all: the real host is a GNOME Shell extension,
and the headless GNOME the capture wrapper starts runs with none, so there is
nothing on that bus to register with.

    dbus-run-session -- python3 scripts/check_status_icon.py

Asserts what a unit test can't: that the item registers, that its properties
read back as the model computed them, that flipping a session to unread emits
NewStatus and reports NeedsAttention, that the menu's GetLayout matches
traymodel's layout, and that clicking a session row dispatches focus-session
with the right id.

None of this is reachable from pytest — statusicon imports the GTK stack, which
tests/conftest.py blocks to match CI — and the one thing it still can't prove
is what the icon *looks* like in a panel. That stays a manual check against the
live session:

    gdbus call --session --dest org.kde.StatusNotifierWatcher \\
        --object-path /StatusNotifierWatcher --method \\
        org.freedesktop.DBus.Properties.Get \\
        org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from collins import statusicon, traymodel  # noqa: E402

WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem">
      <arg name="service" type="s" direction="in"/>
    </method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
  </interface>
</node>
"""

# The name the item owns, and the only address the checks below use: an item
# is found by its well-known name, exactly as a host finds it.
BUS_NAME = f"org.kde.StatusNotifierItem-{os.getpid()}-1"

failures = []


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(label)


class StubWatcher:
    """Just enough watcher for an item to find: it records who registers."""

    def __init__(self):
        self.registered = []
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        statusicon.register_object(
            self.bus, statusicon.WATCHER_PATH, WATCHER_XML, self._call, self._get
        )
        Gio.bus_own_name_on_connection(
            self.bus, statusicon.WATCHER_NAME, Gio.BusNameOwnerFlags.NONE, None, None
        )

    def _call(self, _c, sender, _p, _i, method, params, invocation):
        if method == "RegisterStatusNotifierItem":
            self.registered.append((sender, params.unpack()[0]))
        invocation.return_value(None)

    def _get(self, _c, _s, _p, _i, prop):
        if prop == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", [name for _sender, name in self.registered])
        if prop == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", True)
        return GLib.Variant("i", 0)


def spin(predicate, seconds=5.0):
    """Run the main loop until *predicate* holds, or give up."""
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(seconds * 1_000_000)
    while GLib.get_monotonic_time() < deadline:
        if predicate():
            return True
        context.iteration(False)
        GLib.usleep(2000)
    return predicate()


def call(bus, path, interface, method, params, reply_type=None):
    """One D-Bus call, asynchronously, with the main loop turning underneath.

    Never call_sync: the item being tested lives in *this* process, so its
    property getters and method handlers only run when this loop does — a
    synchronous call would sit waiting for a reply it is itself blocking.
    """
    box = {}
    bus.call(
        BUS_NAME, path, interface, method, params,
        GLib.VariantType(reply_type) if reply_type else None,
        Gio.DBusCallFlags.NONE, 5000, None,
        lambda b, res: box.setdefault("r", _finish(b, res)),
    )
    if not spin(lambda: "r" in box):
        raise TimeoutError(f"{method} never answered")
    result = box["r"]
    if isinstance(result, Exception):
        raise result
    return result.unpack() if result is not None else None


def _finish(bus, result):
    try:
        return bus.call_finish(result)
    except GLib.Error as err:
        return err


def main():
    # A display, so the icon lookup behind IconPixmap has a theme to ask.
    Gtk.init()

    watcher = StubWatcher()
    check("stub watcher on the bus", statusicon.available())

    sessions = [
        traymodel.TraySession("s-alpha", "alpha-widgets", "refactor store", busy=True,
                              last_active=200.0),
        traymodel.TraySession("s-beta", "podcast-hacker", "fix CI", last_active=100.0),
    ]
    state = {"sessions": sessions, "placeholders": 0, "unread": 0}
    clicks = []

    def view():
        return traymodel.tray_view(
            state["sessions"], state["placeholders"], state["unread"]
        )

    icon = statusicon.StatusIcon(
        app_id="com.episode6.Collins",
        title="Collins",
        icon_name="com.episode6.Collins",
        view_provider=view,
        on_show=lambda: clicks.append(("show", "")),
        on_focus=lambda sid: clicks.append(("focus", sid)),
        on_new_window=lambda: clicks.append(("new-window", "")),
        on_quit=lambda: clicks.append(("quit", "")),
    )
    check("item started", icon.start())
    check("registered with the watcher", spin(lambda: bool(watcher.registered)),
          str(watcher.registered))
    # Owning the name and seeing the watcher appear are two arrivals in a race,
    # and both lead here: exactly one of them may end up registering.
    spin(lambda: len(watcher.registered) > 1, 1.0)
    check("registered exactly once", len(watcher.registered) == 1, str(watcher.registered))

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def item_prop(name):
        return call(
            bus, statusicon.ITEM_PATH, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (statusicon.ITEM_INTERFACE, name)), "(v)",
        )[0]

    def menu_call(method, params, reply_type):
        return call(bus, statusicon.MENU_PATH, statusicon.MENU_INTERFACE, method,
                    params, reply_type)

    check("Status is Active with tabs open", item_prop("Status") == "Active",
          item_prop("Status"))
    check("Id is the app id", item_prop("Id") == "com.episode6.Collins")
    check("Menu points at /MenuBar", item_prop("Menu") == statusicon.MENU_PATH)
    check("ItemIsMenu is false (so Activate is used)", item_prop("ItemIsMenu") is False)
    tooltip = item_prop("ToolTip")[2]
    check("tooltip counts the sessions", tooltip == "Collins — 2 sessions, 1 working",
          tooltip)

    # Introspection is how the host decides a double click has somewhere to go.
    xml = call(bus, statusicon.ITEM_PATH, "org.freedesktop.DBus.Introspectable",
               "Introspect", None, "(s)")[0]
    node = Gio.DBusNodeInfo.new_for_xml(xml)
    iface = node.lookup_interface(statusicon.ITEM_INTERFACE)
    check("Activate is introspectable", iface is not None and
          iface.lookup_method("Activate") is not None)

    pixmaps = item_prop("IconPixmap")
    check("icon exports a pixmap per size", len(pixmaps) == len(statusicon.ICON_SIZES),
          f"{[(w, h) for w, h, _d in pixmaps]}")
    if pixmaps:
        w, h, data = pixmaps[0]
        check("pixmap is packed ARGB32", len(data) == w * h * 4, f"{len(data)} bytes")
    # A host tries the name before the pixmap, and the one that wins the first
    # time keeps the icon actor for good — so with artwork to send, the name
    # stays empty whether or not a badge is up.
    check("IconName is empty while artwork is exported", item_prop("IconName") == "",
          item_prop("IconName"))
    check("AttentionIconName is empty while artwork is exported",
          item_prop("AttentionIconName") == "", item_prop("AttentionIconName"))

    # -- the menu ----------------------------------------------------------

    revision, layout = menu_call("GetLayout", GLib.Variant("(iias)", (0, -1, [])),
                                 "(u(ia{sv}av))")
    root_id, root_props, children = layout
    check("root is id 0 with a submenu", root_id == 0 and
          root_props.get("children-display") == "submenu")
    expected = view().menu
    check("layout has a child per model entry", len(children) == len(expected),
          f"{len(children)} vs {len(expected)}")
    labels = [c[1].get("label") for c in children]
    model_labels = [statusicon.menu_label(e) if not e.separator else None for e in expected]
    check("labels match the model", labels == model_labels, f"{labels}")
    types = [c[1].get("type") for c in children]
    check("separators are typed", types == [
        "separator" if e.separator else None for e in expected], f"{types}")

    focus_id = next(e.id for e in expected if e.action == traymodel.ACTION_FOCUS
                    and e.target == "s-beta")
    call(bus, statusicon.MENU_PATH, statusicon.MENU_INTERFACE, "Event",
         GLib.Variant("(isvu)", (focus_id, "clicked", GLib.Variant("i", 0), 0)))
    check("a session row focuses its session", spin(lambda: ("focus", "s-beta") in clicks),
          str(clicks))

    quit_id = next(e.id for e in expected if e.action == traymodel.ACTION_QUIT)
    call(bus, statusicon.MENU_PATH, statusicon.MENU_INTERFACE, "Event",
         GLib.Variant("(isvu)", (quit_id, "clicked", GLib.Variant("i", 0), 0)))
    check("Quit dispatches", spin(lambda: ("quit", "") in clicks), str(clicks))

    call(bus, statusicon.ITEM_PATH, statusicon.ITEM_INTERFACE, "Activate",
         GLib.Variant("(ii)", (0, 0)))
    check("Activate shows the window", spin(lambda: ("show", "") in clicks), str(clicks))

    # -- state moving ------------------------------------------------------

    seen = []
    bus.signal_subscribe(BUS_NAME, statusicon.ITEM_INTERFACE, "NewStatus", statusicon.ITEM_PATH,
                         None, Gio.DBusSignalFlags.NONE,
                         lambda *a: seen.append(a[-1].unpack()[0]))
    icons = []
    bus.signal_subscribe(BUS_NAME, statusicon.ITEM_INTERFACE, "NewIcon", statusicon.ITEM_PATH,
                         None, Gio.DBusSignalFlags.NONE, lambda *a: icons.append(True))
    dock = []
    bus.signal_subscribe(BUS_NAME, statusicon.LAUNCHER_INTERFACE, "Update",
                         statusicon.LAUNCHER_PATH, None, Gio.DBusSignalFlags.NONE,
                         lambda *a: dock.append(a[-1].unpack()))
    plain_pixmaps = item_prop("IconPixmap")
    state["sessions"] = [
        sessions[0],
        traymodel.TraySession("s-beta", "podcast-hacker", "fix CI", unread=True,
                              last_active=100.0),
    ]
    icon.refresh()
    check("unread announces NeedsAttention", spin(lambda: traymodel.STATUS_ATTENTION in seen),
          str(seen))
    check("Status reads back NeedsAttention",
          item_prop("Status") == traymodel.STATUS_ATTENTION, item_prop("Status"))

    # -- the badge ---------------------------------------------------------

    check("the badge announces NewIcon", spin(lambda: bool(icons)))
    badged_pixmaps = item_prop("IconPixmap")
    check("the badge is drawn into the artwork", badged_pixmaps != plain_pixmaps)
    check("attention artwork carries the same badge",
          item_prop("AttentionIconPixmap") == badged_pixmaps)
    check("IconName stays empty under a badge", item_prop("IconName") == "",
          item_prop("IconName"))
    check("AttentionIconName stays empty under a badge",
          item_prop("AttentionIconName") == "", item_prop("AttentionIconName"))
    check("the dock hears the count",
          spin(lambda: any(p.get("count") == 1 and p.get("count-visible") for _u, p in dock)),
          str(dock))
    check("the dock broadcast names the desktop id", all(
        uri == "application://com.episode6.Collins.desktop" for uri, _p in dock), str(dock))

    # A tab that hasn't resolved its session id still holds the item Active.
    state["sessions"] = []
    state["placeholders"], state["unread"] = 1, 0
    icon.refresh()
    check("a placeholder alone keeps the item Active", item_prop("Status") == "Active",
          item_prop("Status"))
    _rev, layout = menu_call("GetLayout", GLib.Variant("(iias)", (0, -1, [])),
                             "(u(ia{sv}av))")
    check("a placeholder gets no row", len(layout[2]) == len(view().menu),
          f"{[c[1].get('label') for c in layout[2]]}")
    check("layout revision climbed", _rev > revision, f"{revision} -> {_rev}")

    state["placeholders"] = 0
    icon.refresh()
    check("nothing open goes Passive", item_prop("Status") == "Passive", item_prop("Status"))
    # The badge clears back into the pixmap, never into the name: a host that
    # painted one pixmap keeps painting pixmaps, and the plain artwork is what
    # takes the badged one's place.
    check("the cleared badge leaves the name empty", item_prop("IconName") == "",
          item_prop("IconName"))
    check("the cleared badge restores the plain artwork",
          item_prop("IconPixmap") == plain_pixmaps)
    check("the dock badge hides at zero",
          spin(lambda: dock and dock[-1][1].get("count-visible") is False), str(dock[-2:]))

    # The dock badge is keyed to the desktop id, not our bus name, so nothing
    # clears it for us when the item goes: stop() has to.
    state["sessions"] = [traymodel.TraySession("s-beta", "podcast-hacker", "fix CI",
                                               unread=True, last_active=100.0)]
    icon.refresh()
    spin(lambda: dock and dock[-1][1].get("count") == 1)
    dock.clear()
    icon.stop()
    check("stopping clears the dock badge",
          spin(lambda: any(p.get("count-visible") is False for _u, p in dock)), str(dock))
    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
