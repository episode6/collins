# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""A changed image, shown as an image, in the PR view's Files list.

The diff is the wrong renderer for a picture. git says
``Binary files a/icon.png and b/icon.png differ`` and stops; an SVG fares
worse, spilling a screen of path data that says a shape changed without ever
showing it. Reviewing artwork in the panel meant leaving for the browser.

So a file whose name says image (`prblobs.is_image`) gets its blobs fetched
and drawn instead: **Before** beside **After** for a file the PR changed,
one picture alone for one it adds or deletes, each a click away from the
lightbox at full size. Which sides a file has is `prblobs.sides`' answer
(GTK-free, so the change-type rules are tested where CI can run them); the
drawing is `imagediff`'s, shared with the git page's native diff view —
this module only says where the bytes come from.

The bytes come from `prblobs` (a `gh api` blob fetch, so private repositories
and Enterprise hosts work), routed through `pictures.fetch` so a preview is
downloaded once per commit per run, is decoded no bigger than it is drawn,
measures height-for-width in the column, and animates when it is a GIF. What
lands is a *file*, which is what lets a click hand it to the lightbox.

It honors the ``pr_inline_images`` setting: off, the Files view renders
exactly the patch it always did.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from . import imagediff, prblobs, prdetail  # noqa: E402
from .i18n import _  # noqa: E402

# Re-exported: the section height cap is imagediff's now.
MAX_HEIGHT = imagediff.MAX_HEIGHT


def preview(file: prdetail.PrFile, detail: prdetail.PullRequestDetail) -> Gtk.Widget | None:
    """The picture(s) for *file*, or None when there is no image to show.

    None — the caller renders the patch alone — for a file that isn't an
    image by name, and for one whose commits the reply didn't carry
    (`prblobs.sides` decides both).

    Main thread only. The fetches start here and land later; the widget
    handed back is the slot they land in. They queue behind `pictures`' own
    three-at-a-time gate, so a PR that regenerates thirty screenshots fills
    its sections in progressively rather than opening thirty `gh` processes
    at once.
    """
    sides = prblobs.sides(file, detail)
    if not sides:
        return None
    # Captioned only when both are shown: one picture on its own is the file,
    # and the header above it already says the PR added or deleted it.
    both = len(sides) > 1
    return imagediff.preview_row([_side(side, both) for side in sides])


def _side(side: prblobs.Side, captioned: bool) -> imagediff.ImageSide:
    caption = "" if not captioned else (_("Before") if side.before else _("After"))
    return imagediff.ImageSide(
        key=side.key,
        path=side.path,
        caption=caption,
        fetcher=lambda: prblobs.fetch_to_file(side.repository, side.ref, side.path),
    )
