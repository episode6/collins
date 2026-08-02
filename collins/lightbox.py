# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""A lightbox for image files: the bare picture floated over the whole window,
dimming everything behind it, with two big captioned buttons on whichever
side of the image has more screen space (editorfiles.lightbox_layout) and a
-/+ zoom bar floating over the bottom edge of the image.

Zooming is a display scale relative to the image's natural size, starting at
the fitted scale: the -/+ buttons step it, double-clicking the image zooms
in by 100% (and back to fitted on the next). The image grows with the zoom
until it hits the edges of
the screen, at which point it stops growing and pans instead — the picture
lives in a ScrolledWindow whose slot is pinned to min(display size, screen
max) per axis, so the two are equal (no scrolling) until the display size
outgrows the window. The picture's size request IS the display size; its
huge natural size never wins (a viewport allocates by minimum).

Not a dialog: an earlier Adw.Dialog version couldn't deliver "click outside
closes" — Adwaita leaves a floating dialog's dimmed area untargetable, so
shade clicks produce no event propagation at all and no controller anywhere
sees them. This widget IS the shade, added to the main window's
`lightbox_overlay` (a Gtk.Overlay wrapping the window content), so every
pixel is targetable by us: any press that isn't the image, the zoom bar or
a button closes, as does Esc (a capture-phase key controller on the window,
so focus doesn't matter).

Presented when a clicked file reference turns out to be an image (see
terminal._setup_links). Deliberately read-only — it can therefore show any
readable path, including the /tmp screenshots agent output loves to
reference. "Open in Editor" only appears when the file is inside the
clicking tab's editor project (the caller's call via `can_open_in_editor`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import editorfiles  # noqa: E402
from .editorfiles import (  # noqa: E402
    LIGHTBOX_BUTTON_STRIP,
    LIGHTBOX_MIN_H,
    LIGHTBOX_MIN_W,
    LIGHTBOX_SHADOW_PAD,
    lightbox_layout,
)
from .i18n import _  # noqa: E402

_ICON_SIZE = 32  # the captioned action buttons' icon, px
_ZOOM_STEP = 1.25  # one -/+ button press multiplies/divides the zoom by this
_ZOOM_MAX = 8.0  # ceiling on the display scale relative to natural size
_FALLBACK_WINDOW = (1200, 800)  # margins math when the window isn't realized


class ImageLightbox(Gtk.Box):
    """The shade itself; `present_over` floats it in the window's overlay."""

    def __init__(
        self,
        path: str | Path,
        can_open_in_editor: bool = False,
        on_open_in_editor: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._path = Path(path)
        self._on_open_in_editor = on_open_in_editor
        self._overlay: Gtk.Overlay | None = None
        self._esc: Gtk.EventControllerKey | None = None
        self._esc_root: Gtk.Widget | None = None
        self._zoom: float | None = None
        self._fit_zoom = 1.0
        self.add_css_class("lightbox-shade")
        self.set_hexpand(True)
        self.set_vexpand(True)

        self._texture = self._load_texture()
        if self._texture is not None:
            self._picture = Gtk.Picture.new_for_paintable(self._texture)
            self._picture.set_can_shrink(True)
            self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            self._image_size = (self._texture.get_width(), self._texture.get_height())
            zoom = Gtk.GestureClick()
            zoom.connect("pressed", self._on_picture_pressed)
            self._picture.add_controller(zoom)
            # The image slot: always a scrolled view. Until the zoomed
            # display size outgrows the screen the slot matches it exactly,
            # so the scrollbars only appear once panning means anything.
            self._scroller: Gtk.ScrolledWindow | None = Gtk.ScrolledWindow(
                child=self._picture
            )
            self._scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            self._scroller.add_css_class("lightbox-slot")
            self._slot: Gtk.Widget = Gtk.Overlay(child=self._scroller)
        else:
            self._picture = None
            self._scroller = None
            self._image_size = (LIGHTBOX_MIN_W, LIGHTBOX_MIN_H)
            self._slot = Adw.StatusPage(
                icon_name="image-missing-symbolic",
                title=_("Couldn't display image"),
                description=self._path.name,
            )
        self._slot.set_hexpand(True)
        self._slot.set_vexpand(True)

        # The -/+ zoom bar floats over the image, anchored to its bottom —
        # which, the slot being pinned to min(display, screen), is the bottom
        # of the image or of the screen, whichever is smaller.
        self._zoom_out_btn = self._zoom_button("zoom-out-symbolic", _("Zoom out"), 1 / _ZOOM_STEP)
        self._zoom_in_btn = self._zoom_button("zoom-in-symbolic", _("Zoom in"), _ZOOM_STEP)
        self._zoombar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
            margin_bottom=12,
        )
        self._zoombar.add_css_class("lightbox-zoombar")
        self._zoombar.append(self._zoom_out_btn)
        self._zoombar.append(self._zoom_in_btn)
        if isinstance(self._slot, Gtk.Overlay):
            self._slot.add_overlay(self._zoombar)

        self._buttons: list[Gtk.Button] = []
        if can_open_in_editor and on_open_in_editor is not None:
            self._buttons.append(
                self._caption_button(
                    "document-edit-symbolic", _("Open in Editor"), self._on_editor_clicked
                )
            )
        self._buttons.append(
            self._caption_button(
                "document-open-symbolic", _("Open With…"), self._on_open_with_clicked
            )
        )

    def _caption_button(self, icon_name: str, caption: str, callback) -> Gtk.Button:
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(_ICON_SIZE)
        label = Gtk.Label(label=caption)
        label.add_css_class("caption")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.append(icon)
        inner.append(label)
        btn = Gtk.Button(child=inner)
        btn.add_css_class("flat")
        btn.add_css_class("lightbox-action")
        btn.connect("clicked", callback)
        return btn

    def _zoom_button(self, icon_name: str, tooltip: str, factor: float) -> Gtk.Button:
        btn = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip)
        btn.add_css_class("flat")
        btn.add_css_class("lightbox-zoom")
        btn.connect(
            "clicked", lambda *_a: self._apply_zoom((self._zoom or self._fit_zoom) * factor)
        )
        return btn

    def _load_texture(self) -> Gdk.Texture | None:
        if editorfiles.image_guard(self._path) != editorfiles.LoadGuard.OK:
            return None
        try:
            return Gdk.Texture.new_from_filename(str(self._path))
        except GLib.Error:
            return None  # not decodable after all; the status page says so

    # -- presenting ----------------------------------------------------------

    def present_over(self, parent: Gtk.Widget) -> None:
        """Float over *parent*'s window: buttons on whichever side of the
        image has more screen space, the image capped to a fraction of the
        window the way a web lightbox is (zoom can then grow it to the
        window edges)."""
        root = parent.get_root()
        overlay = getattr(root, "lightbox_overlay", None)
        if overlay is None:
            # A window without the overlay (shouldn't happen for terminals):
            # degrade to the default app rather than dead-ending the click.
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(self._path)))
            launcher.launch(root, None, lambda lch, res: _launch_done(lch, res))
            return
        self._overlay = overlay

        win_w, win_h = root.get_width(), root.get_height()
        side, width, height = lightbox_layout(*self._image_size, win_w, win_h)
        if win_w <= 0 or win_h <= 0:
            win_w, win_h = _FALLBACK_WINDOW
        self._win = (win_w, win_h)
        strip_r = LIGHTBOX_BUTTON_STRIP if side == "right" else 0
        strip_b = LIGHTBOX_BUTTON_STRIP if side == "below" else 0
        # Panel chrome around the image slot, and the zoom ceiling: the slot
        # may grow to the window edges minus the shadow inset and chrome.
        self._chrome = (strip_r, strip_b)
        self._max_slot = (
            max(win_w - 2 * LIGHTBOX_SHADOW_PAD - self._chrome[0], 1),
            max(win_h - 2 * LIGHTBOX_SHADOW_PAD - self._chrome[1], 1),
        )
        fit_w = width - 2 * LIGHTBOX_SHADOW_PAD - self._chrome[0]
        fit_h = height - 2 * LIGHTBOX_SHADOW_PAD - self._chrome[1]
        self._fit_zoom = min(
            fit_w / max(self._image_size[0], 1), fit_h / max(self._image_size[1], 1), 1.0
        )

        strip = Gtk.Box(
            orientation=(
                Gtk.Orientation.VERTICAL if side == "right" else Gtk.Orientation.HORIZONTAL
            ),
            spacing=12,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        for btn in self._buttons:
            strip.append(btn)
        self._panel = Gtk.Box(
            orientation=(
                Gtk.Orientation.HORIZONTAL if side == "right" else Gtk.Orientation.VERTICAL
            ),
            spacing=6,
        )
        self._panel.append(self._slot)
        self._panel.append(strip)
        self.append(self._panel)

        if self._picture is not None:
            self._apply_zoom(self._fit_zoom)
        else:
            self._pin_panel(self._image_size)

        click = Gtk.GestureClick()
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_shade_pressed)
        self.add_controller(click)

        # Esc closes no matter what holds keyboard focus: capture phase on
        # the window itself, removed again on close.
        esc = Gtk.EventControllerKey()
        esc.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        esc.connect("key-pressed", self._on_root_key)
        root.add_controller(esc)
        self._esc, self._esc_root = esc, root

        overlay.add_overlay(self)

    def close(self) -> None:
        if self._esc_root is not None and self._esc is not None:
            self._esc_root.remove_controller(self._esc)
            self._esc = self._esc_root = None
        if self._overlay is not None:
            self._overlay.remove_overlay(self)
            self._overlay = None

    # -- zoom ----------------------------------------------------------------

    def _apply_zoom(self, zoom: float) -> None:
        """Set the display scale: the picture's size request becomes the
        display size, the slot is pinned to min(display, screen max) per
        axis — equal until the image hits the window edges, scrolling
        after."""
        zoom = min(max(zoom, self._fit_zoom), _ZOOM_MAX)
        if zoom == self._zoom or self._picture is None:
            return
        self._zoom = zoom
        display = (round(self._image_size[0] * zoom), round(self._image_size[1] * zoom))
        slot = (min(display[0], self._max_slot[0]), min(display[1], self._max_slot[1]))
        self._picture.set_size_request(*display)
        self._pin_panel(slot)
        self._zoom_out_btn.set_sensitive(zoom > self._fit_zoom)
        self._zoom_in_btn.set_sensitive(zoom < _ZOOM_MAX)
        if display != slot:
            GLib.idle_add(self._center_scroll)

    def _pin_panel(self, slot: tuple[int, int]) -> None:
        """Center the panel by margins for an image slot of *slot* px — the
        shade fills the window, so margins are what fix the panel's size
        (and the picture's huge natural size never wins)."""
        win_w, win_h = self._win
        content_w = slot[0] + self._chrome[0]
        content_h = slot[1] + self._chrome[1]
        left = max((win_w - content_w) // 2, LIGHTBOX_SHADOW_PAD)
        top = max((win_h - content_h) // 2, LIGHTBOX_SHADOW_PAD)
        self._panel.set_margin_start(left)
        self._panel.set_margin_end(max(win_w - content_w - left, LIGHTBOX_SHADOW_PAD))
        self._panel.set_margin_top(top)
        self._panel.set_margin_bottom(max(win_h - content_h - top, LIGHTBOX_SHADOW_PAD))

    def _center_scroll(self) -> bool:
        if self._scroller is not None:
            for adj in (self._scroller.get_hadjustment(), self._scroller.get_vadjustment()):
                adj.set_value(max((adj.get_upper() - adj.get_page_size()) / 2, 0))
        return GLib.SOURCE_REMOVE

    def _on_picture_pressed(self, _gesture, n_press: int, _x, _y) -> None:
        if n_press != 2 or self._zoom is None:
            return
        # Zoom in BY 100% (double the fitted size); double-clicking again —
        # or from any button-stepped zoom — goes back to the fitted size.
        if abs(self._zoom - self._fit_zoom) < 0.001:
            self._apply_zoom(self._zoom * 2.0)
        else:
            self._apply_zoom(self._fit_zoom)

    # -- input ---------------------------------------------------------------

    def _press_closes(self, x: float, y: float) -> bool:
        """Whether a press at shade-local (x, y) should close the lightbox:
        anything that isn't the image slot, the zoom bar or a button."""
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None and target is not self:
            if (
                target is self._slot
                or target is self._zoombar
                or isinstance(target, Gtk.Button)
            ):
                return False
            target = target.get_parent()
        return True

    def _on_shade_pressed(self, gesture: Gtk.GestureClick, _n, x: float, y: float) -> None:
        if not self._press_closes(x, y):
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.close()

    def _on_root_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        if keyval != Gdk.KEY_Escape:
            return False
        self.close()
        return True

    # -- actions -------------------------------------------------------------

    def _on_editor_clicked(self, _btn: Gtk.Button) -> None:
        callback = self._on_open_in_editor
        self.close()
        if callback is not None:
            callback()

    def _on_open_with_clicked(self, _btn: Gtk.Button) -> None:
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(self._path)))
        launcher.set_always_ask(True)  # "another app": always show the chooser
        launcher.launch(self.get_root(), None, lambda lch, res: _launch_done(lch, res))


def _launch_done(launcher: Gtk.FileLauncher, result) -> None:
    try:
        launcher.launch_finish(result)
    except GLib.Error:
        pass  # no handler, or the user dismissed the chooser


def present_image_lightbox(
    parent: Gtk.Widget,
    path: str | Path,
    can_open_in_editor: bool = False,
    on_open_in_editor: Callable[[], None] | None = None,
) -> None:
    ImageLightbox(path, can_open_in_editor, on_open_in_editor).present_over(parent)
