---
name: collins-notifications-and-tray
description: >-
  How Collins tells the user something: the NotificationCenter and its delivery
  table (notifycenter.py), in-app cards (notifyoverlay.py), the header bell and
  history sheet (notifypanel.py), the notification sound (notifysound.py),
  desktop notifications, the bell flash (flash.py), the StatusNotifierItem
  status icon and dock badge (statusicon.py, traymodel.py), the unread badge's
  meaning, close-to-hide, the daily update check (updatecheck.py) and Caffeine
  Mode (caffeine.py). Use when adding a notification kind or delivery,
  changing what the badge counts, debugging a stuck dock badge, touching the
  tray menu, or changing how a finished run or a bell is announced.
---

# Notifications, the status icon, updates and Caffeine

## The center (`notifycenter.py`, GTK-free)

`NotificationCenter` owns every notification Collins has raised, the number
every badge shows, and the **delivery table**; the widgets hang off one
`changed()` callback (`connect` / `disconnect`). Kinds: `message`
(`notify_user`), `bell` (a terminal BEL), `finished` (a run ended and nobody
looked — a *synthetic* row `set_green` owns, tracking the sidebar's unread
flag one-for-one), `update` (a newer Collins; no session). Focus states from
`focus_state(any_window_active, tab_window_active, tab_selected)`:
`selected`, `elsewhere` (in Collins, another tab), `unfocused` (no Collins
window active — hidden windows are never active).

`delivery(kind, focus, announce_finished_runs)` returns a frozenset of
`card`, `sound` (only ever beside a card — the desktop sounds its own),
`row`, `row-read`, `flag` (sidebar unread), `flash`, `desktop`, `beep`.
Rules it settles: a message to the selected tab is a read row and nothing
more (nothing silently does nothing — `tool_reply` tells the agent where it
went); a bell from an unfocused Collins is a desktop notification, from the
selected tab the compositor beep and no row; `finished` is a row only unless
*Announce finished runs*, then it goes out exactly as a message would (riding
the synthetic row's own edge: `App._sync_green` → `MainWindow.announce_
finished`; `_on_session_finished` announces nothing itself); an update is
card+sound or desktop, plus a row. Unknown kinds/focuses **raise**.
`without_cards` (the `inapp_notifications` switch off) turns card+sound into
desktop. `MainWindow._deliver` executes a set; `notify_session` is the entry
for `notify_user`.

**The badge counts unread notifications**: every unlooked-at finished run
(the synthetic green row: appears when a session's unread flag comes on,
*leaves* — is not marked read — when it goes off) plus every message and
bell nobody has gone to. Never unread + working: a busy row's green row
counts 0 until the turn ends (`SessionStore.busy-changed`). Placeholders
(tabs without a session id yet) are keyed by placeholder id and rekeyed on
resolve (`rekey_session`); their green rows are driven from `MainWindow._set_
placeholder_unread` since store rows only. Message, bell and update rows
persist in `state.json` (`clean_records`); finished rows don't. Reading:
going to the session marks its rows read (`mark_session_read`); opening or
scrolling the sheet reads nothing.

Sounds: `notification_sound` is `default` (the desktop theme's
`message-new-instant`, walked at play time), `none`, `theme:<event>`,
`bundled:<name>` (five CC0 files under `data/sounds`, package data via the
`collins/sounds` symlink — adding one means the file, a `SOUND_BUNDLED` row,
THIRD_PARTY_LICENSES.md, `debian/copyright`, the RPM License and the PKGBUILD
license array; `verify_wheel_data.py` globs the dir), or an absolute path.
`sound_file` resolves it; `notifysound.play()` is a reused GStreamer `playbin`
behind a soft import (falls back to `Gdk.Display.beep`), debounced and
single-flight, honouring `org.gnome.desktop.sound`'s event-sounds switch.
`Gtk.MediaFile` was rejected (needs a media backend a fresh desktop lacks).

## The widgets

- `notifyoverlay.NotificationCards`: a box in the window's full-window overlay
  (`MainWindow.lightbox_overlay`), each card a `Gtk.Revealer` revealed on the
  next main-loop turn so it slides; a 32 px tile with the project's icon,
  title, kind mark, age, two body lines, project footer, a × that dismisses
  the card **only** (the row stays unread — the badge means "waiting for
  you"). Escape never touches a card. `notification_color_scheme` pins the
  card light/dark by re-pinning `--card-bg-color` / `--card-fg-color` /
  `--card-shade-color` under a class (`card_scheme_class`); the dark bg is
  flattened to `#333337` because Adwaita's is translucent.
- `notifypanel.NotificationBell` (header toggle wearing the count as a pill,
  `traymodel.badge_text` caps at `9+`) and `NotificationSheet` (an
  `Adw.OverlaySplitView` wrapping the content stack, collapsed, sheet as the
  end sidebar — scrim, slide and Escape for free; `active` ↔ `show-sidebar`
  bound both ways). Ctrl+Shift+B.
- Desktop notifications go out under the session id (replacing) and are
  withdrawn on archive and when the unread flag comes off; the one-time
  "Collins is still running" notice (`collins-hidden`) is withdrawn by
  `App._dismiss_hide_notice` on every way back.
- `flash.py` pulses `.bell-flash` (the app's only "look here" animation) on
  the header bar, tab and row; its inset box-shadow uses a 9999 px spread
  because GSK fills inset spreads center-out, and capsules animate
  `background-color` instead.

## The status icon (`traymodel.py` + `statusicon.py`)

GTK4 has no `GtkStatusIcon` and libayatana is GTK3-only, so `StatusIcon` puts
a StatusNotifierItem on D-Bus by hand (`org.kde.StatusNotifierItem` at
`/StatusNotifierItem`, `com.canonical.dbusmenu` at `/MenuBar`) via
`Gio.DBusConnection`. `traymodel` (GTK-free) decides everything: `status_for`
(`Passive` / `Active` / `NeedsAttention`), `badge_text`, `artwork_for` (the
glass: barber pole while `working > 0`, the drink while unread, empty
otherwise — `<app id>-panel*.svg`, three drawings, **no animation**: SNI has
no frame API and every `NewIcon` is a round trip), `tooltip_for`,
`menu_entries` (jump to any open session, show windows, quit), and
`tokened_action` (spend the host's activation token via
`Gtk.Window.set_startup_id` right before `present()` — `App._present` — or
mutter shows a busy cursor for 15 s). Facts about GNOME's
`ubuntu-appindicators` host: property changes travel only as `New*` signals
(`PropertiesChanged` is ignored); a `Passive` item defers refreshes; the
DBusMenu client **merges** property dicts by id and resets nothing, so every
row states its whole set (`type` and `label`) every time; click routing keys
on introspection finding `Activate` (left single = menu, double = Activate,
middle = SecondaryActivate); the badge is composited into `IconPixmap`
(digits centred on **ink**, half-pixel origin) because `IconName` resolves in
the host's process. Ubuntu Dock's `com.canonical.Unity.LauncherEntry` gets
the same count. Throwaway app ids never register an item; `App.tray_host_
present` caches `watch_availability` so the close path never blocks.

Close-to-hide: `quit_with_running_sessions = "hide"` (or the dialog's Keep
Running) hides the window, sessions keep running with no
`Gio.Application.hold()`, the icon/a notification/a relaunch presents it
again (`App._present_main_window`); every dialog presents through
`dialogs._present`, which unhides an invisible parent first.

**A stuck dock badge** has two sources: ours, and Ubuntu Dock's own counter
of undismissed notifications in GNOME's message list. Read the live item's
`ToolTip` over `gdbus` — if it says unread 0, the number is the dock's; clear
with `org.gtk.Notifications.RemoveNotification <app id> <key>`.

## Update check (`updatecheck.py`)

Once a day (`due()`: a day since an answer, an hour since a failure) GET
`/repos/episode6/collins/releases/latest` through `gh` when installed and
signed in (5000/h), else anonymously with `If-None-Match` (a 304 is free).
Cache `~/.cache/collins/update-check.json` (`checked_at`, `failed_at`,
`latest`, `url`, `etag`, `notified`) — never `state.json`. A newer version is
one `KIND_UPDATE` row (`update:<version>`, announced once ever); `retire`
drops rows the running version caught up with. `harnessed()` refuses under
`COLLINS_USAGE_FIXTURE` or a non-release/debug app id so e2e card counts stay
deterministic. Compare is PEP 440-lite (`0.1.2.dev0 < 0.1.2`).

## Caffeine (`caffeine.py`, `App` inhibitor)

The header's cup: `Gtk.Application.inhibit` with SUSPEND (+ IDLE when
`caffeine_keep_screen_on`), by timer (`caffeine.py` owns the durations and the
button wording so menu, setting and countdown can't drift), indefinitely, or
**until idle** — `App._follow_activity` keeps the inhibit while any tab is
working and releases `caffeine_idle_grace_minutes` after the last stops,
re-arming when work resumes.

## Footguns

- Every notification should go through the delivery table so it reaches the
  desktop when the app isn't focused — no bespoke banner paths.
- A never-selected tab's VTE emits `bell` but rings nothing; the app's own
  sound removes that accidental silence deliberately.
- `Adw.ComboRow` values are ~130 px: "Follow app" fits, a subtitle that wraps
  squeezes it to "Follo…".
- `GLib.idle_add` landings in the usage panel and notification flows must be
  `PRIORITY_DEFAULT` (CI Xvfb starvation).
- Tray e2e needs an in-process `StatusIcon` fed `_item_call` directly
  (`check_status_icon.py`, which grows the session list mid-check to catch the
  merge-by-id trap).

Related: `collins-session-mcp-tools` (`notify_user`),
`collins-sessions-and-sidebar` (unread/busy flags), `collins-gtk-sharp-edges`.
