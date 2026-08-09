# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""(De)serialization of a session's panel-dock layout, GTK-free.

A session's `panel_layout` entry describes the dock around its agent
terminal — the home edge and remembered home-strip sizes, plus (when any
strips exist) the serialized split tree:

    {
      "mode": "bottom" | "right",          # the shells' home edge (Ctrl+J)
      "sizes": {"bottom": px, "right": px},  # home strip's per-axis sizes
      "tree": <node>,                      # absent = no strips (panel closed)
    }

    node  := {"terminal": true}
           | {"strip": {"open": bool, "home": bool, "selected": int,
                        "pages": [page, ...]}}
           | {"split": "h" | "v", "size": px, "managed": "a" | "b",
              "a": node, "b": node}
    page  := {"kind": "shell", "hist": ordinal}    # panelhistory file key
           | {"kind": <other>, ...}                # future kinds (e.g. "pr")

A split's "size" is the managed child's pixel extent (the value its
PanedSizer remembers), "managed" which slot that child occupies. A strip
saved "open": false is *hidden*, not closed — its pages restore running,
invisible until Ctrl+J — and only the home strip can be hidden, so open
is forced true elsewhere. Page dicts carry whatever their kind needs; only
their shape is validated here, so a layout saved by a build that knows
more kinds survives one that doesn't (restore prunes, see `prune`).

Persisted state is untrusted input: `validate` returns the normalized
entry, dropping a malformed tree (falling back to the fresh-session
default — no strips — rather than guessing at a structure) and the whole
entry when nothing usable remains. `from_legacy` converts the pre-tree
`panel_states` shape ({"open", "mode", "sizes"}) read-time and one-way.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_MODES = ("bottom", "right")


def _valid_size(value: object) -> int:
    """A stored pixel size: positive int, else 0 ("never sized")."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def _valid_sizes(value: object) -> dict[str, int]:
    """The per-axis home-strip sizes, invalid values dropped."""
    if not isinstance(value, dict):
        return {}
    return {
        mode: size for mode in _MODES if (size := _valid_size(value.get(mode)))
    }


def _valid_page(page: object) -> dict | None:
    """One page's normalized dict, or None. A shell page needs the history
    ordinal its scrollback file is keyed by; other kinds keep their dict
    as-is — their fields are theirs to validate at restore."""
    if not isinstance(page, dict):
        return None
    kind = page.get("kind")
    if not isinstance(kind, str) or not kind:
        return None
    if kind == "shell":
        hist = page.get("hist")
        if not isinstance(hist, int) or isinstance(hist, bool) or hist < 0:
            return None
        return {"kind": "shell", "hist": hist}
    return dict(page)


def _valid_node(node: object) -> tuple[dict, int, int, int] | None:
    """Recursively validate one tree node. Returns (normalized node,
    terminal count, strip count, home count), or None on any malformed
    piece — a tree is taken whole or not at all."""
    if not isinstance(node, dict):
        return None
    if node.get("terminal") is True:
        return {"terminal": True}, 1, 0, 0
    strip = node.get("strip")
    if isinstance(strip, dict):
        raw_pages = strip.get("pages")
        if not isinstance(raw_pages, list) or not raw_pages:
            return None
        pages = [_valid_page(p) for p in raw_pages]
        if any(p is None for p in pages):
            return None
        home = strip.get("home") is True
        selected = strip.get("selected")
        clean = {
            "strip": {
                # Only the home strip can hide; anything else is visible by
                # construction, whatever a hand-edited file claims.
                "open": bool(strip.get("open", True)) or not home,
                "home": home,
                "selected": selected
                if isinstance(selected, int) and not isinstance(selected, bool)
                else 0,
                "pages": pages,
            }
        }
        return clean, 0, 1, 1 if home else 0
    orientation = node.get("split")
    if orientation in ("h", "v"):
        a = _valid_node(node.get("a"))
        b = _valid_node(node.get("b"))
        if a is None or b is None:
            return None
        clean = {
            "split": orientation,
            "size": _valid_size(node.get("size")),
            "managed": node.get("managed") if node.get("managed") in ("a", "b") else "b",
            "a": a[0],
            "b": b[0],
        }
        return clean, a[1] + b[1], a[2] + b[2], a[3] + b[3]
    return None


def _valid_tree(tree: object) -> dict | None:
    """The normalized tree, or None. The dock invariants hold or the tree
    falls: exactly one terminal leaf, at least one strip (a lone terminal
    is the fresh default, not a layout), at most one home strip, and no
    two shell pages claiming the same history ordinal (they would share
    one scrollback file, and the next save would fold one shell's text
    over the other's)."""
    result = _valid_node(tree)
    if result is None:
        return None
    clean, terminals, strips, homes = result
    if terminals != 1 or strips < 1 or homes > 1:
        return None
    hists = list(_iter_shell_hists(clean))
    if len(hists) != len(set(hists)):
        return None
    return clean


def _iter_shell_hists(node: dict):
    """Every shell page's history ordinal across a validated tree."""
    if "strip" in node:
        for page in node["strip"]["pages"]:
            if page["kind"] == "shell":
                yield page["hist"]
    elif "split" in node:
        yield from _iter_shell_hists(node["a"])
        yield from _iter_shell_hists(node["b"])


def validate(entry: object) -> dict | None:
    """Normalize a stored panel_layout entry. A malformed tree is dropped
    (logged) while the mode/size memory survives; None when nothing usable
    remains."""
    if not isinstance(entry, dict):
        return None
    clean: dict = {"mode": entry.get("mode") if entry.get("mode") in _MODES else "bottom"}
    sizes = _valid_sizes(entry.get("sizes"))
    if sizes:
        clean["sizes"] = sizes
    if "tree" in entry:
        tree = _valid_tree(entry.get("tree"))
        if tree is not None:
            clean["tree"] = tree
        else:
            log.warning("malformed panel layout tree dropped: %r", entry.get("tree"))
    return clean


def prune(entry: dict, kinds: set[str]) -> dict:
    """Drop tree pages whose kind isn't in *kinds* (a layout saved by a
    build that knows more page kinds than this one), collapsing emptied
    strips — the sibling promotes, exactly as a live strip collapses — and
    the tree itself once only the terminal remains. Operates on a
    `validate`d entry; returns a new one."""
    clean = {k: v for k, v in entry.items() if k != "tree"}
    tree = entry.get("tree")
    if tree is not None:
        tree = _prune_node(tree, kinds)
        if tree is not None and "terminal" not in tree:
            clean["tree"] = tree
    return clean


def _prune_node(node: dict, kinds: set[str]) -> dict | None:
    if "terminal" in node:
        return node
    if "strip" in node:
        strip = node["strip"]
        pages = [p for p in strip["pages"] if p["kind"] in kinds]
        if not pages:
            return None
        selected = strip["selected"]
        if not 0 <= selected < len(pages):
            selected = 0
        return {"strip": {**strip, "selected": selected, "pages": pages}}
    a = _prune_node(node["a"], kinds)
    b = _prune_node(node["b"], kinds)
    if a is None:
        return b
    if b is None:
        return a
    return {**node, "a": a, "b": b}


def from_legacy(state: object, ordinals: list[int]) -> dict | None:
    """A pre-tree `panel_states` entry ({"open", "mode", "sizes"}) as a
    layout entry. An open panel becomes the two-node tree for its mode with
    one shell page per existing history file (*ordinals*, or a single blank
    shell — exactly what showing the panel would have recreated); a closed
    one keeps only the mode/size memory, so nothing spawns until Ctrl+J,
    matching the old behavior."""
    if not isinstance(state, dict):
        return None
    mode = state.get("mode") if state.get("mode") in _MODES else "bottom"
    entry: dict = {"mode": mode}
    sizes = _valid_sizes(state.get("sizes"))
    if sizes:
        entry["sizes"] = sizes
    if state.get("open"):
        pages = [{"kind": "shell", "hist": o} for o in sorted(set(ordinals)) if o >= 0]
        if not pages:
            pages = [{"kind": "shell", "hist": 0}]
        entry["tree"] = {
            "split": "v" if mode == "bottom" else "h",
            "size": sizes.get(mode, 0),
            "managed": "b",
            "a": {"terminal": True},
            "b": {
                "strip": {"open": True, "home": True, "selected": 0, "pages": pages}
            },
        }
    return entry
