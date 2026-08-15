# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-15. Full change history: git log for this file.

"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import (
    APP_ID,
    DEBUG_APP_ID,
    attachrecords,
    buildinfo,
    clisetup,
    cliwelcome,
    editorfiles,
    ghwelcome,
    mcpserver,
    mcptools,
    proctree,
    providers,
    remoteimages,
    tooltipmute,
)
from .caffeine import duration_seconds, follow_poll, follows_activity, grace_seconds
from .i18n import _
from .lightbox import present_image_lightbox
from .prefs import apply_color_scheme
from .prstatus import parse_pr_url
from .state import AppState
from .store import SessionStore
from .terminal import TerminalTab
from .window import MainWindow, session_window

# Bundled icons (e.g. tab-close-symbolic); found by name when installed.
_BUNDLED_ICONS = Path(__file__).resolve().parent.parent / "data" / "icons"

_CSS = b"""
.group-header { padding: 10px 10px 4px 10px; }

/* the image lightbox (lightbox.py): the widget itself is the shade, floated
   over the whole window in MainWindow.lightbox_overlay */
.lightbox-shade {
  background-color: alpha(black, 0.6);
}
/* a light image would blend into a light app behind the shade; the classic
   lightbox drop shadow keeps its edge readable. On the slot, not the
   picture: the slot's scrolled view would clip a child's shadow. */
.lightbox-shade .lightbox-slot {
  box-shadow: 0 4px 24px alpha(black, 0.45);
}
/* big captioned actions; flat buttons would disappear against the shade, so
   a faint plate that brightens on hover keeps them reading as clickable.
   The shade is dark in both themes, so the plate and its text are too. */
.lightbox-shade button.lightbox-action {
  padding: 12px;
  border-radius: 12px;
  color: white;
  background-color: alpha(white, 0.12);
}
.lightbox-shade button.lightbox-action:hover {
  background-color: alpha(white, 0.22);
}
/* the -/+ zoom bar floating over the image's bottom edge: a dark pill so
   the round buttons stay readable over any image content. While floating
   it fades out when the pointer leaves the image (lightbox.py toggles
   .faded); sitting below a small image it stays fully shown. */
.lightbox-shade .lightbox-zoombar {
  background-color: alpha(black, 0.35);
  border-radius: 999px;
  padding: 6px;
  transition: opacity 400ms ease;
}
.lightbox-shade .lightbox-zoombar.faded {
  opacity: 0;
}
/* the round -/+ zoom buttons in that bar */
.lightbox-shade button.lightbox-zoom {
  color: white;
  background-color: alpha(white, 0.12);
  border-radius: 999px;
  min-width: 34px;
  min-height: 34px;
}
.lightbox-shade button.lightbox-zoom:hover {
  background-color: alpha(white, 0.22);
}
.lightbox-shade button.lightbox-zoom:disabled {
  color: alpha(white, 0.4);
}
/* the agent-supplied caption under the image (show_image's caption arg);
   the shade behind it is dark in both themes, so the text is light */
.lightbox-shade .lightbox-caption {
  color: alpha(white, 0.9);
}

/* insertion line while dragging a project header to a new position */
row.drop-above { box-shadow: inset 0 2px 0 0 @accent_bg_color; }
row.drop-below { box-shadow: inset 0 -2px 0 0 @accent_bg_color; }

/* make the active tab clearly stand out from inactive ones. Its background
   color is set dynamically, not here: themes._apply_dynamic_theme_css keeps
   it matched to the current terminal theme's background (see themes.py), so
   the tab reads as part of the terminal it sits above rather than a
   mismatched frame around it. libadwaita marks the active AdwTabBar row
   with the GTK state `:selected`, not `:checked` (`:checked` is for
   checkbox/toggle-style widgets and silently matches nothing here). */
tabbar tab:selected {
  box-shadow: inset 0 -3px 0 #D97757;
}
tabbar tab:selected label { font-weight: bold; }
tabbar tab:not(:selected) label { opacity: 0.6; }

/* attach-file button floating over the terminal's bottom-left corner. Only
   its shape lives here: its colors come from themes._apply_dynamic_theme_css,
   which inverts the current terminal theme (semi-transparent fg-colored pill,
   bg-colored icon) so it contrasts with any palette. background-image: none
   strips Adwaita's own button fill so that background-color is the whole
   story. */
.attach-overlay {
  border-radius: 9999px;
  background-image: none;
  border: none;
  box-shadow: none;
  min-width: 22px;
  min-height: 22px;
  padding: 3px;
}

/* the composer panel sliding up over the terminal's bottom edge. Shape only,
   as with .attach-overlay above: its colors come from
   themes._apply_dynamic_theme_css so it reads as a surface of the terminal's
   own theme rather than an app-colored card floating on it. */
.composer-panel {
  padding: 8px;
  border-radius: 12px 12px 0 0;
}

/* a panel tab floated over the whole session tab by the tab row's overlay
   button (paneldock._MaxPane). Opaque on purpose: it hangs in an overlay
   over the terminal and the editor column, and anything showing through
   would read as a rendering fault rather than a maximized tab. Its thin
   bar -- the restore button and the page's title -- is divided from the
   page below by the same hairline the rest of the app draws seams with. */
.panel-maximized {
  background-color: @window_bg_color;
}
.panel-maximized .panel-maximized-bar {
  padding: 3px 6px;
  border-bottom: 1px solid alpha(currentColor, 0.15);
}

/* docked in a panel page, the composer is a pane, not a floating card */
.composer-panel.docked {
  border-radius: 0;
}

/* the attachments handle on the terminal's right edge: the same pill as
   .attach-overlay above (whose colors it also borrows), stood on end -- tall
   and narrow, so it reads as an edge to pull rather than a second button. */
.attachments-handle {
  min-width: 18px;
  min-height: 46px;
  padding: 3px 1px;
}

/* the attachments panel sliding in over the terminal's right edge. Shape
   only, as with the composer above: its colors come from
   themes._apply_dynamic_theme_css, so it reads as a surface of the
   terminal's own theme. Rows are flat buttons -- a preview with its caption
   under it -- outlined only enough to tell one picture from the next. */
.attachments-panel {
  border-radius: 12px 0 0 12px;
}
.attachments-panel .attachments-header {
  padding: 6px 6px 6px 12px;
}
.attachments-panel .attachments-list {
  padding: 6px 10px 10px 10px;
}
.attachments-panel .attachments-empty {
  padding: 24px;
}
.attachments-panel .attachment-row {
  padding: 6px;
  border-radius: 8px;
}
/* the preview / file-face slot draws as a chip: a hairline of the panel's
   own foreground around rounded corners (the widget clips its child to
   them, so a picture's corners follow). .filled only -- while an image row
   waits its turn to decode the slot is empty, and a border around nothing
   is a stray line over the caption. */
.attachments-panel .attachment-thumb {
  border-radius: 6px;
}
.attachments-panel .attachment-thumb.filled {
  border: 1px solid alpha(currentColor, 0.25);
}
.attachments-panel .attachment-standin {
  padding: 12px 8px;
}
/* a non-picture file's row face: file-type icon beside the bare name,
   padded like the stand-in so the two one-line chips rhyme */
.attachments-panel .attachment-file {
  padding: 8px;
}
.attachments-panel .attachment-caption {
  font-size: 0.9em;
  opacity: 0.8;
}

/* docked in a panel page, the panel is a pane, not a floating card (see
   .composer-panel.docked above) */
.attachments-panel.docked {
  border-radius: 0;
}

/* dropped-image previews above the composer's text box: square crops with
   a remove button that only shows itself while the pointer is on the thumb
   (hover state reaches the overlay from any child under the pointer) or
   while the button itself holds keyboard focus -- it stays in the tab
   order, so a keyboard user can still reach and see it */
.composer-thumbs picture {
  border-radius: 6px;
}
.composer-thumb .composer-thumb-remove {
  opacity: 0;
  margin: 2px;
  min-width: 20px;
  min-height: 20px;
  padding: 2px;
  transition: opacity 150ms ease;
}
.composer-thumb:hover .composer-thumb-remove,
.composer-thumb:focus-within .composer-thumb-remove {
  opacity: 1;
}

.count-badge {
  background-color: alpha(currentColor, 0.1);
  border-radius: 10px;
  padding: 1px 8px;
  font-size: 0.8em;
}

/* a session with something going on -- a tab open, or running detached -- is
   drawn as an outlined card with a left guide line; what a running one adds
   is a full-strength title (every other row's dims), and (for a detached one)
   a status color on that line. Every idle row keeps this same box with its
   borders turned transparent -- all but the red guide line of an interrupted
   one (see the :not(.running):not(.detached) rules below) -- so the border
   box is identical in every status and a row never
   shifts or resizes as its status changes; the selected tab's row is the one
   exception, and it changes only horizontally (see .active-tab below).
   The left indent is a widget margin set in sidebar.py, not a CSS margin
   here: it tracks the configurable project-icon size, so the card always
   starts just past the icon of the project header above it. */
row.session-child {
  margin-right: 16px;
  border: 1px solid alpha(currentColor, 0.15);
  border-left: 2px solid alpha(currentColor, 0.15);
  border-radius: 0 8px 8px 0;
  /* Ours rather than Adwaita's stock 8px, and half of it. What follows it is
     the row's leading mark, which reserves 4px of badge overhang on its left
     (see prmenu._mark, where that scales with the badge -- 4px at the sizes
     the row asks for) and is held off the title by its own padding and the
     content box's spacing. Stock 8px here, plus a 4px margin the content box
     used to carry, put the icon twice as far from the guide line as from the
     title it leads -- a lopsided slot in which it read as belonging to
     neither. With the margin gone (see SessionRow) this is only half of the
     gap on that side; the mark button's own 4px of left padding is the rest,
     and it is the half to move (see menubutton.pr-mark below), because the
     barber pole below is positioned by counting back over the border and this
     padding, so those two values move together. */
  padding-left: 4px;
  /* smaller text than a stock sidebar row, and shorter: 34px of content plus
     the 1px borders puts the row at 36px, with the project headers keeping the
     coarser rhythm above it */
  min-height: 34px;
  font-size: 0.9em;
  /* the .active-tab handoff (below) animates instead of snapping: the fill
     fades while the card slides out to the panel edge -- or back in, on the
     row giving the highlight up, which is why the transition sits here on the
     base rule rather than on .active-tab. The two radius longhands are spelled
     out because they are what actually changes; the same clock also runs the
     content box's counter-margin (see the > box rule below), keeping the row's
     content pinned while only the card edge moves. Hover shares the
     background-color transition, softening on and off, and the three
     card-outline border sides fade with it (visible only on outlined rows,
     hover being what brightens them). The guide line -- border-left-color --
     is deliberately not in this list: it is the status channel, and every
     signal it carries is designed around instant or self-animated changes
     (the unread pulse, the busy pole's stripes showing through a border that
     must go transparent the moment they start), so a transition there would
     smear one signal into the next. GTK drops CSS transitions when the
     desktop's animations are off, so reduced motion gets the old instant
     switch for free. */
  transition: background-color 200ms ease, margin-right 200ms ease,
    border-top-right-radius 200ms ease, border-bottom-right-radius 200ms ease,
    border-top-color 200ms ease, border-right-color 200ms ease,
    border-bottom-color 200ms ease;
}
/* neither the archive/close button nor the selection-mode check may hold the
   row open at its stock size: entering selection mode must not resize rows */
row.session-child button {
  min-height: 20px;
  min-width: 20px;
  padding: 0 4px;
}
row.session-child checkbutton {
  min-height: 20px;
  min-width: 20px;
  padding: 0;
  margin-right: 4px;  /* keep the shrunken check off the title */
}
/* the pull request mark ahead of the title (see SessionRow): a menu button
   standing in a line of text, so it takes only the width of the two icons in
   it rather than the tap target the row's other buttons keep. Its padding is
   even -- 4px a side -- and what that buys is not: with the row's own 4px the
   mark's 20px slot stands 8px off the guide line, and with the content box's
   2px of spacing it stops 6px short of the title's text box. Six and eight is
   what measures even on screen, because the two sides are not read against
   the same thing. The left is read against the guide line, which is ink; the
   right against the title's first glyph, which sits a pixel or two inside its
   own box on its left side bearing. The 6px this padding used to hold on the
   right squared the two sides on paper and left the mark visibly nearer the
   line than the title -- 8px of white against 10. Every distance in this
   comment and the next is measured from the guide line, the row's padding
   included, and against ink rather than boxes where they disagree.
   The 4px of badge overhang inside that slot is not the gap it looks like: on
   a mark that carries a badge the badge is exactly what hangs into it, so
   counting it as gap left the colored mark reading as crowded against the
   line however even the two sides measured. This padding is the gap the
   overhang cannot be, and it is the one value to move to change how far the
   mark stands off the line: image.agent-mark below and the two mark-less rows
   in sidebar.py (PlaceholderRow, NewThreadRow) all align to this column and
   have to move with it.
   A base with no badge over it -- a merged PR's mark, and the agent mark
   below -- is centered inside the slot rather than filling it (see
   prmenu._mark), so it inherits the slot's own 8-and-6 and reads about 2px
   nearer the line than the badged marks in the same column. That is the trade
   this value can't get out of: one padding cannot center both a mark whose
   badge fills the overhang and a base that leaves it empty, and the badged
   marks are the ones carrying color, so they are the ones to land square.
   The selector is the menubutton node, not the button one: pr-mark is set on
   the GtkMenuButton, whose CSS node is "menubutton" wrapping the "button" node
   that carries the padding. Written as button.pr-mark it matches nothing, and
   the mark silently keeps the generic row-button metrics above. */
row.session-child menubutton.pr-mark > button {
  min-height: 20px;
  min-width: 0;
  padding: 0 4px;
}
/* what stands in that slot when the session has no pull requests: the agent's
   own mark (see SessionRow). Its margins center it on the same 20px slot the
   mark occupies -- 16px of icon plus 4px of badge overhang, 8px off the guide
   line -- rather than standing it where the mark's base icon starts within
   that slot: a badged mark fills the slot corner to corner and reads as
   centered on it, so an icon aligned to the base alone sits visibly high and
   to the right of the marks above and below it (prmenu._mark centers its own
   unbadged bases for the same reason). Left and right margins together still
   come to the mark's 4 + 4 plus the 4px of overhang this icon has no badge to
   hang into, so a session that opens its first PR trades one for the other
   without the title moving under it. */
row.session-child image.agent-mark {
  margin: 0 6px;
}
row.session-child checkbutton > check {
  min-height: 14px;
  min-width: 14px;
  margin: 0;
}
row.session-child:hover {
  background-color: alpha(currentColor, 0.1);
  border-color: alpha(currentColor, 0.3);
}
/* sessions running in an open tab are the ones the panel is really about, and
   they say so through their titles: theirs read at full strength while every
   other row's dims back. No fill carries this: the resting card looks the
   same in every status, so the only filled rows in the list are the selected
   tab's (orange, below) and whichever one the pointer is on, and neither has
   to compete with a wash of gray for attention.

   Detached (/bg) sessions dim with the rest. They are running, but there is no
   tab to return to, and their guide line is what says they are still going. */
row.session-child:not(.running) .session-title {
  opacity: 0.55;
}
/* ...and the dim fades on and off rather than snapping, on the same clock as
   the card transitions above: opening a tab brightens the title as the card
   outline draws in, closing one dims it as the outline dissolves. The
   transition sits on the bare class so it runs in both directions. */
.session-title {
  transition: opacity 200ms ease;
}
/* a finished run nobody has looked at yet (see SessionItem.unread): the guide
   line breathes green -- the run is done and its result is waiting -- until
   the user returns to the tab. It held that green still until the pull
   request marks arrived beside the titles, where the checks-passed glyph is
   green too: a still line an inch away from a still glyph of the same hue
   reads as more of the same chrome, so the flag now says "look here" by
   moving. Nothing else in a row moves slowly, which is the whole signal.

   Two rules, and the split is load-bearing. The color alone sits on the plain
   .unread selector, where the .detached and .interrupted rules below still
   outrank it on source order, exactly as they did when it was still. The
   animation cannot ride along: an animated property beats every normal
   declaration in the cascade, so keyframes on that selector would repaint the
   line green right over the yellow and red those rules set. The pulse is
   therefore fenced off from all three of the statuses that outrank it --
   .busy (whose barber pole would also lose its own animation to this one,
   the selector being more specific) and the two colors below.

   The pulse is slow where the busy pole is fast, so the two never read as the
   same signal: blue stripes climbing means an agent is working, one green
   line breathing means it stopped and left something for you. GTK stops CSS
   animation when the desktop's animations are off, which leaves the still
   #2ec27e of the first rule -- the old solid green, the reduced-motion
   fallback for free, and the reason the color does not live in the
   keyframes. */
@keyframes unread-pulse {
  0%   { border-left-color: #26a269; }
  50%  { border-left-color: #8ff0a4; }
  100% { border-left-color: #26a269; }
}
row.session-child.unread { border-left-color: #2ec27e; }
row.session-child.unread:not(.busy):not(.detached):not(.interrupted) {
  animation: unread-pulse 3s ease-in-out infinite;
}

/* running detached (/bg): the one status the guide line still speaks for.
   Below the .unread rule so yellow wins the line: a detached session is
   running, not sitting on a result -- green is a tab-only signal, and
   window.py keeps the flag itself off tabless rows (see _sync_status), so
   this order is the paint-level backstop for the same rule. */
row.session-child.detached { border-left-color: #e5a50a; }

/* the user stopped Claude mid-task and nothing has happened since (see
   SessionItem.state): the guide line turns red. Deliberately last of the
   equal-specificity line colors (.unread, .detached), so the interruption,
   the most actionable "needs you" signal, takes the line when the stopped
   run is also detached or unseen -- state is scanned for every session, so
   a /bg row whose transcript ends in an interruption is a reachable
   combination, not a hypothetical. The .busy pole rule still beats it (one
   more class on its selector) for the same reason it beats unread: a
   resumed session should move, not sit on a stale interruption.
   It is also the only one of these colors an idle row keeps: the rule below
   takes the card and the line off a row with nothing going on, and carves
   this one out. */
row.session-child.interrupted { border-left-color: #e01b24; }

/* nothing going on: no tab open, and not running detached either. Those rows
   are most of a long list -- every session ever started is still in it -- and
   drawn as cards they stack up into a wall of outlines that the few rows
   actually doing something have to compete with. So the card is only drawn
   around a session with something to say, and the rest keep the box with its
   borders turned transparent: their titles still line up with the cards' and
   no row changes size as a session starts, stops or is handed to the
   background.

   Two rules, because the guide line is not part of the card here. The box
   goes in the first: three sides, named one at a time rather than through the
   border-color shorthand, which would take the left with them. The line goes
   in the second, and only for a row with nothing to say on it -- an
   interrupted session keeps its red whether or not a tab is open, since being
   stopped mid-task outlives the tab it was stopped in and is the one thing an
   otherwise idle row still needs to flag. The other two line colors above
   never reach an idle row anyway (.detached is a running status by
   definition, and window._sync_status drops .unread from any row whose tab is
   gone), and .busy only paints alongside .running, so the barber pole below
   stays out of this too.

   Both rules are more specific than every border rule above, so order among
   them doesn't matter -- but the red itself is still set once, up at
   .interrupted, and simply left standing here.

   The stand-in row under an empty project (see NewThreadRow) falls in here
   for free: it stands for no session, so it carries no status class, and an
   outlined card there would count as a row in a list that has none.

   Hover still fills -- the row is clickable and says so -- but does not draw
   the card back in around it, which would read as the row changing rather
   than as the pointer arriving. */
row.session-child:not(.running):not(.detached) {
  border-top-color: transparent;
  border-right-color: transparent;
  border-bottom-color: transparent;
}
row.session-child:not(.running):not(.detached):not(.interrupted) {
  border-left-color: transparent;
}

/* An agent producing output right now (see activity.py) turns its row's guide
   line into a barber pole: stripes climbing while work is happening, still the
   moment it stops. Only sessions in a tab pole: a detached (/bg) one has no
   terminal to listen to, so it keeps the still yellow line of the .detached
   rule above whatever its agent is doing.

   The pole is painted, never laid out: the row keeps its 2px border-left (made
   transparent so the stripes show through it) and the gradient is a background
   layer exactly as wide as that border and sitting right on it, so a row's
   guide line reads as the same line whether it is still or moving, and nothing
   shifts as the pole starts or stops.

   Landing it there takes two properties, because GTK does not place a
   background where the CSS spec says. It ignores background-origin: the layer
   starts inside the border *and* the padding, 6px in for this row (2px border
   + 4px padding, pinned above), so background-position counts that whole
   distance back. And its default clip stops at the padding edge, which would
   throw the shifted layer away entirely, so the clip is widened to the border
   box. Both are load-bearing; drop either and the pole either sits 6px inside
   the card or vanishes.

   The tile is 2x12px and repeats down the line; the stripe period is 8.485px
   (12 / sqrt 2), the one value at which a 135deg gradient meets itself across
   a 12px vertical seam. Any other period leaves a visible cut where the tiles
   stack. One animation cycle travels exactly one tile, so the loop is
   seamless too. GTK stops all CSS animation when the desktop's animations are
   off, which is the reduced-motion behavior we want for free. */
@keyframes barber-pole {
  from { background-position: -6px 12px; }
  to   { background-position: -6px 0; }
}
row.session-child.running.busy {
  border-left-color: transparent;
  background-clip: border-box;
  background-repeat: repeat-y;
  background-size: 2px 12px;
  animation: barber-pole 900ms linear infinite;
  background-image: repeating-linear-gradient(135deg,
    #1c71d8 0px, #1c71d8 4.243px, #99c1f1 4.243px, #99c1f1 8.485px);
}
/* the session shown in the currently selected tab: the fill says which one it
   is, and the card runs out to the panel's right edge (square-cornered, with
   no right border) so the row reads as joined to the terminal it is showing
   rather than as one more card in the list. The guide line keeps saying what
   the session's status is. Only horizontal geometry changes, so the row keeps
   its height and nothing in the list shifts as the selected tab moves. */
row.session-child.active-tab {
  background-color: alpha(#D97757, 0.16);
  margin-right: 0;
  border-right: none;
  border-radius: 0;
}
row.session-child.active-tab:hover {
  background-color: alpha(#D97757, 0.22);
}
/* ...but only the card runs out to the edge: its content stays where the
   content of every other row is. The 17px the card gave up on the right
   (16px margin + 1px border) comes back as a margin on the content box, so
   the timestamp, the hover buttons and a long folder path all line up with
   the rows above and below instead of sliding out to the panel edge too. */
row.session-child.active-tab > box,
row.session-child.active-tab > revealer > box {
  margin-right: 17px;
}
/* ...and the counter-margin rides the same 200ms clock as the card's own
   margin (see row.session-child above), so the two sum to a near-constant
   right inset at every frame of the handoff and the content never visibly
   drifts. (Only near-constant: the 1px border-right is discrete, border-style
   not being animatable -- a one-pixel step nobody can see.) */
row.session-child > box,
row.session-child > revealer > box {
  transition: margin-right 200ms ease;
}
/* The revealer is an arriving row's (see sidebar.py's _arrive_by_slide: a
   "New Thread" placeholder, or a session row Undo restored), which is why
   the two rules above have to reach through it: an arrived row can be the
   selected tab like any other row, and its content has the same right inset
   to keep. */

/* A row arriving in the list (see _arrive_by_slide in sidebar.py): the row
   hands its height to the revealer inside it for the length of the slide,
   so the slot grows from nothing as the content slides down out from behind
   the row above, and the rows below move down ahead of it rather than after
   it. The body inside keeps the height the row gives up for the length of
   the slide, so the slot the revealer opens is the height the row will
   stand at: 26px here plus the 4px the body is margined by top and bottom
   (see PlaceholderRow) is the row's own 34 floor, and a session row's body
   is shorter than that too. Both halves are scoped to .arriving and come off
   together -- the floor simply moves back from the body to the row, at the
   same height, so nothing jumps. Leaving the body's min-height on afterwards
   is what an arrived row must not have: stretched to the floor it fills the
   row instead of being centered in it (valign CENTER measuring its content),
   which sat every restored row's title ~3px high. */
row.session-child.arriving {
  min-height: 0;
}
row.session-child.arriving > revealer > box {
  min-height: 26px;
}

/* A row on its way to the archive (see SessionRow.begin_archiving): the
   archive itself can take seconds when the session's tab has to shut its
   agent down first, so the row answers the click at once by sliding out of
   the panel to the left as it fades. The translation doesn't measure the
   panel; it only has to outrun the fade, and past 440px the row is gone at
   any plausible panel width. ease-in, not ease: leaving accelerates. */
row.session-child.archiving {
  transform: translateX(-440px);
  opacity: 0;
  transition: transform 250ms ease-in, opacity 250ms ease-in;
}
/* ...then, the slide done (sidebar.py adds this class and hides the row's
   content box), the emptied slot closes up. With the content gone the row's
   height is nothing but min-height and its two horizontal borders, and all
   of it animates to zero -- so by the time the archive really lands and the
   rebuild drops the row, there is no longer anything on screen to notice
   disappearing. Cancelling the close instead brings the row back whole:
   classes off, content shown, and with them gone these transitions don't
   run, so the return is an instant snap rather than a reverse slide. */
row.session-child.archiving.archiving-gone {
  min-height: 0;
  border-top-width: 0;
  border-bottom-width: 0;
  transition: min-height 200ms ease, border-top-width 200ms ease,
    border-bottom-width 200ms ease;
}

/* The session list's scrollbar rides the panel's right edge. Stock Adwaita
   insets its trough from that edge (4px as the thin overlay indicator, 8px
   once the pointer nears it and it expands), which leaves the bar floating in
   the gutter beside the row cards, reading as one more column of the list.
   Dropping the margin puts it against the border, where it reads as chrome
   belonging to the panel. The trough keeps its own width in both states, so
   only its position moves, and being an overlay scrollbar it takes no width
   from the list either way: no row reflows. */
.sidebar-scroll > scrollbar.vertical > range > trough {
  margin-right: 0;
}

/* ...and its slider is solid, not translucent, in the same color the panel
   border next to it appears in. That border is Adwaita's paned separator:
   currentColor at --border-opacity composited over the window background, so
   the same mix (against the background rather than transparent) is that
   exact rendered color, opaque. Plain currentColor would be the full-strength
   foreground (white in dark mode); stock Adwaita instead mixes it 20-60% into
   transparent (resting/hover/drag), which lets the row cards bleed through
   the bar. This provider sits at APPLICATION priority, so the one rule
   outranks every state variant in the theme; the fade-in/out of the overlay
   indicator is widget opacity animated by GTK itself, not a style, so idle
   hiding still works. */
.sidebar-scroll > scrollbar.vertical > range > trough > slider {
  background-color: color-mix(in srgb, currentColor var(--border-opacity), var(--window-bg-color));
}

/* a chat turn's card */
.chat-card {
  background-color: @window_bg_color;
  border: 1px solid alpha(#D97757, 0.6);
  border-radius: 12px;
  padding: 12px;
  box-shadow: 0 2px 12px alpha(black, 0.35);
}
.chat-card-header {
  color: #D97757;
  font-weight: bold;
  font-size: 0.85em;
}

/* visual bell: a terminal's BEL tints the header bar, plus the ringing
   session's tab header and sidebar row, all fading out over the animation.
   An inset shadow rather than background-color, so the flash composites over
   each widget's normal background instead of replacing it. The spread must
   exceed the widget's width: GSK fills an inset spread from the center out,
   not from the edges in, so a smaller spread tints a band in the middle of
   the widget instead of all of it. */
@keyframes bell-flash {
  from { box-shadow: inset 0 0 0 9999px alpha(#D97757, 0.4); }
  to   { box-shadow: inset 0 0 0 9999px alpha(#D97757, 0); }
}
headerbar.bell-flash,
tabbar tab.bell-flash,
row.session-child.bell-flash {
  animation: bell-flash 400ms ease-out;
}
/* The attachments handle takes the same .bell-flash class (flash.py is the
   app's one "look here", see TerminalTab._note_attachment_news) but not this
   animation, for two reasons. An inset shadow spread across a 9999px-radius
   capsule bands -- a hard-edged bright rectangle between two dimmer caps,
   invisible on the near-square widgets above and plain to see on a 20px
   pill. And this one fades back to nothing, while the handle's flash has to
   *land* somewhere: it fires as the pill lights up for an unseen image, so
   draining out would flash orange, fade to grey, then snap back to orange.
   It animates background-color into the lit color instead -- and since both
   ends of that are terminal colors, the rule lives with the rest of the
   handle's paint in themes._apply_dynamic_theme_css. */

/* a busy row's barber pole also claims the animation property, with one more
   class on its selector, which would silently drop the flash exactly when a
   bell is most likely (the agent is mid-turn): the row that is busy while its
   bell rings must carry both animations in one declaration. */
row.session-child.running.busy.bell-flash {
  animation: barber-pole 900ms linear infinite, bell-flash 400ms ease-out;
}

/* slim per-tab footer row: working directory + terminal-panel buttons */
.tab-footer {
  padding: 1px 8px;
  border-top: 1px solid alpha(currentColor, 0.15);
}
/* The footer buttons read as a row of icons, not as buttons: tight enough
   that the icons sit close together, and with no hover background (the
   tooltip and the pointer are feedback enough at this size). */
.tab-footer button {
  padding: 0 2px;
  min-height: 22px;
  min-width: 20px;
}
.tab-footer button:hover {
  background: none;
  box-shadow: none;
}

/* ...except inside the footer's PR list, which is a menu and reads as one. A
   popover belongs to the widget tree of the button it hangs off, so the two
   rules above apply to its rows too: without these they sit 22px tall with
   2px of side padding, flush against the popover's edge, and stay flat as the
   pointer crosses them. The contents padding gives the list room on every
   side (its longest line was reaching the frame), and each row gets menu-item
   geometry plus a hover fill, so the pointer says what a click would open. */
popover.pr-menu > contents {
  padding: 6px;
}
popover.pr-menu button.pr-menu-row {
  padding: 4px 8px;
  min-height: 28px;
  border-radius: 6px;
}
popover.pr-menu button.pr-menu-row:hover {
  background-color: alpha(currentColor, 0.1);
}
/* The actions submenu's header when there is no list to lead back to (a
   footer chip's own menu): a plain caption naming the PR, padded like the
   rows under it so the column of titles still lines up. */
popover.pr-menu .pr-menu-title {
  padding: 4px 8px;
  min-height: 28px;
}

/* The "Open in <app>" rows of a project's context menu, which carry an icon
   and so can't be menu items (GtkModelButton hides an icon that shares its
   row with a label). Buttons instead, given the geometry of the model buttons
   they sit between: the same height and the same side padding, so an icon row
   lines up with the plain items above and below it. */
popover.menu button.open-with-row {
  padding: 0 10px;
  min-height: 26px;
  border-radius: 6px;
  font-weight: inherit;
}
popover.menu button.open-with-row:hover {
  background-color: alpha(currentColor, 0.1);
}

/* sidebar usage panel: subscription limit bars under the session list */
.usage-panel {
  padding: 8px 12px 10px 12px;
  border-top: 1px solid alpha(currentColor, 0.15);
}
/* The refresh button is a secondary affordance next to the "Claude usage"
   heading: shrunk to roughly the heading's own height so it stops padding
   out the header row and dominating the panel. */
.usage-panel button.usage-refresh {
  padding: 0 2px;
  min-height: 20px;
  min-width: 20px;
}
.usage-panel button.usage-refresh image {
  -gtk-icon-size: 13px;
}
/* The heading itself is the collapse/expand toggle: strip the button chrome
   so it still reads as the plain caption heading it replaced, with just the
   caret giving the affordance away. */
.usage-panel button.usage-toggle {
  padding: 0 2px;
  min-height: 20px;
}
.usage-panel button.usage-toggle image {
  -gtk-icon-size: 13px;
}
.usage-panel progressbar.usage-bar trough,
.usage-panel progressbar.usage-bar progress {
  min-height: 6px;
  border-radius: 3px;
}
.usage-panel progressbar.usage-bar progress { background-color: #D97757; }
.usage-panel progressbar.usage-bar.usage-sev-warning progress { background-color: #e5a50a; }
.usage-panel progressbar.usage-bar.usage-sev-critical progress { background-color: #e01b24; }

/* chat-session tab: streaming bubbles + tool chips */
.chat-bubble {
  padding: 8px 12px;
  border-radius: 14px;
}
.chat-user {
  background-color: #D97757;
  color: white;
}
.chat-assistant {
  background-color: alpha(currentColor, 0.08);
}
.chat-tool {
  font-size: 0.85em;
  opacity: 0.6;
  padding: 2px 4px;
}
/* a chip that knows its file: clickable, reads as a link */
.chat-tool-link {
  opacity: 0.8;
  text-decoration-line: underline;
}

/* file-tree rows for dotfiles and gitignored entries: still there, still
   openable, but visibly not part of the project's real content. On the row's
   content box, so the icon's file-type color dims with the label instead of
   staying at full strength next to faded text. */
.filetree-dim {
  opacity: 0.55;
}

/* native PR view panel page: header, conversation cards, label pills */
.pr-view-header {
  padding: 6px 10px;
}
.pr-view-title {
  font-weight: bold;
}
/* the view switcher and, at its end, the state's own actions: the header's
   own side padding, and enough under the row to keep it off the content */
.pr-view-switcher {
  padding: 0px 10px 6px 10px;
}
.pr-card {
  padding: 8px 10px;
  border-radius: 10px;
  background-color: alpha(currentColor, 0.06);
}
.pr-label-pill {
  padding: 0px 8px;
  border-radius: 99px;
  background-color: alpha(currentColor, 0.12);
}
/* the couldn't-load banner: content stays visible underneath, so this only
   has to read as a note, not an error page */
.pr-view-banner {
  margin: 10px 10px 0px 10px;
  padding: 6px 10px;
  border-radius: 8px;
  background-color: alpha(currentColor, 0.08);
}
.pr-check-row {
  padding: 2px 6px;
  min-height: 26px;
}
/* an image a PR body embeds, rendered in place (bodyimages.py). The
   hairline frame is on the slot rather than the picture: it hugs the
   picture either way, and while the download runs (or after it failed) the
   same frame reads as the place the picture is going. */
.pr-body-image {
  border: 1px solid alpha(currentColor, 0.15);
  border-radius: 6px;
}
.pr-body-image-standin {
  padding: 6px 10px;
}
/* the Files view's per-file sections: a quieter card than .pr-card, since a
   built diff buffer brings its own scheme-colored background on top */
.pr-file-section {
  padding: 6px 8px;
  border-radius: 8px;
  background-color: alpha(currentColor, 0.04);
}
.pr-file-path {
  font-weight: bold;
}
"""


# Colors that must follow the light/dark scheme, keyed by "is dark". GTK CSS has
# no working prefers-color-scheme query, so these are re-applied from a second
# provider whenever the effective scheme flips (see _apply_scheme_css).
#
# The footer's PR marks use GitHub's own pairs of shades, so a merged PR or a
# red build reads here exactly as it does on the PR page, in either theme.
_SCHEME_CSS = """
.pr-merged { color: %(merged_purple)s; }
/* GitHub's open-PR green is its checks-passed green, so the open base icon
   shares the shade; a draft (and a PR nothing is known about) takes GitHub's
   muted grey; a closed PR and the conflict badge both share the failed red —
   on GitHub a closed PR is that same danger red, and either of the other two
   blocks the merge; the unresolved-comments badge borrows the pending yellow —
   attention-colored, but nothing is broken, someone just has the last word. */
.pr-open { color: %(passed_green)s; }
.pr-draft { color: %(draft_grey)s; }
.pr-closed { color: %(failed_red)s; }
.pr-conflict { color: %(failed_red)s; }
.pr-unresolved { color: %(pending_yellow)s; }
.pr-checks-passed { color: %(passed_green)s; }
.pr-checks-failed { color: %(failed_red)s; }
.pr-checks-pending { color: %(pending_yellow)s; }

/* the file tree's file-type icon palette (see filetypes.py for the mapping).
   Hues from VS Code's Seti icon theme, which is built for dark backgrounds;
   the light shades are the same hues pulled down far enough to read on
   white, most borrowed from GitHub's light palette for the overlaps. */
.ft-blue { color: %(ft_blue)s; }
.ft-yellow { color: %(ft_yellow)s; }
.ft-orange { color: %(ft_orange)s; }
.ft-green { color: %(ft_green)s; }
.ft-red { color: %(ft_red)s; }
.ft-purple { color: %(ft_purple)s; }
.ft-pink { color: %(ft_pink)s; }
.ft-grey { color: %(ft_grey)s; }
"""
_MARK_COLORS = {
    False: {  # light
        "merged_purple": "#8250df",
        "passed_green": "#1a7f37",
        "failed_red": "#cf222e",
        "pending_yellow": "#bf8700",
        "draft_grey": "#59636e",
    },
    True: {  # dark
        "merged_purple": "#a371f7",
        "passed_green": "#3fb950",
        "failed_red": "#f85149",
        "pending_yellow": "#d29922",
        "draft_grey": "#9198a1",
    },
}
# The file-type icon palette, same keying. The dark shades are Seti's own
# (VS Code's default file-icon theme); each light shade is the same hue,
# darkened to hold contrast on a white sidebar.
_FILETYPE_COLORS = {
    False: {  # light
        "ft_blue": "#1a7aa8",
        "ft_yellow": "#9e6a03",
        "ft_orange": "#bc4c00",
        "ft_green": "#2da44e",
        "ft_red": "#cf222e",
        "ft_purple": "#8250df",
        "ft_pink": "#bf3989",
        "ft_grey": "#59636e",
    },
    True: {  # dark
        "ft_blue": "#519aba",
        "ft_yellow": "#cbcb41",
        "ft_orange": "#e37933",
        "ft_green": "#8dc149",
        "ft_red": "#cc3e44",
        "ft_purple": "#a074c4",
        "ft_pink": "#f55385",
        "ft_grey": "#9198a1",
    },
}


class App(Adw.Application):
    def __init__(self) -> None:
        # COLLINS_APP_ID lets a demo instance run alongside the real one (for screenshots).
        super().__init__(application_id=os.environ.get("COLLINS_APP_ID") or APP_ID)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        # The debug build (same id prefix _app_icon_name keys on) runs out of
        # a source checkout; note which commit — and whether the tree was
        # dirty — now, so the About dialog reports the launch-time state.
        if (self.get_application_id() or "").startswith(DEBUG_APP_ID):
            buildinfo.capture()
        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        if _BUNDLED_ICONS.is_dir():  # running from source; installed icons live in the system theme
            # Ahead of the system theme, not behind it (which is all
            # add_search_path can do): a machine that ran data/install.sh
            # months ago has copies of these icons under ~/.local/share and
            # /usr/share, and those directories are searched first, so the
            # checkout would render whatever artwork was current back then.
            # Silently, and often not as a missing icon: an old stroked
            # symbolic still resolves, and GTK's recolor — fill:!important on
            # every rect/circle/path, stroke untouched — turns it into a
            # silhouette or into nothing at all. Running from source means
            # running this checkout's icons.
            theme = Gtk.IconTheme.get_for_display(display)
            theme.set_search_path([str(_BUNDLED_ICONS), *theme.get_search_path()])

        # No tooltips from the UI behind an open menu (see tooltipmute).
        tooltipmute.install()

        # Scheme-dependent colors ride in their own provider so a light/dark
        # flip only reloads these few rules, never the stylesheet above.
        self._scheme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, self._scheme_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_: self._apply_scheme_css())
        self._apply_scheme_css()

        # Caffeine Mode: non-None while we hold a sleep/idle inhibitor.
        # The live toggle is deliberately not persisted — a restart must
        # never silently keep the machine awake unless the user explicitly
        # opted in via the caffeine_on_launch setting.
        self._caffeine_cookie: int | None = None
        # The flags that cookie was taken under, so a settings change can tell
        # whether the inhibitor it's holding is still the right one.
        self._caffeine_flags_held: Gtk.ApplicationInhibitFlags | None = None
        # The optional shut-off timer: when it's running, a monotonic deadline
        # (µs) and the once-a-second source that both drives every window's
        # countdown and turns Caffeine Mode off on reaching it.
        self._caffeine_deadline: int | None = None
        self._caffeine_tick: int | None = None
        # The duration key Caffeine Mode was turned on under, set only for the
        # one option whose deadline isn't a fixed span: Until idle (see
        # _follow_activity). For that mode this is the on-flag itself — it
        # outlives the inhibitor across a doze, when the cookie is dropped but
        # the mode keeps watching — and only turning the mode off clears it.
        self._caffeine_mode: str | None = None

        # Shared across all windows so scans/monitors aren't duplicated and
        # state.json writes don't race.
        self.state = AppState()
        apply_color_scheme(self.state.get_setting("color_scheme"))
        # A remembered CLI location goes on PATH before anything looks for
        # the CLI — the store's first scan is the very next line.
        clisetup.apply_saved(self.state)
        self.store = SessionStore(self.state)
        self.store.start()

        self._start_mcp_service()

        focus = Gio.SimpleAction.new("focus-session", GLib.VariantType("s"))
        focus.connect("activate", self._on_focus_session)
        self.add_action(focus)

        new_window = Gio.SimpleAction.new("new-window", None)
        new_window.connect("activate", lambda *_: self._new_window())
        self.add_action(new_window)
        self.set_accels_for_action("app.new-window", ["<Control><Shift>n"])

    # -- session MCP tools ---------------------------------------------------
    #
    # The socket service every launched session's MCP shim relays tool calls
    # through (mcpserver/mcptools/mcp_shim). The service and the handlers live
    # here because dispatch needs what only the app has: every window, every
    # tab, and the /proc ancestry walk that ties a calling shim back to the
    # tab whose shell spawned its `claude`.

    def _start_mcp_service(self) -> None:
        """Bring up the tool socket and the `--mcp-config` file it's named in.

        Any failure logs and leaves providers.MCP_CONFIG_PATH unset, so
        launched commands come out exactly as they did before the feature —
        the tools are conveniences, never load-bearing.
        """
        self._mcp_service: mcpserver.SessionToolService | None = None
        app_id = self.get_application_id()
        service = mcpserver.SessionToolService(
            mcptools.socket_path(app_id),
            list_tools=lambda: mcptools.enabled_tools(self._mcp_tool_enabled),
            dispatch=self._mcp_dispatch,
        )
        try:
            service.start()
        except (GLib.Error, OSError):
            logging.getLogger(__name__).exception("session MCP socket unavailable")
            return
        config = mcptools.write_config(app_id)
        if config is None:
            logging.getLogger(__name__).error("session MCP config not writable")
            service.stop()
            return
        self._mcp_service = service
        providers.MCP_CONFIG_PATH = config

    def do_shutdown(self) -> None:
        # Stops accepting and unlinks the socket; mcp.json stays behind on
        # purpose — the app-id-keyed path is stable across restarts, so a
        # session that outlives this run reconnects to the next one, and
        # until then its shim degrades to clean "Collins is not running"
        # errors rather than breaking the session.
        if self._mcp_service is not None:
            self._mcp_service.stop()
        Adw.Application.do_shutdown(self)

    def _mcp_tool_enabled(self, name: str) -> bool:
        """Whether the user leaves this tool switched on (Preferences →
        Session tools). Read per list and per call, never cached: a session
        keeps the tool list it was handed at startup, so the switch reaching
        a running session at all depends on this being asked again."""
        return bool(self.state.get_setting(mcptools.tool_setting_key(name)))

    def _mcp_tab_for_pid(self, shim_pid: int) -> tuple[MainWindow, TerminalTab] | None:
        """The window and tab whose terminal the calling shim descends from.

        None for anything that wasn't launched from a tab's shell: a
        daemon-hosted background job (ancestry tops out at systemd), a chat
        session, a tab that has since closed. Resolved fresh per call —
        pids recycle, and a walk of every open tab is cheap.
        """
        ancestors = proctree.ancestor_pids(shim_pid)
        for window in self.get_windows():
            if not isinstance(window, MainWindow):
                continue
            for i in range(window.tab_view.get_n_pages()):
                tab = window.tab_view.get_nth_page(i).get_child()
                if isinstance(tab, TerminalTab) and tab.owns_pid_ancestors(ancestors):
                    return window, tab
        return None

    def _mcp_dispatch(self, pid: int, tool: str, args: object) -> tuple[bool, str]:
        """Run one tool call from the shim at *pid*: (ok, message-or-error).

        The validate → switch → identity → handler skeleton is run_tool_call
        (GTK-free, so CI pins its branching); only the widget-touching halves
        live here. Error strings are agent-facing English, untranslated.
        """
        return mcptools.run_tool_call(
            tool,
            args,
            find_tab=lambda: self._mcp_tab_for_pid(pid),
            handlers={
                "set_session_title": self._mcp_set_session_title,
                "open_in_editor": self._mcp_open_in_editor,
                "show_image": self._mcp_show_image,
                "notify_user": self._mcp_notify_user,
                "attach_pr": self._mcp_attach_pr,
            },
            is_enabled=self._mcp_tool_enabled,
        )

    def _mcp_set_session_title(self, found, args: dict) -> tuple[bool, str]:
        window, tab = found
        if not tab.session_id:
            return False, (
                "The session isn't resolved in Collins yet — try again in a moment"
            )
        window.rename_session_tab(tab.session_id, args["title"])
        return True, "Session renamed."

    @staticmethod
    def _mcp_resolve_file(tab, raw: str) -> str | None:
        """The existing file a tool's path argument names, or None.

        Relative paths try the running agent's cwd first (it may have cd'd
        into a worktree), then the tab's project root — the same order
        clickable file references resolve in (terminal._reference_roots).
        """
        expanded = os.path.expanduser(raw)
        if os.path.isabs(expanded):
            trials = [expanded]
        else:
            roots = [tab.current_agent_cwd(), tab.editor_root]
            trials = [os.path.join(root, expanded) for root in roots if root]
        for trial in trials:
            if os.path.isfile(trial):
                return os.path.normpath(trial)
        return None

    def _mcp_open_in_editor(self, found, args: dict) -> tuple[bool, str]:
        window, tab = found
        path = self._mcp_resolve_file(tab, args["path"])
        if path is None:
            return False, f"No such file: {args['path']}"
        if not tab.can_open_in_editor(path):
            return False, "That file is outside this session's project"
        line = args.get("line")
        window.open_in_tab_editor(tab, path, [line - 1, 0] if line else None)
        return True, "Opened in the editor."

    def _mcp_show_image(self, found, args: dict) -> mcptools.ToolResult:
        window, tab = found
        raw = args["path"]
        if remoteimages.looks_remote(raw):
            return self._mcp_show_remote_image(window, tab, raw, args.get("caption"))
        path = self._mcp_resolve_file(tab, raw)
        if path is None:
            return False, f"No such file: {raw}"
        if not editorfiles.is_image_path(path):
            return False, f"Not an image Collins can display: {raw}"
        return self._mcp_present_image(window, tab, path, args.get("caption"), raw)

    def _mcp_present_image(
        self, window, tab, path: str, caption: str | None, origin: str
    ) -> tuple[bool, str]:
        """Float *path* over *tab*'s window, the way a clicked image
        reference does (terminal._present_image): the lightbox shows any
        readable image, project membership only gates its "Open in Editor"
        button. *origin* is what the agent asked for — the file it named, or
        the URL the copy came from — which is what a failed decode names on
        the status page rather than the cache file nobody chose."""
        can_edit = tab.can_open_in_editor(path)
        on_open = (lambda: window.open_in_tab_editor(tab, path)) if can_edit else None
        # The one place every `show_image` passes through, local and remote
        # alike, and the only one that has the agent's own caption in hand.
        # A remote image is written down under its URL, never under *path* —
        # that is the cache copy, and the cache is pruned after a day.
        tab.record_attachment(
            origin if attachrecords.is_remote(origin) else path,
            caption=caption,
            origin=origin,
        )
        present_image_lightbox(
            tab,
            path,
            can_open_in_editor=can_edit,
            on_open_in_editor=on_open,
            caption=caption,
            origin=origin,
        )
        return True, "Image shown."

    def _mcp_show_remote_image(
        self, window, tab, url: str, caption: str | None
    ) -> mcptools.ToolResult:
        """`show_image` given an http(s) URL: fetch it, then show the copy.

        The download runs on a worker thread and the session's reply waits
        for it (mcptools.DeferredResult) — a blocking fetch on the main loop
        would freeze the window for as long as the server felt like taking.
        The thread only fetches; the widget half runs back on the main loop
        (GLib.idle_add), where the tab may by then be gone.
        """
        error = remoteimages.url_error(url)
        if error is not None:
            return False, error
        deferred = mcptools.DeferredResult()
        directory = remoteimages.default_directory()

        def fetched(path: str | None, failure: str | None) -> bool:
            if failure is not None:
                deferred.resolve(False, failure)
            elif tab.get_root() is None:  # the tab closed while we fetched
                deferred.resolve(
                    False, "That session's tab closed before the image arrived"
                )
            else:
                try:
                    deferred.resolve(
                        *self._mcp_present_image(window, tab, path, caption, url)
                    )
                except Exception:  # noqa: BLE001 - the reply must land regardless
                    # An unresolved call is a connection that never speaks
                    # again, so the session would hang on it until its own
                    # timeout; answer, then let the log carry the details.
                    logging.getLogger(__name__).exception("show_image lightbox failed")
                    deferred.resolve(False, f"Collins couldn't show {url}")
            return GLib.SOURCE_REMOVE

        def download() -> None:
            try:
                path = remoteimages.fetch_to_file(url, directory)
            except remoteimages.FetchError as failure:
                GLib.idle_add(fetched, None, str(failure))
            else:
                GLib.idle_add(fetched, str(path), None)

        threading.Thread(target=download, name="show-image-fetch", daemon=True).start()
        return deferred

    def _mcp_notify_user(self, found, args: dict) -> tuple[bool, str]:
        window, tab = found
        if not window.notify_session(tab, args["message"]):
            return False, "Collins couldn't post a notification"
        return True, "The user was notified."

    def _mcp_attach_pr(self, found, args: dict) -> tuple[bool, str]:
        """Put a PR on the calling session's row without a gh call: the
        dispatch runs on the main loop, so the number and repository are read
        off the URL here and the tab's own update thread fetches title and
        status right after. Persistence rides the existing prs-changed path,
        which is keyed by session id — hence the resolution guard."""
        _window, tab = found
        if not tab.session_id:
            return False, (
                "The session isn't resolved in Collins yet — try again in a moment"
            )
        pr = parse_pr_url(args["url"])
        if pr is None:
            return False, f"Not a GitHub pull request URL: {args['url']}"
        if not tab.attach_pr(pr):
            return True, f"{pr.slug} is already attached to this session."
        return True, f"Attached {pr.slug} to this session."

    def _apply_scheme_css(self) -> None:
        """Load the scheme's colors. Runs at startup and on every light/dark
        flip, whether that came from the setting or from the system."""
        dark = Adw.StyleManager.get_default().get_dark()
        colors = {**_MARK_COLORS[dark], **_FILETYPE_COLORS[dark]}
        self._scheme_provider.load_from_data((_SCHEME_CSS % colors).encode())

    @property
    def caffeine_enabled(self) -> bool:
        """Whether Caffeine Mode is on, as the user sees it: an inhibitor
        held, or an Until-idle mode dozing — still armed, just not holding
        the machine while nothing works."""
        return self._caffeine_cookie is not None or self._caffeine_mode is not None

    @property
    def caffeine_remaining(self) -> int | None:
        """Seconds left before Caffeine Mode turns itself off (or, following
        the sessions, dozes off), or None when no countdown is running."""
        if self._caffeine_deadline is None:
            return None
        left = self._caffeine_deadline - GLib.get_monotonic_time()
        return max(0, -(-left // 1_000_000))  # round up, so a 1h timer opens at 1:00:00

    @property
    def caffeine_follows_activity(self) -> bool:
        """Whether Caffeine Mode is running on the sessions rather than the
        clock — what the button's tooltip says instead of a countdown while
        something is still working."""
        return self.caffeine_enabled and follows_activity(self._caffeine_mode or "")

    @property
    def caffeine_dozing(self) -> bool:
        """Whether a sessions-following Caffeine Mode is armed but not
        holding: the grace ran out with everything idle, so the machine is
        free to blank and sleep until a session picks work back up. The
        tooltip's honesty flag — a dozing cup must not promise a lit screen."""
        return self._caffeine_mode is not None and self._caffeine_cookie is None

    def set_caffeine_enabled(self, enabled: bool, duration: str | None = None) -> None:
        """Toggle Caffeine Mode: inhibit suspend and screen blanking app-wide.

        `duration` is a key from caffeine.py: an hours option arms a shut-off
        timer that turns Caffeine Mode off again when it runs out, WHILE_ACTIVE
        ("Until idle" — the default, what a plain click on the button arms)
        follows the sessions instead — holding the machine awake while they
        work, dozing while they rest — and never turns off on its own. Any
        timer already running is cancelled either way, so re-picking a
        duration restarts the clock and none is ever left stale.
        """
        self._cancel_caffeine_timer()
        if enabled != self.caffeine_enabled:
            if enabled:
                self._take_caffeine_inhibit()
            else:
                self._release_caffeine_inhibit()
        # Nothing to count down to if the inhibit didn't take (or was refused).
        if self.caffeine_enabled:
            key = duration or ""
            if follows_activity(key):
                self._caffeine_mode = key
                self._follow_activity()  # arms the grace if nothing is working
                self._caffeine_tick = GLib.timeout_add_seconds(1, self._on_caffeine_tick)
            elif seconds := duration_seconds(key):
                self._caffeine_deadline = GLib.get_monotonic_time() + seconds * 1_000_000
                self._caffeine_tick = GLib.timeout_add_seconds(1, self._on_caffeine_tick)
        self._sync_caffeine_windows()

    def _caffeine_flags(self) -> Gtk.ApplicationInhibitFlags:
        """What Caffeine Mode holds off. SUSPEND always — the machine staying
        awake is the whole point — plus IDLE, which is what also keeps the
        screen lit, only while the user wants the screen kept on."""
        flags = Gtk.ApplicationInhibitFlags.SUSPEND
        if self.state.get_setting("caffeine_keep_screen_on"):
            flags |= Gtk.ApplicationInhibitFlags.IDLE
        return flags

    def _take_caffeine_inhibit(self) -> None:
        """Claim the inhibitor under the current setting, replacing any we
        already hold. The new one is taken before the old is dropped, so the
        machine is never briefly free to sleep in between.

        inhibit() returns 0 when the platform can't inhibit. Turning Caffeine
        Mode on, that has to read as "still off", or every window's toggle
        stays lit over a machine that will happily sleep. Swapping the flags
        under a running Caffeine Mode it must not: the inhibitor we already
        hold is still good, so keep it and leave the setting to land on the
        next attempt rather than letting the screen question turn the whole
        thing off.
        """
        flags = self._caffeine_flags()
        cookie = self.inhibit(self.get_active_window(), flags, _("Caffeine Mode is on")) or None
        if cookie is None:
            return  # refused: whatever we were holding (if anything) stands
        previous = self._caffeine_cookie
        self._caffeine_cookie = cookie
        self._caffeine_flags_held = flags
        if previous is not None:
            self.uninhibit(previous)

    def _release_caffeine_inhibit(self) -> None:
        """Let go of the inhibitor, if one is held. Says nothing about the
        mode: turning Caffeine Mode off comes through here, but so does an
        Until-idle mode starting a doze, which stays armed."""
        if self._caffeine_cookie is None:
            return
        self.uninhibit(self._caffeine_cookie)
        self._caffeine_cookie = None
        self._caffeine_flags_held = None

    def refresh_caffeine_inhibit(self) -> None:
        """Re-take the inhibitor when the keep-the-screen-on setting changed
        under a Caffeine Mode that is already running, so the new answer lands
        without the user toggling the cup off and on. The shut-off timer is
        left alone — only what's being inhibited changes, not for how long.

        Safe to call whenever the setting may have moved: every window is
        resynced either way, since the button's tooltip says what the setting
        promises even while Caffeine Mode is off. A dozing mode holds nothing
        to re-take — the new flags simply apply when work next claims the
        machine.
        """
        if self._caffeine_cookie is not None and self._caffeine_flags() != self._caffeine_flags_held:
            self._take_caffeine_inhibit()
        self._sync_caffeine_windows()

    def _cancel_caffeine_timer(self) -> None:
        if self._caffeine_tick is not None:
            GLib.source_remove(self._caffeine_tick)
        self._caffeine_tick = None
        self._caffeine_deadline = None
        self._caffeine_mode = None

    def _sessions_working(self) -> bool:
        """Whether any window has a session working right now — the same
        answer the sidebar's barber poles give. Only sessions with an open tab
        can say: a detached one has no activity source at all, so a backgrounded
        agent never holds Caffeine Mode on (and never did)."""
        for window in self.get_windows():
            working = getattr(window, "has_working_session", None)
            if working is not None and working():
                return True
        return False

    def _follow_activity(self) -> None:
        """Point the inhibitor at the sessions. `follow_poll` holds the rule;
        this is the poll around it, doing whatever it says: take the machine
        back (work resumed under a doze), let it go (the grace ran out — the
        mode stays armed), or leave the running countdown be.

        Polled from the tick rather than pushed by the windows: the answer is a
        set-emptiness check per window, and reading it live means no missed
        signal — a window closing on a busy tab, a session torn down — can
        leave the machine awake with nothing left to work for, or dozing
        through a session that picked work back up.
        """
        # The grace is read fresh every poll, so a changed setting takes
        # effect the next time the sessions go quiet — no toggle needed. A
        # countdown already running keeps its old deadline (follow_poll never
        # rewrites one), which beats yanking it around under the user.
        grace_s = grace_seconds(self.state.get_setting("caffeine_idle_grace_minutes"))
        deadline, action = follow_poll(
            working=self._sessions_working(),
            holding=self._caffeine_cookie is not None,
            deadline=self._caffeine_deadline,
            now=GLib.get_monotonic_time(),
            grace=grace_s * 1_000_000,  # the poll's clock is µs
        )
        changed = deadline != self._caffeine_deadline
        self._caffeine_deadline = deadline
        if action == "take":
            # A refused take (the platform can't inhibit) leaves the cookie
            # unset, and the next poll simply asks again.
            self._take_caffeine_inhibit()
            changed = True
        elif action == "release":
            self._release_caffeine_inhibit()
            changed = True
        if changed:
            # The countdown appeared, went away, or gave way to a doze — and
            # a doze starting or ending reword the tooltip too; every header
            # needs to hear about it.
            self._sync_caffeine_windows()

    def _on_caffeine_tick(self) -> bool:
        """Once a second while a timer runs: redraw the countdowns, and turn
        Caffeine Mode off when the deadline passes. A sessions-following mode
        never turns off here — the poll swings it between holding and dozing
        until the user ends it, so its tick runs for the mode's whole life."""
        if follows_activity(self._caffeine_mode or ""):
            self._follow_activity()
            if self.caffeine_remaining is not None:
                self._sync_caffeine_windows()  # a grace is counting down
            return GLib.SOURCE_CONTINUE
        if self.caffeine_remaining:
            self._sync_caffeine_windows()
            return GLib.SOURCE_CONTINUE
        # Forget the source before turning off: this callback *is* the source,
        # and returning REMOVE below is what disposes of it.
        self._caffeine_tick = None
        self.set_caffeine_enabled(False)
        return GLib.SOURCE_REMOVE

    def _sync_caffeine_windows(self) -> None:
        for window in self.get_windows():
            sync = getattr(window, "sync_caffeine_toggle", None)
            if sync is not None:
                sync()

    def _new_window(self) -> MainWindow:
        window = MainWindow(application=self, state=self.state, store=self.store)
        window.present()
        return window

    def _main_window(self) -> MainWindow | None:
        """The active window, unless that's a popped-out editor window (or
        some other non-main window): those must never be handed a main
        window's job, so fall back to any main window that exists."""
        window = self.get_active_window()
        if isinstance(window, MainWindow):
            return window
        return next((w for w in self.get_windows() if isinstance(w, MainWindow)), None)

    def _on_focus_session(self, _action, param: GLib.Variant) -> None:
        session_id = param.get_string()
        # The notification came from one specific tab, which may not be in the
        # window that happens to be active — go to the window that has it.
        window = session_window(self, session_id) if session_id else None
        if window is not None:
            window.focus_session(session_id)
            return
        window = self._main_window()
        if window is None:
            return
        window.present()
        if session_id:
            window.focus_session(session_id)

    def do_activate(self) -> None:
        window = self._main_window()
        if window is None:
            window = self._new_window()
            # Fresh launch: reopen the session that was active when the app
            # was last closed. Extra windows (Ctrl+Shift+N) start empty.
            window.restore_last_session()
            if self.state.get_setting("caffeine_on_launch"):
                self.set_caffeine_enabled(
                    True, duration=self.state.get_setting("caffeine_launch_timer") or ""
                )
            # Once per install, and only on a launch: an extra window is not a
            # first impression, and neither is one opened from a notification.
            # The agent CLI comes first and blocks until answered (there is
            # no app without it); the GitHub notice takes its turn after —
            # immediately when the CLI is already in place.
            cliwelcome.maybe_show(
                window,
                self.state,
                self.store,
                then=lambda: ghwelcome.maybe_show(window, self.state),
            )
        window.present()


def main() -> int:
    from . import i18n
    from .state import AppState

    # COLLINS_LOG=INFO (or DEBUG) surfaces diagnostic logs on the console,
    # e.g. bgstatus's watch-dir and refresh activity.
    logging.basicConfig(level=(os.environ.get("COLLINS_LOG") or "WARNING").upper())
    i18n.init(AppState().get_setting("language"))
    app = App()
    return app.run(sys.argv)
