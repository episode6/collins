---
name: collins-composer-and-new-chat
description: >-
  How the prompt composer and the new-chat screen work in Collins: ComposerView
  (composer.py) floating over or docked beside the agent terminal, the
  open-cut that lifts typed text out of the CLI's box and the paste-back on
  close, drafts that survive tab close and quit, typing-opens-composer, the
  key grammar in composerkeys.py, image/file drops and pastes (dropimages.py),
  libspelling, the new-chat screen (newchat.py, newchatview.py) with its
  worktree checkbox and model/effort pickers (modelmenu.py), Draft rows, and
  how Send launches the CLI, the composer's Attach file, and the folder
  choosers (choosers.py) with the path picker over the service's folders
  (pathpicker.py, pathpickermodel.py). Use when changing prompt entry,
  drafts, the first-prompt screen, the model or effort menus, a folder
  chooser, or anything that types text into the agent on the user's behalf.
---

# Composer and the new-chat screen

## ComposerView (`composer.py`)

A host-agnostic widget: an optionally spell-checked multi-line
`GtkSource.View` (font matched to the terminal on purpose) under a button
row — close (floating only: docked, the panel tab's X already closes it, so
`set_docked` hides the chrome's), dock/float, attach, model and effort
pickers, Send. It owns no
terminal plumbing: it emits `send-requested` / `close-requested` /
`dock-toggle-requested` and takes injected callbacks for file references and
notifications. The one live view is either raised over the terminal in a
`Gtk.Revealer` or docked as a `ComposerPage` (`page_kind="composer"`) in the
panel dock — moved by **reparenting**, never rebuilt. Its placement persists
in the panel layout; its text persists as a draft (below).

libspelling is a soft dependency: `Spelling.TextBufferAdapter.new(buffer,
Spelling.Checker.get_default())`, `view.set_extra_menu(adapter.get_menu_
model())`, `insert_action_group("spelling", adapter)`. A right-click doesn't
move the caret in GTK4's text view, and libspelling builds corrections for the
word under the *insertion cursor*, so `composer_spell_click` moves the caret
from a CAPTURE-phase secondary-button gesture (gated on the squiggle tag,
skipped inside a selection) and calls `update_corrections()` synchronously.
A `GtkSource.Buffer` starts on the `classic` light scheme — clear it
(`set_style_scheme(None)`) for prose or dark mode paints black text.

Drops and pastes: a capture-phase `Gtk.DropTarget` on the view (the text
view's own handler would paste `file://` URIs) with `set_gtypes([Gdk.Texture,
Gdk.FileList])` in preference order; pastes hook `paste-clipboard` ahead of
the default handler and decide on `get_formats().union_deserialize_gtypes()`.
Raw images are **uploaded to the service** (PR-2.7: `remoteuploads.upload_png`,
`PUT /api/upload`, named by `dropimages.png_name` with `drop-` / `paste-`
prefixes) and the mention names the path the service answers; dropped files
are mentioned in place (`dropimages.mention_text`) only when the link proved
`local` (`remoteuploads.is_local`), else each is read on a worker
(`Gio.File.load_bytes` after a size check) and uploaded under its basename
(`remoteuploads.upload_files`). The upload lands in
`~/.local/share/collins/uploads/<session id>/` (removed with the session) —
the host's `upload_session()` — or `_pending/` when there is no id yet (the
new-chat screen, a tab that hasn't resolved: D37, swept after a week),
held there to the uploading client's hello `device`
(`uploads.PendingOwners`, PR-3.5: its preview is served back to that device
only, and to no client that is not `local` once the service restarted); the
service uniquifies the name. Every upload and its mention land after the
PUT, at `PRIORITY_DEFAULT`. A preview strip shows image thumbnails, each a
blob (`pictures.fetch` of the path, `kind=file`, the host's
`image_session()` naming the session for D38's confinement) decoded from
the blob cache; the thumb goes up empty and fills, or goes when it won't
decode. Removing one takes its mention (`remove_mention`, refuses on
ambiguity). File-reference chips were deliberately never built.

**Attach file** (the composer's attach button, `TerminalTab.
_pick_file_for_composer`) is a native `Gtk.FileDialog` always (D56: the
file meant is this device's). A `local` client's starts in the agent's cwd
(set only after `apilink.is_local()`) and mentions the picked path; any
other's starts where the desktop puts it, uploads the pick with
`remoteuploads.upload_files` exactly as a drop (session id or `_pending/`)
and mentions the copy (`_mention_attached`, into whichever composer
`_attach_composer` still finds: the new-chat screen's or the open one; a
composer gone by then drops it). `check_local_extras.py` drives both by
handing `_on_composer_file_chosen` a stand-in dialog.

## Choosing a folder (`choosers.py`, `pathpicker.py`, `pathpickermodel.py`)

The seven sites that pick a folder the service will use (window's new
session, *Add project*, *Continue*, new chat; the clone dialog's and
Preferences' clone destination; the sandbox chip's *Allow a directory…*)
call `choosers.choose_folder(parent, title, initial, on_done)`:
`Gtk.FileDialog.select_folder` for a `local` client (the starting folder's
`isdir` behind the ask: one `LOCAL_EXTRAS` site), `pathpicker.pick_folder`
otherwise. *on_done(path)* gets a path of the service's machine, a
dismissal calls nothing. The picker (`PathPicker`, an `Adw.Dialog`, 640 x
520, never resizable) browses the service's whole filesystem over its own
two unconfined requests (D60): `fs.dirs {path, hidden}` for the listing
(`remotefiles.list_dirs`) and `fs.newdir {path}` for *New folder*
(`remotefiles.new_dir`); never the editor's confined `fs.list` / `fs.mkdir`
(a non-local client is refused `outside` everything that is no known
root there). `~` is sent as typed and expanded by the service; the entry
then shows the folder as the service listed it. Typing re-lists after
`LIST_DEBOUNCE_MS` (300) on a `GLib.timeout_add`; a click, a shortcut,
*Up*, *Show hidden* (a toggle, off per open, never saved) list at once;
`Listing` numbers the asks and drops a stale answer; Tab completes against
the rows of the folder typed (asking first when they are another
folder's). A link row carries `pathpicker.LINK_ICON`,
`collins-symlink-symbolic`, a filled-path icon of Collins' own (the tree
has no link marker to borrow, and the desktop's
`emblem-symbolic-link-symbolic` is missing from Adwaita, which draws the
broken-image glyph for it), and opens like any folder; a cut listing
ends in an inert row; a refusal's words replace the list. *Open* is the
service's `fs.stat` of the entry's text and hands on the path it echoes
(`~` expanded, normalised, never resolved). Shortcuts: the service's home
(`apilink.service_home`) and the store's `proj` rows' project roots. All
requests through `remotefiles.off_main`, landing at `PRIORITY_DEFAULT`.
`pathpickermodel` is GTK-free (`tests/test_pathpicker.py`);
`check_path_picker.py` walks it with the client not local at both ends
(`e2e_service.drop_local`).

## The cut and the paste-back (`TerminalTab`)

Opening the composer over a running agent **cuts** whatever is typed in the
CLI's box into the composer (so a later send can't double it). The cut is a
chain of screen reads: four identical reads 50 ms apart before erasing (a box
still moving after 12 tries is left alone — erasing a stale read deletes
characters the CLI hasn't echoed yet, unrecoverably), then re-reads at
150/400/900 ms that erase any leftover which is a *prefix* of what was read.
The CLI finishes echoing a burst 30–100 ms after the last key; a trailing
space, a wrap read as a split token, or a space before a typed break each
leave exactly that many leading characters behind — the signature is "the
first character appears twice". A send during the settle is held and released
by `_end_settling`; `_cut_seq` invalidates in-flight rounds when anything else
writes the box. A box holding a paste the CLI folded (`[Pasted text #N]`) is
refused (`_foreign_paste_in_box`) — cutting it would cost the paste.

Closing (Ctrl+. or the X) **types the draft back** into the CLI's box in
pieces the CLI won't fold (`composerkeys.paste_pieces`: bracketed pastes of
≤320 characters (`_PIECE_CHARS`, a fifth under the CLI's 800-char fold) and ≤2
newlines each), verifies what landed
(`pasted_back` / `expand_pasted_back`), and stashes what it couldn't hand
back (`stashable_draft`), to be restored on the next open
(`draft_to_restore`). Sending cuts nothing and submits via `inject_prompt`
semantics (text, then `\r` a beat later; multi-line via bracketed paste).

**Typing opens the composer** (`composer_on_typing`): a printable key at an
*empty* box raises the composer with that character
(`composerkeys.typing_opens_composer`; `/`, `!`, `#`, `@` keep their CLI
meaning). Only an empty box — a permission dialog, a menu, a half-written line
keep their keys; an agent mid-turn has an empty box and *does* open, since
composing over a working agent is the point. (The old
`composer_new_sessions` auto-show setting is gone — the new-chat screen is
where a first prompt is written — and `AppState._load` drops a saved copy.)

**Drafts.** `capture_composer_draft` / `restore_composer_draft` round-trip the
text through `AppState.set_session_draft` on tab close and quit; a floating
composer's draft is stashed, a docked one's saved with its layout. The key
grammar (`enter_action`: Enter sends unless `composer_enter_sends` is off,
Shift+Enter is always a newline because it is the terminal's literal-newline
chord, Ctrl+Enter the alternate send) is integer-keyval GTK-free code.

**With no `composer` family** (server-side spec D5) there is no cut, and so no
promise the box is empty. `ClientSession.send_composed` sends through `prompt
{when_empty: true, focus: false}` (a bracketed paste: a composed prompt is free
text) and `on_refused` toasts when the box holds something, the draft staying
put. `switch_model` / `switch_effort` type the provider's command through
`prompt {when_empty: true, focus: true}`, the road `switch` itself takes
(`Session.post_prompt`: the text, then the Return alone a beat later, which is
what makes the CLI read a command). `restore_draft` is False, so a closed draft
stays in the stash; `mention` builds the reference here and goes through
`write`; typing does not open the composer; `Preferences`' switch for it is
gone.

**With no `files` family** (ruling 20) `checkouts.ask_answer` answers False: no
box is shown, nothing was promised, and the launch is plain, where None would
refuse every Send with a worktree wish. A client that is not `local` has no picker
either, so `choosers.choose_folder` opens one box for a path on the service's
machine (`pathpicker.type_folder`, prefilled with `service.status`'s `home`, ruling
26): it sends no `fs.*`, every site stays where it is, and the service judges the
path (`spawn` falls back and says where it landed, `store.add-project` refuses).

## The new-chat screen (`newchat.py`, `newchatview.py`)

Beside the worktree box sits a **Sandboxed** one on the same terms
(`sandbox_choice` / `set_sandbox_choice`, `newchat.effective_sandbox`, the
draft record's `sandbox` slot), visible only when the probe's cached verdict
says a box can be built (`set_sandbox_available` puts it on a screen built
before the verdict landed). One difference: a draft's restored choice is
kept while the box is hidden — reopened before the verdict, or on a machine
with no box — so the late verdict can't swap in the project's default and a
re-saved draft doesn't drop it; `sandbox()` and `effective_sandbox` read a
hidden box as off;
`send-requested` and the tab's `new-chat-send` carry it as a second bool
(`text, worktree, sandbox, model, effort`). See `collins-sandboxed-sessions`.

A hand-started session opens onto a screen, not the console: the project's
icon and name over the same `ComposerView`, with a **New git worktree**
checkbox and the model and effort pickers in the Send row. Nothing is spawned
until Send (`TerminalTab.begin_session`), which spawns the CLI with `--model`
/ `--effort` only when picked (a pick is for this launch alone; nothing writes
the user's default) and `-w` per `newchat.effective_worktree(choice,
project_default, is_git)` (whether the folder is a checkout is the
service's answer, `checkouts.ask`: the box is hidden until it lands, a Send
before then carries the box's wish, `newchat.worktree_wish`, and the window
holds the launch for the answer with the screen insensitive; a wish the
service could not answer launches nothing, `newchat.send_outcome`; see
`collins-terminal-tab`), then polls `takes_prompt` (`Session._new_chat_prompt_tick`,
~90 s budget, ~6 s idle-shell cutoff) and types the prompt. With nothing
typed the button reads **Empty Session** and starts the agent bare. Until
Send the tab is a **draft**: text or a terminal opened beside it
(`draft_worthy`) is written to `state.new_chat_drafts` under a `draft-` id
(`draft_record` / `valid_draft`), shown as a sidebar Draft row (`draft_label`)
that comes back with text, checkbox, picks and dock. The draft id doubles as
the tab's sidebar placeholder id. The row's trailing button on a live draft
closes the tab **and forgets the draft** (confirming when there is text).
`start_session` (MCP) spawns bypass the screen; a caller's model seeds the
picker.

The pickers (`modelmenu.new_launch_model_popover` /
`new_launch_effort_popover`) open pre-marked on the CLI's own default —
`claudemodels.cli_default_model(cwd)` walks `ANTHROPIC_MODEL` → managed
settings → `<cwd>/.claude/settings.local.json` → `.claude/settings.json` →
`~/.claude/settings.json`; `cli_default_effort(cwd, model)` walks the same
chain but **per model** (`modelSettings.<id>.effortLevel` beats a file's
top-level `effortLevel`, which `/effort` never clears). Picking a model
clears the effort pick so the dial reads the new model's default. Levels a
model can't take (catalog `capabilities.effort`) draw insensitive. The
running session's menus (`new_model_popover` / `new_effort_popover`) mark the
transcript's current model/effort and post `/model` / `/effort` instead; the
mark moves when the transcript shows the CLI confirming the switch
(`TranscriptModel._record_switch`, see `collins-terminal-tab`), never on the
pick itself.
Every one of these popovers must be hosted by a `Gtk.MenuButton`: a
hand-parented `Gtk.PopoverMenu` filled on `show` measures once against an
empty menu and locks tiny.

## Footguns

- `send-requested` grew arguments (text, worktree, model, effort); e2e checks
  emit it by hand — grep `scripts/check_*.py` when changing the signature.
- Never hand the composer a `feed_child_text` of the raw draft on close; the
  CLI folds >800 chars / >2 breaks into a stand-in, which is why
  `paste_pieces` exists.
- The composer autoshow polls for a *running agent*; a shell at its prompt
  (the command exited) must stop the poll, not wait it out.
- Clipboard claims from a headless script are dropped until the compositor
  hands over focus; retry until `is_local()`.
- Drafts are kept even when `state.json` is rewritten by an older build:
  `valid_draft` is what trusts a record back off disk — extend it when adding
  a field.

Related: `collins-terminal-tab`, `collins-panel-dock`,
`collins-token-use-and-claude-api` (the model catalog).
