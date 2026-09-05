---
name: collins-preferences-keybindings-i18n
description: >-
  How to add or change a setting, a keyboard shortcut, a terminal theme or a
  translated string in Collins: state.DEFAULT_SETTINGS and AppState, the
  Preferences dialog (prefs.py) with its own search (prefssearch.py) and the
  group/row order pinned by prefslayout.py, the keybindings catalogue
  (keybindings.py) and its GTK realization (keymap.py, keybindingsdialog.py),
  VTE color themes (themes.py), and gettext via i18n.py with hand-written
  translations in po/generate.py. Use whenever touching Preferences, a
  setting's default or migration, a shortcut, the terminal palette, or any
  user-visible string.
---

# Preferences, keybindings, themes, translations

## Settings

`state.DEFAULT_SETTINGS` (in `state.py`) is the catalogue: every key with a
comment saying what it means and where it is read. `AppState.get_setting(key)`
falls back to the default; `set_setting` / `update_settings` save at once
(synchronous, atomic). Save writes every default back, so after a first save
a new key exists in every install. Migrations live in `AppState._load`
(old key spellings, `auto_title_sessions` → `title_model`, the legacy
`panel_states` shape) — one-shot, on read. Tests use the `app_state`
fixture, which isolates the config dir.

To add a setting:

1. The `DEFAULT_SETTINGS` entry with its comment.
2. A row in `prefs.PreferencesDialog` in the right `_build_*_group`; groups
   are `_SearchableGroup`s built in `prefslayout.GROUPS` order (the Token use
   group sits directly under General, and `TOKEN_USE_ROWS` pins its rows —
   `tests/test_prefslayout.py` fails if either drifts). Rows write the setting
   the moment they change and call `on_change` (Escape after a toggle loses
   nothing); `MainWindow.apply_preferences` fans the new settings out to tabs
   (`TerminalTab.apply_settings` → every `PanelPage.apply_settings`).
3. Search words: the dialog's search is Collins' own (`prefssearch.matches`,
   word-wise, unanchored), reading each row's title/subtitle plus the
   `search_terms` attribute `_searchable()` sets. Anything the search can't
   read — options inside an expander, a group's header-suffix button — needs
   its words smuggled in (`_group_text`, `prefslayout.NOTIFICATION_SEARCH_
   TERMS` / `GIT_SEARCH_TERMS`).
4. Docs: `docs/guide/features.md` (and the README bullet if it changes the
   feature list).

Dialog facts: an `Adw.Dialog` + `Adw.ToolbarView` with the search bar as a
second top bar (libadwaita's built-in search matches title+subtitle only and
pushes a separate page); an `Adw.Dialog` hands focus to its first focusable
widget on open, so the search bar translates Escape into clear-then-close; a
filtering dialog needs an explicit `content_width` / `content_height`
(dialogs are never user-resizable anyway). `Adw.PreferencesGroup.add()` puts
a non-row child in a box *below* the list — a clean caption slot — but add it
via the base class, never `_SearchableGroup.add` (the filter calls
`get_title()` on `group.rows`). `Adw.ComboRow` values get ~130 px: short
labels, explanation in the subtitle (a two-line subtitle squeezes the value
to ~40 px). `Adw.ComboRow` section headers need libadwaita 1.6 (floor is
1.5) — lists stay flat. Behaviour settings (`archive_running_session`,
`quit_with_running_sessions`) are enums `ask | exit | background` (+ `hide`
for quit). The status-icon row watches host availability live
(`_on_status_icon_host`).

Group descriptions are rare on purpose: the audience is Linux developers, so
titles and one-line subtitles carry the meaning; a description only for a
non-obvious caveat ("Restart to apply").

## Keybindings

`keybindings.BINDINGS` (GTK-free) is the one list of shortcuts: `Binding(
action, label, defaults, group, note)` with actions in GTK's `prefix.name`
form (`win.*`, `app.*`, `editor.*`, `terminal.*`) and accelerators in GTK
syntax (`<Control><Shift>t`). User overrides live in the `keybindings`
setting (`{action: [accels]}`; empty list = unbound, absent = default);
`resolve` folds them. `keymap.py` turns the catalogue into a
`Gtk.ShortcutController` for the window (CAPTURE phase, so the CLI never sees
a claimed chord) and the editor, `set_accels_for_action` for `app.*`, and a
`KeyMatcher` the terminals' hand-rolled key handlers consult (copy only fires
with a selection; the newline chord feeds bytes — neither can be a
`Gtk.Shortcut`). `keybindingsdialog.py` rebinds by capturing a chord (window
shortcuts suspended meanwhile) and saves; the window rebuilds controllers
(`reinstall_shortcuts`). Tooltips quote chords via `keybindings.with_hint`.

Rules: a new shortcut is a `BINDINGS` entry, never a hard-coded chord. A
shortcut that must never reach the terminal keeps its action **enabled** and
no-ops internally — a disabled `NamedAction` lets the key fall through to the
VTE. Compare accelerators with `keybindings.canonical()`; GTK spells
modifiers `<Shift><Control>n`. Keycaps in the dialog need a parent with
`set_css_name("shortcut")` for Adwaita's `.keycap` rule. Free chords: the
CLI's readline owns nearly every Ctrl+letter (`Ctrl+M/I/H` are Enter/Tab/
Backspace; `Ctrl+[` ESC, `Ctrl+\` SIGQUIT, `Ctrl+/` undo, `Ctrl+Enter` is a
plain `\r`); single-modifier punctuation is exhausted (`, . ; '`); function
keys (F10–F12 free) and Alt+letter (VTE turns Alt into an ESC prefix; readline
binds some) remain. `win.swap-panel` and `win.move-panel-page` are registered
but unbound by default for custom bindings.

## Themes (`themes.py`)

`_THEMES` maps names to VTE palettes (Dracula, Solarized, Gruvbox, Nord,
Catppuccin, Tokyo Night, Monokai, One Dark, …; `Default` = VTE's own, which
is 75% grey on black under **both** light and dark — `set_default_colors`
does not follow Adwaita). `apply_terminal_theme` sets VTE colors and
`_apply_dynamic_theme_css` restyles everything that tracks the terminal
(`.terminal-gutter`, selected tab, composer and attachments panels, the
attach overlay) so there is no seam; for Default it reads the bg off the
widget (`get_color_background_for_draw`, the only getter) and pairs it with
`_VTE_DEFAULT_FG`. `terminal_foreground(name)` is what `vtehtml` divides by
to recognise dim text, and returns None for Default on purpose.
Theme-following colors belong in this dynamic provider; static CSS in
`app.py`'s `_CSS` is a **bytes literal — ASCII only** in comments.
`color_scheme` (system/light/dark) drives `Adw.StyleManager`.

## Translations (`i18n.py`, `po/`)

`i18n.init(language)` once at startup (the `language` setting, `""` =
system), then `_()` everywhere; `N_()` marks strings translated later (menu
labels in catalogues). Languages: en, hu, de, es, fr. Translations are
**hand-written dicts** in `po/generate.py` (`TRANSLATIONS[lang][msgid]`);
`python3 po/generate.py` rewrites `po/*.po` and compiles
`collins/locale/*/LC_MESSAGES/collins.mo` (commit all of them). The writer
emits only `msgid`/`msgstr` — **no plurals**, so `i18n.ngettext` always falls
back to English; write count strings as one `_()` form ("… ({n} new)").
Changing an existing msgid means changing that key in all four dicts.
`po/collins.pot` is regenerated only at release cuts (`xgettext
--language=Python --from-code=UTF-8 -k_ -kN_ -kngettext:1,2` over
`collins/**/*.py`) together with a full translation refresh; between cuts new
strings falling back to English is expected and accepted. The desktop entry's
`GenericName[xx]` / `Comment[xx]` / `Keywords[xx]` and the metainfo's
`<summary xml:lang>` are translated by hand and invisible to xgettext. Agent-
facing strings (MCP tool descriptions and errors, `hunkctl` reply text) are
deliberately untranslated English. `ruff` covers `collins/` and `tests/`
only; `generate.py`'s long lines are expected.

## Footguns

- `AppState.save()` persists every default; a test asserting on a "missing"
  key after a save is wrong.
- Preferences' CLI row and the welcome dialog share `welcome.MARKS` /
  `reason_for` so both judge paths identically.
- A `Gtk.Settings` change (`gtk-label-select-on-focus` off) lives in
  `app.apply_gtk_settings`; the effort/model pickers and every other
  `Gio.Menu` popover must be hosted by a `Gtk.MenuButton` (a hand-parented one
  measures once, empty).
- The four `.mo` files are pre-fork binaries: their GPL notice dates live in
  the top-level `NOTICE`.

Related: `collins-token-use-and-claude-api` (Token use rows),
`collins-gtk-sharp-edges`, `collins-terminal-tab` (terminal key handling).
