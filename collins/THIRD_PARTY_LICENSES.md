# Third-party license notices

Collins is free software licensed under the GNU General Public License v3.0 or later
([LICENSE](https://github.com/episode6/collins/blob/main/LICENSE)). It is a fork of, and
is built with, the third-party components below. The app ships this document and shows it
on the **Legal** page of its About dialog.

## Upstream project

Collins is a fork of **agent-session-manager** — © Máté Molnár —
[r4nd3l/agent-session-manager](https://github.com/r4nd3l/agent-session-manager) — used
under the **GNU General Public License v3.0**. Files inherited from it carry an in-file
modification notice, and comment-less files that changed are listed in `NOTICE` at the
repo root. The interface translations under `po/` also originate there.

## GNOME platform libraries

Collins bundles none of these — they are loaded at runtime from your system packages, and
you are free to replace them with your own builds:

- **GTK 4** — © The GTK Team — LGPL-2.1-or-later — [gtk.org](https://www.gtk.org)
- **libadwaita** — © GNOME contributors — LGPL-2.1-or-later —
  [GNOME/libadwaita](https://gitlab.gnome.org/GNOME/libadwaita)
- **VTE** (`vte-2.91-gtk4`, the embedded terminal widget) — © the VTE authors —
  LGPL-3.0-or-later — [GNOME/vte](https://gitlab.gnome.org/GNOME/vte)
- **GLib / GObject / GIO**, **Pango** and **gdk-pixbuf** — © the GNOME Project —
  LGPL-2.1-or-later — [gtk.org](https://www.gtk.org)
- **cairo** — © the cairo authors — LGPL-2.1 or MPL-1.1 —
  [cairographics.org](https://www.cairographics.org)
- **PyGObject**, the Python bindings for all of the above — © PyGObject contributors —
  LGPL-2.1-or-later — [pygobject.gnome.org](https://pygobject.gnome.org)
- **Python** and its standard library — © the Python Software Foundation — PSF License
  Agreement — [python.org](https://www.python.org)

## Icons

Most icons are named icons resolved at runtime from the system icon theme, typically the
**Adwaita icon theme** — © the GNOME Project — CC-BY-SA-3.0 or LGPL-3.0 —
[GNOME/adwaita-icon-theme](https://gitlab.gnome.org/GNOME/adwaita-icon-theme). The
symbolic icons bundled under `data/icons` are either original artwork for this fork or
derived from agent-session-manager (GPL-3.0), except the ones used unmodified from
**Octicons** — © GitHub, Inc. — MIT License —
[primer/octicons](https://github.com/primer/octicons): `alert-symbolic`,
`alert-fill-symbolic`, `check-circle-fill-symbolic`, `x-circle-fill-symbolic`,
`git-merge-symbolic`, `git-pull-request-symbolic`,
`git-pull-request-draft-symbolic`, `git-pull-request-closed-symbolic` and
`github-symbolic`, so that a pull request's state and status marks — merged, open,
draft or closed, with the check, warning or error riding its corner — read the same
here as on the site they came from; and the file tree's
file-type set, `ft-*-symbolic` (each SVG names its source Octicon in its header
comment). The Collins app icon is original artwork.

## Terminal color schemes

The built-in terminal palettes reproduce color values from the schemes below. No code is
taken from any of them — only the published colors.

- **Solarized** — © Ethan Schoonover — MIT License —
  [altercation/solarized](https://github.com/altercation/solarized)
- **Dracula** — © Dracula Theme — MIT License — [draculatheme.com](https://draculatheme.com)
- **Gruvbox** — © Pavel Pertsev — MIT License —
  [morhetz/gruvbox](https://github.com/morhetz/gruvbox)
- **Nord** — © Sven Greb and the Nord contributors — MIT License —
  [nordtheme.com](https://www.nordtheme.com)
- **Catppuccin** — © Catppuccin — MIT License — [catppuccin.com](https://catppuccin.com)
- **Tokyo Night** — © enkia — MIT License —
  [enkia/tokyo-night-vscode-theme](https://github.com/enkia/tokyo-night-vscode-theme)
- **One Dark** — © GitHub, Inc., from Atom's One Dark syntax theme — MIT License —
  [atom/atom](https://github.com/atom/atom)
- **Tango** — the Tango Desktop Project palette, published for unrestricted use —
  [Tango Desktop Project](https://en.wikipedia.org/wiki/Tango_Desktop_Project)
- **Monokai** — the palette popularized by Wimer Hazenberg's Monokai theme; its color
  values are widely reproduced and no separate license is claimed over them.

## Claude Code

Collins drives the **Claude Code** CLI, which it neither bundles nor redistributes: it
runs whatever `claude` it finds on your `PATH`, under Anthropic's own terms
([claude.com/claude-code](https://claude.com/claude-code)). Claude, Claude Code and
Anthropic are trademarks of Anthropic PBC. Collins is an unofficial community tool, not
affiliated with or endorsed by Anthropic.

## Documentation site

The docs under `docs/` are built with **VitePress** — © Yuxi (Evan) You and VitePress
contributors — MIT License — [vitepress.dev](https://vitepress.dev). It is a
development-time dependency only, and is not part of the installed application.
