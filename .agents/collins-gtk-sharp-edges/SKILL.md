---
name: collins-gtk-sharp-edges
description: >-
  Verified GTK4 / libadwaita / VTE / GtkSourceView / Pango / GSK / CSS
  behaviours that have bitten Collins and are not specific to one feature:
  measurement and allocation, focus and hit-testing, dialogs and rows,
  popovers and menus, icons and glyphs, CSS animation and rendering, GdkPixbuf
  and images, VTE reads, headless capture and probe traps. Use when writing or
  debugging any widget code in Collins, when a layout, focus, tooltip, icon or
  animation "looks fine in code" but renders wrong, or before reaching for a
  GTK API that has a documented C idiom (it may not hold in PyGObject).
---

# GTK, libadwaita and VTE sharp edges

Each item is something measured on this codebase (GTK 4.22, libadwaita 1.9,
VTE 0.84, PyGObject 3.56 on the dev box; floors are GTK 4.10 / adw 1.5).
Feature-specific traps live in the feature skills; these are the general ones.

## Measurement and allocation

- **`do_size_allocate` on a `Gtk.Box` subclass is never called** — Box
  allocates through its layout manager. React to window resizes via the
  surface's `notify::width/height` (fires for maximize too; `notify::
  default-width` tracks only the floating size). `Gtk.ScrolledWindow` has no
  layout manager, so *its* `do_measure` is a real hook — used to cap a list at
  N rows by summing the first N children (set `propagate_natural_height`,
  return `-1` for baselines).
- **`Gtk.Picture` has no height-for-width**; in a narrow column it asks for
  the full natural height. Subclass with `do_get_request_mode →
  HEIGHT_FOR_WIDTH` + `do_measure`; a ScrolledWindow column allocates children
  their **minimum**, so the minimum must be the real height; `measure(
  VERTICAL, -1)` must report a bound that holds at every width (use a floor
  width) or GTK warns per measure. The slot must be a `Gtk.Box`, not an
  `Adw.Bin` (constant-size layout cuts height-for-width off).
- A `Gtk.Stack` measures as its largest page whichever shows — swap a word for
  a spinner without the button changing width.
- `Adw.BreakpointBin` needs `set_size_request` on **both** axes or it warns
  per allocation; a changed breakpoint condition isn't re-judged without
  `queue_resize()`.
- `Gtk.Label.set_lines(n)` caps lines **per paragraph** (Pango negative
  height); fold multi-paragraph text by truncating the text.
- Expand flags propagate to ancestors that never set their own: a `hexpand`
  icon inside a tile splits the tile's slack.
- A widget reparented this frame reports `get_width() == 0` until the next
  layout pass.
- Screen-size thresholds use `monitor.get_geometry().width` (logical px),
  never × `scale_factor`; `get_monitor_at_surface` is None before realize.
- Allocation happens in the frame clock's `layout` phase; an idle after a
  patch reads **stale** bounds. To re-pin scroll after a rebuild use
  `clock.connect_after("layout", …)` + `request_phase(LAYOUT)`. Conversely a
  `set_value` from inside `notify::upper` (emitted during the viewport's
  allocation) is laid out against already; mark intent there and set from an
  idle. Setting an adjustment to its current value emits nothing.
- `scroll_to_iter` on a fresh text buffer stays at the top (heights are
  estimates until validation idles); re-issue `scroll_to_mark` from a
  `PRIORITY_LOW` idle.

## Focus, input, hit-testing

- `grab_focus()` on a `Gtk.Box` subclass stops at a `Gtk.ScrolledWindow`
  child; composites need `do_grab_focus` naming the focusable leaf. Check
  `root.get_focus()`, not the return value; `has_focus()` is False headless.
- A synchronous `grab_focus()` inside a popover/menu action is undone when
  the popover closes and restores focus; defer with `GLib.idle_add`.
- `Adw.AlertDialog` focuses its default response — an entry in the extra
  child starts unfocused (`dialog.set_focus(entry)` as it presents); a
  focusable widget at the bottom of a tall extra child makes the dialog
  scroll to it during presentation (create it `can_focus=False`, restore
  later). `Adw.Dialog` hands focus to its first focusable widget on open.
- Presenting a newer dialog on the same host sets `can-focus` / `can-target`
  False on the older dialog's bin; `grab_focus()` inside it returns False
  with every ancestor healthy. `win.get_visible_dialog()` names what is on
  top.
- A selectable `Gtk.Label` selects all on focus (`gtk-label-select-on-focus`,
  default TRUE; off app-wide in `app.apply_gtk_settings`); unparenting the
  focused widget relocates focus **after the next paint** to the first
  focusable child of the emptied container.
- `Gtk.Shortcut` + `NamedAction` consumes the key only when the action
  activates; a disabled action lets the chord fall through to the focused
  VTE.
- `set_sensitive(False)` takes a widget out of pick, so its tooltip is
  unreachable — wrap it in a sensitive `Gtk.Box` and put the tooltip there.
- GTK4's tooltip machinery ignores popover grabs (tooltips pop behind an open
  menu); `tooltipmute.py` clears `has-tooltip` on the grab's window group via
  emission hooks (hooks cannot veto; `gtk-enable-tooltips` no longer exists).
- Hit-testing stops at a widget's allocation; CSS padding grows the drawn
  background with it. Widen a target at the row with a capture-phase gesture
  (`row-column-click-redirect`). `Gtk.Widget.pick()` in a ListView row's
  indent returns the ListView, not the row's expander — resolve rows from
  coordinates. `GtkButton` claims on release; a non-activatable
  `AdwTabPage` indicator has `can-target=False`.
- `Gtk.DragSource` refuses to start for 100 ms after the press; a competing
  gesture without that deadband wins a flick. Take the competitor out with
  `set_propagation_phase(NONE)` rather than racing by depth.
- `Gtk.DropTarget.new(GObject.TYPE_INVALID, …)` raises in PyGObject; use
  `Gtk.DropTarget(actions=…)` + `set_gtypes([...])` (order = preference). A
  `Gdk.Drop`'s formats are auto-unioned with deserializable GTypes, so
  `image/png` matches `Gdk.Texture`; connecting `accept` replaces the default
  check. `Gdk.Clipboard.set_texture/set_text` are C-only — build a
  `ContentProvider.new_for_value(GObject.Value(...))`.
- Adwaita tab DnD is process-global with no accept API; undo wrong-group
  drops on `page-attached` (`tabguard`). `AdwTab` widgets sit in the tree in
  creation order — match by the `page` property. `Adw.ViewSwitcher`'s direct
  children are one button per page in page order.
- `Gio.Menu` items have no enabled flag; per-item sensitivity is the
  action's. A `Gtk.PopoverMenu` filled on `show` must live in a
  `Gtk.MenuButton` (hand-parented popovers measure once, empty).
- `dialog.emit("response", id)` runs the handler but doesn't close an
  `Adw.AlertDialog`; `force_close()` after. An open Adw dialog swallows a
  window close.
- `win.activate_action("name", …)` on a `MainWindow` resolves to
  `Gtk.Widget.activate_action` and silently no-ops without the `win.` prefix.

## Dialogs and rows

- `Adw.AlertDialog` derives width from heading/body, not the extra child —
  ActionRows in it ellipsize their titles; `set_size_request(440, -1)` on the
  extra child.
- `Adw.ComboRow` values get ~130 px; a two-line subtitle leaves ~40 px.
- `Adw.PreferencesGroup.add()` of a non-row child lands in a box below the
  list.
- `Adw.AboutDialog`'s front page shows only icon/name/developer/version chip;
  `comments` is on the Details subpage and parses as Pango markup (escape).
- `Adw.PreferencesDialog.set_search_enabled` matches title+subtitle only;
  `Adw.Dialog` is never user-resizable — set explicit content sizes for
  anything that filters.
- Keycaps: Adwaita's rule is `shortcut > .keycap`; a bare label with the class
  is plain text.
- `Gtk.accelerator_name` spells `<Shift><Control>n`; compare canonical forms.

## CSS and rendering

- The stylesheet in `app.py` is a **bytes literal — ASCII only**.
- An animated property outranks every equal-specificity later rule; exclude
  outranking states from the animation's selector.
- `transform: translateX()` works on arbitrary widgets and animates; negative
  margins never move a widget. Pair with `min-height` / border transitions
  for a collapse. A `Gtk.Revealer` (`SLIDE_DOWN`, `reveal_child=False` at
  construction, flipped from an idle) is what makes a row *arrive*; drop the
  row's `min-height` while `.arriving`, and remember `> revealer > box`
  selectors are permanent.
- GSK fills an **inset** `box-shadow` spread from the center out: use a
  9999 px spread for a full tint, and on a capsule animate `background-color`
  instead, ending on the color the widget will actually show.
- GTK CSS has no `overflow` property.
- Libadwaita ≥ 1.6 paints `.card` from `--card-*-color`; redefining those
  under a class is a complete per-widget light/dark swap (dark card bg is
  translucent — flatten it).
- A widget-level `Gtk.CssProvider` styles only that widget; page-wide font
  scale needs a display-level provider keyed on an ancestor class, and
  percentages compound through inheritance.
- Symbolic icons go through GTK's own minimal SVG parser (≥ 4.14): **strokes
  fill or vanish and transforms are ignored** — bake geometry into filled
  paths. `Gtk.IconTheme.add_search_path` appends; prepend with
  `set_search_path([...])` or installed copies shadow the checkout.
  `lookup_icon(...).get_file().get_path()` says which copy won.
- Centre glyphs on **ink** extents, not the advance, and don't round the
  origin to whole pixels. Icon-beside-label padding reads uneven against a
  hard guide line vs a glyph's side bearing — measure ink in a capture.
- `GtkSource.Buffer` defaults to the light `classic` scheme, not to none.
- VTE's default colors are 75% grey on black under both schemes;
  `get_color_background_for_draw` is the only color getter.

## Images

- `Gdk.Texture.new_from_filename` decodes one frame; GIFs need
  `GdkPixbuf.PixbufAnimation` (deprecated, the only decoder) wrapped as a
  `Gdk.Paintable` whose frame clock stops when nothing draws it
  (`animatedimage.py`); clamp sub-20 ms delays.
- Thumbnails: `Pixbuf.get_file_info` (header only) +
  `new_from_file_at_scale` (scales while decoding, will upscale — only
  narrow). One decode per idle turn.
- Rendering a symbolic headlessly needs `snapshot_symbolic` +
  `Gsk.CairoRenderer` and an explicit `unrealize()`.

## VTE

- Range reads: end column exclusive, columns are cells, trailing typed
  spaces kept, reads stop at the last written cell, `feed()` parsed only
  after the main loop runs.
- Dim (SGR 2) is exported by `get_text_range_format(HTML)` only for the run
  the range starts on, as fg × 2/3.
- Never derive rows from the vadjustment under a repaint-style renderer; read
  the visible screen. Regex matches span soft-wraps.
- Finalizing the widget SIGHUPs the child; an unparented held widget keeps
  parsing output with no window.
- `set_size` before spawn reaches the child's winsize with no allocation;
  an unrealized VTE emits `bell` but rings nothing.
- VTE parses only ST-terminated OSC 9;4; after the CLI's clear the termprop
  is unset (ok=False), not INACTIVE; `termprop-changed` coalesces per batch.

## Headless capture and probes

- The in-process render (`WidgetPaintable` → `render_texture`) never includes
  popovers (own `Gtk.Native`): render the window first (a grab makes the
  window snapshot None), snapshot the *popover* not its child, from a ~700 ms
  timeout, filtering on `get_mapped()`, with `set_autohide(False)` so a
  grabbing popup maps at all; composite the PNGs by the anchor's
  `compute_point`.
- In-process renders paint the whole tree and can't show on-screen-only
  artifacts (damage-region clipping); for a real frame own
  `org.gnome.SettingsDaemon.MediaKeys` on the headless shell's private bus and
  call `org.gnome.Shell.Screenshot`.
- Under CI's Xvfb, default-idle callbacks never run; under the headless GNOME
  Shell, clipboard claims need focus first. `Gio.UnixSocketAddress` silently
  truncates paths over 107 bytes.
- `Adw` dialogs render in snapshots; the About dialog's subpages are reached
  with `push_by_tag("details")`.

Related: `collins-testing`, `capture-screenshots`, and the per-feature skills.
