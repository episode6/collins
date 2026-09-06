#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Headless probe of the native diff view (collins/diffview.py): a real
`git show <ref>` (or the working tree) of a repository, drawn by a DiffView
in a window nobody sees, rendered to a PNG, and poked.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/probe_diffview.py [--repo DIR] [--ref HEAD | --unstaged | --staged]
            [--layout auto|split|stack] [--wrap] [--width 1400] [--out out.png]

Not an e2e check (scripts/run_e2e.py only discovers check_*.py): a look at
the output while the view is built, and the measurements the spec asked
for — time to first paint per thousand patch lines, whether Shift+Down
selects in a non-editable view, whether the split layout's rows come out
aligned under wrap. Prints what it measured; exits non-zero when a step
that should work doesn't.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

from collins import diffmodel, diffview, gitops  # noqa: E402
from collins.editor import style_scheme  # noqa: E402

FAILURES: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(("ok   " if cond else "FAIL ") + label + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def render(win: Gtk.Window, out: str) -> None:
    w, h = win.get_width(), win.get_height()
    paintable = Gtk.WidgetPaintable.new(win)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, w, h)
    node = snapshot.to_node()
    if node is None:
        print("render: empty snapshot")
        return
    texture = win.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
    texture.save_to_png(out)
    print(f"wrote {out} ({w}x{h})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=REPO_ROOT)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--unstaged", action="store_true")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--layout", default="auto")
    parser.add_argument("--wrap", action="store_true")
    parser.add_argument("--no-numbers", action="store_true")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--out", default=os.path.join(os.getcwd(), "diffview-probe.png"))
    args = parser.parse_args()

    load: object = {"show": args.ref}
    if args.unstaged:
        load = "unstaged"
    elif args.staged:
        load = "staged"
    started = time.monotonic()
    read = gitops.read_diff(args.repo, load, untracked=True)
    read_ms = (time.monotonic() - started) * 1000
    lines = sum(len(h.lines) for f in read.files for h in f.hunks)
    hunks = sum(len(f.hunks) for f in read.files)
    print(f"read_diff: ok={read.ok} files={len(read.files)} hunks={hunks} lines={lines} in {read_ms:.0f} ms")
    if not read.ok:
        print("read_diff refused:", read.error)
        return 1
    files = list(read.files)

    def reader(file: diffmodel.File, side: str) -> bytes | None:
        ref = gitops.side_ref(load, side)
        if load == "branch" or ref is None and side == diffmodel.OLD and load != "unstaged":
            return None
        return gitops.file_at(
            args.repo, ref, file.previous_path if side == diffmodel.OLD and file.previous_path else file.path
        )

    app = Adw.Application(application_id="com.episode6.Collins.DiffProbe")
    dark = args.dark
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT
    )

    def activate(app: Adw.Application) -> None:
        # The app's stylesheet, so the cards, rails and pinned header look
        # as they do in Collins (the scheme-following colours are the App's
        # own provider and are skipped: +/- counts render in plain text).
        from collins import app as collins_app

        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_data(collins_app._CSS)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        icon_root = collins_app._icon_root()
        if icon_root is not None:  # the bundled file-type icons, as the App prepends them
            theme = Gtk.IconTheme.get_for_display(display)
            theme.set_search_path([str(icon_root), *theme.get_search_path()])
        view = diffview.DiffView()
        view.set_scheme(style_scheme("", dark), dark)
        view.set_options(args.layout, not args.no_numbers, args.wrap, True)
        current: list[tuple[str, int]] = []
        view.connect("current-changed", lambda _v, path, hunk: current.append((path, hunk)))
        contexts: list[tuple[str, str, int]] = []
        view.connect("context-requested", lambda _v, path, gap, count: contexts.append((path, gap, count)))
        win = Gtk.ApplicationWindow(application=app, default_width=args.width, default_height=args.height)
        win.set_child(view)
        win.present()
        t0 = time.monotonic()
        view.load(files, load, reader, repo=args.repo)
        build_ms = (time.monotonic() - t0) * 1000
        per_k = build_ms / max(lines, 1) * 1000
        print(f"load(): {build_ms:.0f} ms for {lines} lines ({per_k:.0f} ms per 1000 lines)")
        print(f"split={view.is_split()}")
        rows = view.file_rows()
        ok("file rows match files", [r[0] for r in rows] == [f.path for f in files], f"{len(rows)} rows")
        first_text = next((f for f in files if f.hunks), None)

        def step_paint() -> bool:
            print(f"first paint after {(time.monotonic() - t0) * 1000:.0f} ms")
            render(win, args.out)
            GLib.timeout_add(50, step_keys)
            return GLib.SOURCE_REMOVE

        def step_keys() -> bool:
            if first_text is None:
                print("no text file to probe keys on")
                return finish()
            # reveal focuses the first hunk's view
            ok("reveal(first file)", view.reveal(first_text.path))
            focus = win.get_focus()
            section = view._section_for(first_text.path, diffmodel.NEW)
            hunk = section.hunks[0]
            in_hunk = focus is not None and any(focus is v.view for v in hunk.views)
            ok("focus is in the first hunk's view", in_hunk, repr(focus))
            ok("hunk wears .git-hunk-focused", hunk.has_css_class("git-hunk-focused"))
            ok(
                "current-changed named the file",
                bool(current) and current[-1][0] == first_text.path,
                repr(current[-1:]),
            )
            fv = next(v for v in hunk.views if v.view is focus)
            ok("cursor visible while focused", fv.view.get_cursor_visible())
            if len(fv.rows) >= 3:
                fv.view.emit("move-cursor", Gtk.MovementStep.DISPLAY_LINES, 1, True)
                fv.view.emit("move-cursor", Gtk.MovementStep.DISPLAY_LINES, 1, True)
                bounds = fv.buffer.get_selection_bounds()
                ok(
                    "Shift+Down twice selects lines 0..2",
                    bool(bounds) and bounds[0].get_line() == 0 and bounds[1].get_line() == 2,
                    repr([b.get_line() for b in bounds]) if bounds else "no selection",
                )
            else:
                print("skip Shift+Down: the first hunk has", len(fv.rows), "rows")
            heights = [v.view.get_height() for s_ in view._sections() for h in s_.hunks for v in h.views]
            ok(
                "every hunk view has a height",
                all(h > 0 for h in heights),
                f"{heights.count(0)} of {len(heights)} at 0",
            )
            # focus_hunk moves on
            before = view.current()
            moved = view.focus_hunk(1)
            print("focus_hunk(1) ->", moved, "current", before, "->", view.current())
            # a gap expands
            gaps = view.gap_rows(first_text.path)
            print("gap rows:", gaps)
            leading = [g for g in gaps if g[0].startswith("before:")]
            if leading:
                ok("expand_gap down", view.expand_gap(first_text.path, leading[0][0], diffview.DOWN, 5))
                GLib.timeout_add(700, lambda: step_gap(leading[0][0]))
            else:
                GLib.timeout_add(50, step_filter)
            return GLib.SOURCE_REMOVE

        def step_gap(address: str) -> bool:
            gaps = dict((g[0], g) for g in view.gap_rows(first_text.path))
            print("gap after expand:", gaps.get(address), "context-requested:", contexts[-1:])
            ok("gap shows 5 lines", address in gaps and gaps[address][2] == 5, repr(gaps.get(address)))
            trailing = [g for g in view.gap_rows(first_text.path) if g[0].startswith("trailing")]
            if trailing:
                view.expand_gap(first_text.path, trailing[0][0], diffview.ALL, 0)
                GLib.timeout_add(700, step_trailing)
            else:
                GLib.timeout_add(50, step_filter)
            return GLib.SOURCE_REMOVE

        def step_trailing() -> bool:
            trailing = [g for g in view.gap_rows(first_text.path) if g[0].startswith("trailing")]
            print("trailing gap after all:", trailing)
            GLib.timeout_add(50, step_filter)
            return GLib.SOURCE_REMOVE

        def step_filter() -> bool:
            shown = view.filter(first_text.path[-6:])
            ok("filter narrows", 1 <= shown < len(files) or len(files) == 1, f"{shown} of {len(files)}")
            ok(
                "filtered rows hidden",
                all(r[2] == (first_text.path[-6:].casefold() in r[0].casefold()) for r in view.file_rows()),
            )
            view.filter("")
            ok("filter cleared", all(r[2] for r in view.file_rows()))
            # split alignment under wrap
            if view.is_split():
                view.set_options("split", not args.no_numbers, True, True)
                GLib.timeout_add(600, step_align)
            else:
                GLib.timeout_add(50, step_reload)
            return GLib.SOURCE_REMOVE

        def step_align() -> bool:
            misaligned = 0
            checked = 0
            for section in view._sections():
                for hunk in section.hunks:
                    if len(hunk.views) != 2:
                        continue
                    old, new = hunk.views
                    for i in range(min(len(old.rows), len(new.rows))):
                        checked += 1
                        _y1, h1 = old.view.get_line_yrange(old.buffer.get_iter_at_line(i)[1])
                        _y2, h2 = new.view.get_line_yrange(new.buffer.get_iter_at_line(i)[1])
                        if h1 != h2:
                            misaligned += 1
            ok("split rows aligned under wrap", misaligned == 0, f"{misaligned} of {checked} rows differ")
            render(win, args.out.replace(".png", "-wrap.png"))
            view.set_options(args.layout, not args.no_numbers, args.wrap, True)
            GLib.timeout_add(50, step_reload)
            return GLib.SOURCE_REMOVE

        def step_reload() -> bool:
            # a reload with the same files keeps every widget
            before = [id(h) for s in view._sections() for h in s.hunks]
            view.load(files, load, reader, repo=args.repo)
            after = [id(h) for s in view._sections() for h in s.hunks]
            ok("reload keeps hunk widgets", before == after, f"{len(before)} hunks")
            # a reload with one file's hunk changed rebuilds only that hunk
            if first_text is not None and first_text.hunks:
                h0 = first_text.hunks[0]
                changed_line = diffmodel.Line(diffmodel.ADD, "probe changed this line", None, h0.new_start)
                new_hunk = diffmodel.Hunk(
                    h0.index,
                    h0.header,
                    h0.old_start,
                    h0.old_count,
                    h0.new_start,
                    h0.new_count,
                    h0.context,
                    (changed_line, *h0.lines[1:]),
                )
                changed = diffmodel.File(
                    first_text.path,
                    first_text.previous_path,
                    first_text.kind,
                    first_text.old_mode,
                    first_text.new_mode,
                    first_text.similarity,
                    first_text.untracked,
                    (new_hunk, *first_text.hunks[1:]),
                    first_text.additions,
                    first_text.deletions,
                    first_text.patch,
                    "probe",
                )
                view.load([changed if f is first_text else f for f in files], load, reader, repo=args.repo)
                after2 = [id(h) for s in view._sections() for h in s.hunks]
                ok(
                    "changed hunk rebuilt, the rest kept",
                    after2[0] != after[0] and after2[1:] == after[1:],
                    f"{sum(a != b for a, b in zip(after, after2, strict=True))} changed",
                )
            GLib.timeout_add(50, finish)
            return GLib.SOURCE_REMOVE

        def finish() -> bool:
            print("FAILURES:", FAILURES or "none")
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(700, step_paint)
        GLib.timeout_add(30000, lambda: (print("deadline"), os._exit(2)))

    app.connect("activate", activate)
    app.run([])
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
