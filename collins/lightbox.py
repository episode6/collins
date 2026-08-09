# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""A lightbox for image files: the bare picture floated over the whole window,
dimming everything behind it, with two big captioned buttons on whichever
side of the image has more screen space (editorfiles.lightbox_layout) and a
-/+ zoom bar floating over the bottom edge of the image. The floating bar
only shows while the pointer is over the image (fading out a couple of
seconds after it leaves), and on images too small to float over — where the
bar would cover more than half the picture — it sits below the image
instead, always visible (editorfiles.lightbox_zoombar_inside).

Zooming is a display scale relative to the image's natural size, starting at
the fitted scale: the -/+ buttons step it, double-clicking the image zooms
in by 100% (and back to fitted on the next). The image grows with the zoom
until it hits the edges of
the screen, at which point it stops growing and pans instead — the picture
lives in a ScrolledWindow whose slot is pinned to min(display size, screen
max) per axis, so the two are equal (no scrolling) until the display size
outgrows the window. The picture's size request IS the display size; its
huge natural size never wins (a viewport allocates by minimum). Once the
display outgrows the space left beside the button strip, the strip hides
so the image gets its space back (zooming out returns it). The zoom/slot
math is editorfiles.lightbox_zoom_slot, unit-tested there. Resizing the
window while the lightbox is open re-lays it out (via the surface's size,
the one signal that also fires for maximize).

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

An optional caption ends the image column — under the image, and under the
zoom bar too when that sits below a small image — wrapping to the image's
width, but never to less than a readable floor: under a narrow portrait
image the caption extends past the image's sides (centered) instead of
stacking a tall sliver of text, which would also starve the fit math (a
taller caption shrinks the fitted image, narrowing the caption further).
The fit and centering math both count the caption's height at that wrap
width, so a captioned image still lands centered with the caption on
screen — and the caption itself is capped at a quarter of the window's
height (_limit_caption_height), scrolling past that rather than squeezing
the image. Today only the show_image MCP tool passes one — the text is
agent-supplied, which is why it renders as plain wrapped text.
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
    lightbox_zoom_slot,
    lightbox_zoombar_inside,
)
from .i18n import _  # noqa: E402

_ICON_SIZE = 32  # the captioned action buttons' icon, px
_ZOOM_STEP = 1.25  # one -/+ button press multiplies/divides the zoom by this
_ZOOM_MAX = 8.0  # ceiling on the display scale relative to natural size
_FALLBACK_WINDOW = (1200, 800)  # margins math when the window isn't realized
_PANEL_SPACING = 6  # gap between the image, the button strip and a below-bar
_ZOOMBAR_MARGIN = 12  # the floating bar's inset from the image's bottom edge
_ZOOMBAR_FADE_DELAY_MS = 2000  # pointer-left-the-image grace before fading out
_CAPTION_MIN_WRAP = 360  # a caption never wraps narrower than this, px (see module doc)
_CAPTION_WINDOW_FRACTION = 4  # a caption never grows past 1/this of the window's height


class ImageLightbox(Gtk.Box):
    """The shade itself; `present_over` floats it in the window's overlay."""

    def __init__(
        self,
        path: str | Path,
        can_open_in_editor: bool = False,
        on_open_in_editor: Callable[[], None] | None = None,
        caption: str | None = None,
    ) -> None:
        super().__init__()
        self._path = Path(path)
        self._on_open_in_editor = on_open_in_editor
        self._overlay: Gtk.Overlay | None = None
        self._esc: Gtk.EventControllerKey | None = None
        self._esc_root: Gtk.Widget | None = None
        self._zoom: float | None = None
        self._fit_zoom = 1.0
        self._win = _FALLBACK_WINDOW
        self._chrome = (0, 0)
        self._zoombar_inside = True
        self._fade_source = 0
        self._resize_queued = False
        self._surface: Gdk.Surface | None = None
        self._surface_handlers: list[int] = []
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

        # The caption under the image: plain wrapped text (it is
        # agent-supplied), centered, wrapping at the image's width floored
        # by _CAPTION_MIN_WRAP (_caption_wrap_w). Its height at that width
        # is folded into the fit and centering math via _caption_extra_h so
        # a captioned image still centers with the caption fully on screen.
        self._caption: Gtk.Label | None = None
        self._caption_scroller: Gtk.ScrolledWindow | None = None
        if caption:
            self._caption = Gtk.Label(
                label=caption,
                wrap=True,
                justify=Gtk.Justification.CENTER,
                halign=Gtk.Align.CENTER,
            )
            # Pin the wrap width to _pin_panel's width request exactly: with
            # NONE the label's natural width equals its minimum, so the
            # request fixes both and every height-for-width measure agrees
            # with the allocation. A free-wrapping label instead reports its
            # minimum height at its minimum (longest-word) width, which
            # inflates the panel's minimum past the pinned margins — the
            # overflow then vexpands the slot into a letterbox around the
            # picture.
            self._caption.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
            self._caption.add_css_class("lightbox-caption")
            # An over-long caption scrolls instead of squeezing the image:
            # the scroller grows with the text (propagating its natural
            # height) up to the quarter-window cap _limit_caption_height
            # keeps current, and scrolls past it.
            self._caption_scroller = Gtk.ScrolledWindow(child=self._caption)
            self._caption_scroller.set_policy(
                Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
            )
            self._caption_scroller.set_propagate_natural_height(True)
            if self._scroller is not None:
                # The caption may be wider than a narrow image, which would
                # stretch a FILL slot — and its shadow plate — to the
                # caption's width. Centering the slot at its exact size
                # (_apply_zoom keeps a size request on it) leaves the shadow
                # hugging the picture.
                self._slot.set_halign(Gtk.Align.CENTER)

        # The -/+ zoom bar floats over the image, anchored to its bottom —
        # which, the slot being pinned to min(display, screen), is the bottom
        # of the image or of the screen, whichever is smaller. On images too
        # small to float over it sits below the image instead, and while
        # floating it only shows when the pointer is over the image
        # (_place_zoombar / the fade handlers).
        self._zoom_out_btn = self._zoom_button("zoom-out-symbolic", _("Zoom out"), 1 / _ZOOM_STEP)
        self._zoom_in_btn = self._zoom_button("zoom-in-symbolic", _("Zoom in"), _ZOOM_STEP)
        self._zoombar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
            margin_bottom=_ZOOMBAR_MARGIN,
        )
        self._zoombar.add_css_class("lightbox-zoombar")
        self._zoombar.append(self._zoom_out_btn)
        self._zoombar.append(self._zoom_in_btn)
        self._hover: Gtk.EventControllerMotion | None = None
        if isinstance(self._slot, Gtk.Overlay):
            self._slot.add_overlay(self._zoombar)
            self._hover = Gtk.EventControllerMotion()
            self._hover.connect("enter", self._on_slot_enter)
            self._hover.connect("leave", self._on_slot_leave)
            self._slot.add_controller(self._hover)

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
        self._limit_caption_height()
        strip_r = LIGHTBOX_BUTTON_STRIP if side == "right" else 0
        strip_b = LIGHTBOX_BUTTON_STRIP if side == "below" else 0
        # Panel chrome around the image slot: the strip's reservation on its
        # side. The zoom/slot math lives in editorfiles.lightbox_zoom_slot.
        self._chrome = (strip_r, strip_b)
        self._set_fit_zoom(width, height)

        self._strip = Gtk.Box(
            orientation=(
                Gtk.Orientation.VERTICAL if side == "right" else Gtk.Orientation.HORIZONTAL
            ),
            spacing=12,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        for btn in self._buttons:
            self._strip.append(btn)
        # The image column: the slot, plus the zoom bar when it sits below
        # the image instead of floating inside it (_place_zoombar), plus the
        # caption. The caption stays last — _place_zoombar inserts the bar
        # right after the slot so it hugs the image it zooms.
        self._image_col = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=_PANEL_SPACING
        )
        self._image_col.append(self._slot)
        if self._caption_scroller is not None:
            self._image_col.append(self._caption_scroller)
        self._panel = Gtk.Box(
            orientation=(
                Gtk.Orientation.HORIZONTAL if side == "right" else Gtk.Orientation.VERTICAL
            ),
            spacing=_PANEL_SPACING,
        )
        self._panel.append(self._image_col)
        self._panel.append(self._strip)
        self.append(self._panel)

        if self._picture is not None:
            self._apply_zoom(self._fit_zoom)
            if self._zoombar_inside:
                # Visible at open for discoverability, then the usual fade —
                # hovering the image (or already being over it) keeps it.
                self._arm_zoombar_fade()
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

        # Re-layout when the window resizes while the lightbox is open (the
        # surface, not a widget signal — see _on_surface_size).
        surface = root.get_surface()
        if surface is not None:
            self._surface = surface
            self._surface_handlers = [
                surface.connect("notify::width", self._on_surface_size),
                surface.connect("notify::height", self._on_surface_size),
            ]

        overlay.add_overlay(self)

    def close(self) -> None:
        if self._fade_source:
            GLib.source_remove(self._fade_source)
            self._fade_source = 0
        if self._esc_root is not None and self._esc is not None:
            self._esc_root.remove_controller(self._esc)
            self._esc = self._esc_root = None
        if self._surface is not None:
            for handler in self._surface_handlers:
                self._surface.disconnect(handler)
            self._surface, self._surface_handlers = None, []
        if self._overlay is not None:
            self._overlay.remove_overlay(self)
            self._overlay = None

    # -- window resize while open --------------------------------------------

    def _on_surface_size(self, _surface, _pspec) -> None:
        """The window's Gdk.Surface resized (fires for interactive resizes
        AND maximize, which the window's default-width property deliberately
        doesn't track — and which a size-allocate vfunc never sees either,
        Gtk.Box allocating through its layout manager). Re-layout from an
        idle, once the window's own size has settled."""
        if not self._resize_queued:
            self._resize_queued = True
            GLib.idle_add(self._relayout_for_resize)

    def _relayout_for_resize(self) -> bool:
        self._resize_queued = False
        root = self.get_root()
        if self._overlay is None or root is None:
            return GLib.SOURCE_REMOVE
        w, h = root.get_width(), root.get_height()
        if w <= 0 or h <= 0 or (w, h) == self._win:
            return GLib.SOURCE_REMOVE
        # Keep the strip on the side it's already on (the panel is built for
        # it); refit for the new window and re-apply the zoom — a fitted
        # image stays fitted, a zoomed one keeps its scale (clamped to the
        # new floor) and the slot re-caps to the new edges.
        at_fit = self._zoom is not None and abs(self._zoom - self._fit_zoom) < 0.001
        self._win = (w, h)
        self._limit_caption_height()
        side = "right" if self._chrome[0] else "below"
        _side, width, height = lightbox_layout(*self._image_size, w, h, side)
        self._set_fit_zoom(width, height)
        if self._picture is None:
            self._pin_panel(self._image_size)
        else:
            zoom = self._fit_zoom if at_fit else (self._zoom or self._fit_zoom)
            self._zoom = None  # force a re-apply even at a numerically equal zoom
            self._apply_zoom(zoom)
        return GLib.SOURCE_REMOVE

    # -- zoom ----------------------------------------------------------------

    def _set_fit_zoom(self, width: int, height: int) -> None:
        """The fitted display scale from lightbox_layout's content size (the
        image's fitted size once the shadow inset, chrome and any caption
        come out). The caption's height depends on its wrap width, which
        follows the fitted image's own width (_caption_wrap_w) — and that
        width in turn depends on the fit, so the two are re-derived until
        they agree. The zoom only shrinks round to round (a narrower wrap
        can only make the caption taller), and the wrap floor stops the
        shrinking, so a few rounds settle it."""
        fit_w = width - 2 * LIGHTBOX_SHADOW_PAD - self._chrome[0]
        fit_h = height - 2 * LIGHTBOX_SHADOW_PAD - self._chrome[1]
        zoom = 0.0
        disp_w = fit_w
        for _round in range(4):
            cap_h = self._caption_extra_h(self._caption_wrap_w(disp_w, fit_w))
            new_zoom = min(
                fit_w / max(self._image_size[0], 1),
                max(fit_h - cap_h, 1) / max(self._image_size[1], 1),
                1.0,
            )
            if abs(new_zoom - zoom) < 0.001:
                break
            zoom = new_zoom
            disp_w = round(self._image_size[0] * zoom)
        self._fit_zoom = zoom

    def _caption_wrap_w(self, image_w: int, avail_w: int) -> int:
        """The width the caption wraps at when the image shows *image_w*
        wide and the column may use up to *avail_w*: the image's width,
        floored by _CAPTION_MIN_WRAP (as far as the space allows) so a
        narrow image widens the caption past its sides instead of stacking
        a tall sliver of text."""
        return max(image_w, min(_CAPTION_MIN_WRAP, avail_w))

    def _limit_caption_height(self) -> None:
        """Cap the caption's scroller so it never grows taller than
        1/_CAPTION_WINDOW_FRACTION of the window — text past the cap
        scrolls. Re-derived whenever self._win does, and mirrored in
        _caption_extra_h, so the fit and centering math only ever see the
        capped height."""
        if self._caption_scroller is not None:
            self._caption_scroller.set_max_content_height(
                self._win[1] // _CAPTION_WINDOW_FRACTION
            )

    def _caption_extra_h(self, width: int) -> int:
        """The caption's footprint in the image column: its wrapped height
        at *width*, capped the way its scroller is (_limit_caption_height),
        plus the column gap above it; 0 with no caption."""
        if self._caption is None:
            return 0
        _min, nat, _mb, _nb = self._caption.measure(
            Gtk.Orientation.VERTICAL, max(width, 1)
        )
        return min(nat, self._win[1] // _CAPTION_WINDOW_FRACTION) + _PANEL_SPACING

    def _apply_zoom(self, zoom: float) -> None:
        """Set the display scale: the picture's size request becomes the
        display size, the slot is pinned to min(display, screen max) per
        axis — equal until the image hits the window edges, scrolling
        after. The button strip yields to the image: once the display
        outgrows the space left beside it on its axis, keeping it would
        only shrink the image, so it hides (and returns on zooming back
        out). The math is editorfiles.lightbox_zoom_slot, unit-tested
        there."""
        zoom = min(max(zoom, self._fit_zoom), _ZOOM_MAX)
        if zoom == self._zoom or self._picture is None:
            return
        self._zoom = zoom
        # The caption keeps its row: its height at this zoom's wrap width
        # comes off the window height the slot may grow into, so zooming
        # far in caps the image below the window edge instead of pushing
        # the caption off it.
        win_h = self._win[1]
        if self._caption is not None:
            avail_w = self._win[0] - 2 * LIGHTBOX_SHADOW_PAD - self._chrome[0]
            # The display width clamped to the space the slot can actually
            # be pinned to — zoomed past the window edge, the raw width
            # would overshoot the real wrap and under-reserve the caption.
            disp_w = min(round(self._image_size[0] * zoom), avail_w)
            win_h -= self._caption_extra_h(self._caption_wrap_w(disp_w, avail_w))
        display, strip_shown, chrome, slot = lightbox_zoom_slot(
            *self._image_size, zoom, self._chrome, self._win[0], win_h
        )
        self._strip.set_visible(strip_shown)
        self._picture.set_size_request(*display)
        if self._caption is not None:
            # The slot is centered, not FILLed, under a caption (see
            # __init__); this request is what gives it its exact size there.
            self._slot.set_size_request(*slot)
        self._place_zoombar(
            lightbox_zoombar_inside(self._zoombar_h() + _ZOOMBAR_MARGIN, slot[1])
        )
        self._pin_panel(slot, chrome)
        self._zoom_out_btn.set_sensitive(zoom > self._fit_zoom)
        self._zoom_in_btn.set_sensitive(zoom < _ZOOM_MAX)
        if display != slot:
            GLib.idle_add(self._center_scroll)

    def _pin_panel(self, slot: tuple[int, int], chrome: tuple[int, int] | None = None) -> None:
        """Center the panel by margins for an image slot of *slot* px — the
        shade fills the window, so margins are what fix the panel's size
        (and the picture's huge natural size never wins). *chrome* is the
        button strip's reservation, `self._chrome` unless the strip is
        hidden."""
        win_w, win_h = self._win
        if chrome is None:
            chrome = self._chrome
        content_w = slot[0] + chrome[0]
        content_h = slot[1] + chrome[1]
        if not self._zoombar_inside:
            content_h += self._zoombar_h() + _PANEL_SPACING
        if self._caption is not None:
            avail_w = win_w - 2 * LIGHTBOX_SHADOW_PAD - chrome[0]
            cap_w = self._caption_wrap_w(slot[0], avail_w)
            self._caption.set_size_request(cap_w, -1)  # the wrap width, exactly
            content_w = cap_w + chrome[0]
            content_h += self._caption_extra_h(cap_w)
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

    # -- the -/+ zoom bar ----------------------------------------------------

    def _zoombar_h(self) -> int:
        """The bar's natural height with its variable bottom margin taken
        back out (GTK4's measure includes margins) — so the value doesn't
        depend on where the bar currently sits."""
        _min, nat, _mb, _nb = self._zoombar.measure(Gtk.Orientation.VERTICAL, -1)
        return nat - self._zoombar.get_margin_bottom()

    def _place_zoombar(self, inside: bool) -> None:
        """Float the bar over the image's bottom edge, or — when it would
        cover too much of a small image (editorfiles.lightbox_zoombar_inside)
        — sit it below the image in the image column. Floating, it fades out
        while the pointer isn't over the image; below, it is always shown."""
        if inside == self._zoombar_inside:
            return
        self._zoombar_inside = inside
        if inside:
            self._image_col.remove(self._zoombar)
            self._zoombar.set_margin_bottom(_ZOOMBAR_MARGIN)
            self._slot.add_overlay(self._zoombar)
            if self._hover is not None and self._hover.contains_pointer():
                self._wake_zoombar()  # zooming via the bar: pointer is on it
            else:
                self._arm_zoombar_fade()
        else:
            self._slot.remove_overlay(self._zoombar)
            self._zoombar.set_margin_bottom(0)
            self._wake_zoombar()
            # Right under the slot, so the bar hugs the image it zooms and
            # any caption stays the column's last word.
            self._image_col.insert_child_after(self._zoombar, self._slot)

    def _wake_zoombar(self) -> None:
        if self._fade_source:
            GLib.source_remove(self._fade_source)
            self._fade_source = 0
        self._zoombar.remove_css_class("faded")
        self._zoombar.set_can_target(True)

    def _arm_zoombar_fade(self) -> None:
        """(Re)start the countdown to fading the floating bar out."""
        self._wake_zoombar()
        self._fade_source = GLib.timeout_add(_ZOOMBAR_FADE_DELAY_MS, self._fade_zoombar)

    def _fade_zoombar(self) -> bool:
        self._fade_source = 0
        self._zoombar.add_css_class("faded")
        self._zoombar.set_can_target(False)  # invisible: don't swallow clicks
        return GLib.SOURCE_REMOVE

    def _on_slot_enter(self, _ctrl, _x, _y) -> None:
        if self._zoombar_inside:
            self._wake_zoombar()

    def _on_slot_leave(self, _ctrl) -> None:
        if self._zoombar_inside:
            self._arm_zoombar_fade()

    # -- input ---------------------------------------------------------------

    def _press_closes(self, x: float, y: float) -> bool:
        """Whether a press at shade-local (x, y) should close the lightbox:
        anything that isn't the image column (the slot, plus the zoom bar
        when it sits below the image) or a button."""
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None and target is not self:
            if (
                target is self._slot
                or target is self._image_col
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
    caption: str | None = None,
) -> None:
    ImageLightbox(path, can_open_in_editor, on_open_in_editor, caption).present_over(parent)
