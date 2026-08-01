<!--
Modified from the original agent-session-manager
(https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
fork. Last modified: 2026-08-01. Full change history: git log for this file.
-->

# Keyboard Shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+Shift+F` | Open the sidebar search (`Esc` closes it) |
| `Ctrl+Shift+T` | New session |
| `Ctrl+Shift+N` | New window |
| `Ctrl+W` | Close the current tab |
| `Ctrl+PgUp` / `Ctrl+PgDn` | Previous / next tab |
| `Ctrl+C` / `Ctrl+V` | Copy selection / paste (easy copy & paste, on by default) |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | Copy / paste in the terminal (always available) |
| `Ctrl+Shift+G` | Find in the terminal |
| `Ctrl+Shift+K` | Quick switcher — jump to any session |
| `Ctrl+Shift+A` | Archive the current session (closes its tab) |
| `Ctrl+Shift+E` | Toggle a 😊 marker on the current tab |
| `Shift+Enter` | Insert a newline in the agent's prompt |
| `Ctrl+J` | Show/hide the terminal panel |
| `Ctrl+K` | Clear the terminal panel (screen and saved history) |
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
| `C` (or `Esc`) | Cancel |

::: tip Easy copy & paste
With **easy copy & paste** on (the default, see Preferences), `Ctrl+C` copies
when text is selected — otherwise it interrupts as usual — and `Ctrl+V`
pastes.
:::

Note that `Ctrl+K` clears the *panel* terminal app-wide, so it takes priority
over the shell's own kill-line binding inside terminals.
