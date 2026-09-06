<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-09-06. Full change history: git log for this file.
-->

# Keyboard Shortcuts

These are the defaults. Every one of them can be changed — see
[Customizing](#customizing) below.

| Shortcut | Action |
| --- | --- |
| `Ctrl+Shift+T` | New session |
| `Ctrl+Shift+N` | New window |
| `Ctrl+W` | Close the panel tab you were last in — and once no panel is left open, the session tab itself |
| `Ctrl+PgUp` / `Ctrl+PgDn` | Previous / next tab |
| `Ctrl+C` / `Ctrl+V` | Copy selection / paste (easy copy & paste, on by default) |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | Copy / paste in the terminal (always available) |
| `Ctrl+Shift+G` | Find in the agent's terminal (the `Ctrl+J` shell has no find bar) |
| `Ctrl++` (or `Ctrl+=`) / `Ctrl+-` / `Ctrl+0` | Zoom the terminal in / out / back to normal (the keypad's `+`, `-` and `0` work too) |
| `Ctrl+K` | Quick switcher — jump to any session |
| `Ctrl+Shift+A` | Archive the current session (closes its tab) |
| `Ctrl+Shift+Z` | Undo the last archive (until another session is archived) |
| `Ctrl+Shift+E` | Toggle a 😊 marker on the current tab |
| `Shift+Enter` | Insert a newline in the agent's prompt (the keypad's Enter counts) |
| `Ctrl+J` | Show/hide the terminal panel |
| `Ctrl+Shift+K` | Clear the terminal panel (screen and saved history) |
| `Ctrl+;` | Move the current panel tab to the panel's other side (bottom ↔ right) — the same thing as its tab row's rotate button |
| `Esc` | Bring a panel tab that's overlaying the whole session back to its place in the panel (a shell with a program running in it keeps the key) |
| `Ctrl+.` | Show/hide the composer — raised, the cursor lands in it; pressed again while composing it closes and puts the draft back in the agent's own input box |
| `Ctrl+'` | Show/hide the attachments gallery — the images this session has been shown, the same panel the handle on the terminal's right edge raises; docked as a panel tab it comes to the front (revealing a hidden strip) instead of closing |
| `F6` | Show/hide the git page — the diff beside the session showing the working tree, the index, or the branch against its parent; pressed while the cursor is in it, it closes |
| `F7` | Open the pull request page for the newest PR this session is linked to — already open, it comes to the front and re-reads itself |
| `F8` | Show/hide the editor panel (brings a popped-out editor back first) |
| `Ctrl+Shift+O` | Quick open — fuzzy-find a file in the project, opened in the editor |
| `Ctrl+S` (in the editor) | Save the current file |
| `Ctrl+F` (in the editor) | Find in the current file |
| `Ctrl+F` (in the git page) | Find in the diff — one query over every hunk; `Enter` / `Shift+Enter` walk the matches across hunks and files |
| `F9` | Toggle the sidebar |
| `Ctrl+Shift+B` | Show/hide the notification history — the sheet the header bell opens; `Esc` closes it too |
| `Ctrl+,` | Preferences |
| `Ctrl+Q` | Quit |

In the close-tab and close-window confirmation dialogs (shown when a session
is still active), a single keypress answers the dialog:

| Key | Action |
| --- | --- |
| `E` | Exit the session(s) |
| `B` | Background the session(s) (when available) |
| `K` | Keep running — hide the window, sessions untouched (close-window dialog) |
| `C` (or `Esc`) | Cancel |

::: tip Easy copy & paste
With **easy copy & paste** on (the default, see Preferences), `Ctrl+C` copies
when text is selected — otherwise it interrupts as usual — and `Ctrl+V`
pastes.
:::

Note that `Ctrl+K` opens the quick switcher app-wide, so it takes priority
over the shell's own kill-line binding inside terminals.

## Customizing

The sidebar's menu (☰) → **Keyboard Bindings** lists every shortcut above,
grouped by what it acts on. Click a row and press the new key combination:
it takes effect immediately, in every open window and tab. In the capture
dialog, `Backspace` on its own removes the binding (the action keeps working
from its menus and buttons, it just has no key) and `Esc` keeps the current
one. A chord that another action already holds is offered back: confirm and
it moves, leaving the other action without it.

Each changed row shows a reset arrow that puts its default back; **Reset All**
in the dialog's header does the same for all of them, after asking. It's
greyed out while nothing is customized. Rows sharing a chord
carry a warning mark naming the other action — every scope is checked
against every other, because the window's shortcuts win over the editor's
and the terminal's.

A few actions ship unbound, for anyone who wants a key for them: search
sessions (`win.focus-search`), swap the panel's sides, move a panel tab to
the other strip, focus the editor, and opening the Keyboard Bindings dialog
itself.

The bindings are stored in `~/.config/collins/state.json` under
`settings.keybindings`, as a map of action name to a list of GTK accelerator
strings (`"win.close-tab": ["<Control>F4"]`; an empty list means unbound).

## Keys that aren't rebindable

These belong to the widget under the cursor, so they don't appear in the
Keyboard Bindings dialog:

| Key | Where | Action |
| --- | --- | --- |
| `Enter` / `Ctrl+Enter` | Composer, new-chat screen | Send / newline — swapped by the *Enter sends composer text* preference. `Shift+Enter` is always a newline |
| `↑` / `↓`, `Enter`, `Esc` | Quick switcher, quick open | Move the selection, open it, close |
| `Esc`, arrows, `Tab`, `Enter` / `Space` | Lightbox | Close, walk the gallery, cycle the shade's buttons, activate the focused one — nothing reaches the terminal underneath |
| `Ctrl+Enter` | Pull request comment & review boxes | Post, as on GitHub; a bare `Enter` stays a newline |
| `Ctrl+1` / `Ctrl+2` / `Ctrl+3` | Git page | Load the unstaged changes, the staged changes, or the branch's diff against its parent |
| `↑` / `↓`, `Enter` | Git page (commits and files lists) | Walk the rows, load the commit or branch (or reveal the file in the diff) — the sidebar's lists are ordinary GTK lists |
| `Enter`, `Shift+Enter` | Git page commit dialog | Commit; a newline in the body |
| `]` / `[`, `.` / `,` | Git page (diff) | Focus the next / previous hunk, the next / previous file — scrolled into view, the files list following. This row and the ones below are the **Git page** group of the Keyboard Bindings dialog: bare letters bound to the diff alone (they never reach the agent's terminal), rebindable there like any other |
| `j` / `k` | Git page (diff) | Move the cursor line down / up in the focused hunk, the page scrolling to keep it on screen; past the hunk's last (first) line the keyboard walks on into the next (previous) hunk |
| `}` / `{` | Git page (diff) | The next / previous hunk carrying a note or highlight |
| `c`, `E`, `a` | Git page (diff) | Add a note under the focused hunk, anchored to the cursor line (`Ctrl+Enter` saves, `Esc` cancels; while the editor is open the other letters type into it); edit the hunk's first note of yours; show or fold the agent's notes |
| `z` | Git page (diff) | Draw every unchanged line above the focused hunk |
| `x` / `X`, `D`, `Esc` | Git page (diff) | Stage, unstage or revert — by the load — the selected lines or, with none, the focused hunk; the same for the whole file; discard (revert, on a commit or branch) the selection or hunk after a confirmation whose default is *Cancel* — `Esc` or `Enter` there leaves the tree alone; clear the line selection (made by dragging in the text or on the line numbers, or with `Shift`+arrows). The hunk and file headers' buttons press the same; a binary has no hunk and goes whole from its file header, never reverted |
| `0` / `1` / `2`, `l`, `w` | Git page (diff) | Layout automatic / split / stacked, line numbers on or off, wrap long lines — each writes the Preferences → Git setting, so every page follows |
| `r`, `/`, `?`, `e`, `q` | Git page (diff) | Reload the diff; focus the files filter (`Esc` there clears it and comes back); open the Keyboard Bindings dialog on its Git page group; open the file under the cursor in the editor, at that line; close the page |
| `←` / `→` | Sidebar project header | Collapse / expand the group |
| `Esc` | Notification sheet, attachments overlay, Open with picker | Close |
