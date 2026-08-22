<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-21. Full change history: git log for this file.
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
| `Ctrl+Shift+G` | Find in the terminal |
| `Ctrl+K` | Quick switcher — jump to any session |
| `Ctrl+Shift+A` | Archive the current session (closes its tab) |
| `Ctrl+Shift+Z` | Undo the last archive (until another session is archived) |
| `Ctrl+Shift+E` | Toggle a 😊 marker on the current tab |
| `Shift+Enter` | Insert a newline in the agent's prompt |
| `Ctrl+J` | Show/hide the terminal panel |
| `Ctrl+Shift+K` | Clear the terminal panel (screen and saved history) |
| `Ctrl+;` | Move the current panel tab to the panel's other side (bottom ↔ right) — the same thing as its tab row's rotate button |
| `Esc` | Bring a panel tab that's overlaying the whole session back to its place in the panel (a shell with a program running in it keeps the key) |
| `Ctrl+.` | Show/hide the composer — raised, the cursor lands in it; pressed again while composing it closes and puts the draft back in the agent's own input box |
| `Ctrl+'` | Show/hide the attachments gallery — the images this session has been shown, the same panel the handle on the terminal's right edge raises; docked as a panel tab it comes to the front (revealing a hidden strip) instead of closing |
| `F7` | Open the pull request page for the newest PR this session is linked to — already open, it comes to the front and re-reads itself |
| `F8` | Show/hide the editor panel (brings a popped-out editor back first) |
| `Ctrl+Shift+O` | Quick open — fuzzy-find a file in the project, opened in the editor |
| `Ctrl+S` (in the editor) | Save the current file |
| `Ctrl+F` (in the editor) | Find in the current file |
| `F9` | Toggle the sidebar |
| `Ctrl+,` | Preferences |

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
in the dialog's header does the same for all of them. Rows sharing a chord
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
