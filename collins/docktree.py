# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The dock's split tree, GTK-free.

A `DockTree` is a binary tree of fixed-axis splits whose leaves carry
opaque values — in the app, the agent-terminal widget and `PanelStrip`
widgets; in tests, anything hashable. The GTK realization (`PanelDock`)
mirrors every mutation onto real `Gtk.Paned`s; this module only answers
"what is the tree now" so the structure can be unit-tested without a
display.

Terminology follows `Gtk.Paned`: a split's `a` child is the start child
(left, or top), `b` the end child. A split's orientation is fixed for its
lifetime — new layouts come from new splits, never from flipping an
existing one.
"""

from __future__ import annotations

from collections.abc import Iterator

# Where a new leaf lands relative to the one being split, mapped to the
# split's orientation ("h" = children side by side, "v" = stacked) and
# whether the new leaf takes the start slot.
_SIDES: dict[str, tuple[str, bool]] = {
    "left": ("h", True),
    "right": ("h", False),
    "above": ("v", True),
    "below": ("v", False),
}


class Leaf:
    """A tree leaf holding one opaque *value*."""

    def __init__(self, value: object) -> None:
        self.value = value
        self.parent: Split | None = None


class Split:
    """A fixed-orientation binary split of two child nodes."""

    def __init__(self, orientation: str, a: Leaf | Split, b: Leaf | Split) -> None:
        self.orientation = orientation
        self.a = a
        self.b = b
        self.parent: Split | None = None
        a.parent = self
        b.parent = self

    def slot_of(self, node: Leaf | Split) -> str:
        """Which slot *node* occupies: "a" or "b"."""
        if node is self.a:
            return "a"
        if node is self.b:
            return "b"
        raise ValueError("node is not a child of this split")

    def sibling_of(self, node: Leaf | Split) -> Leaf | Split:
        return self.b if node is self.a else self.a


class DockTree:
    """A binary split tree over opaque leaf values.

    Mutations keep the tree canonical: every split has exactly two
    children, so a leaf can only disappear by dissolving its parent split
    (the sibling promotes into the grandparent's slot). The root is a bare
    leaf until the first split and again after the last removal.
    """

    def __init__(self, value: object) -> None:
        self.root: Leaf | Split = Leaf(value)

    # -- queries -----------------------------------------------------------

    def leaves(self) -> list[object]:
        """Every leaf value, in spatial order (start children first)."""
        return [leaf.value for leaf in self._iter_leaves(self.root)]

    def _iter_leaves(self, node: Leaf | Split) -> Iterator[Leaf]:
        if isinstance(node, Leaf):
            yield node
        else:
            yield from self._iter_leaves(node.a)
            yield from self._iter_leaves(node.b)

    def find(self, value: object) -> Leaf:
        """The leaf holding *value* (by identity, then equality)."""
        for leaf in self._iter_leaves(self.root):
            if leaf.value is value or leaf.value == value:
                return leaf
        raise ValueError(f"value not in tree: {value!r}")

    def __contains__(self, value: object) -> bool:
        try:
            self.find(value)
        except ValueError:
            return False
        return True

    def __len__(self) -> int:
        return sum(1 for _ in self._iter_leaves(self.root))

    def separator_of(self, value: object, other: object) -> Split:
        """The split that divides *value* from *other*: the nearest
        ancestor of *value*'s leaf whose other branch holds *other*.

        Its orientation is how the two are laid out relative to each other
        — "h" side by side, "v" stacked — however deep either sits under
        it, and its divider is the one whose position sizes one against
        the other. Raises when nothing separates them (*other* missing, or
        the same leaf)."""
        node: Leaf | Split = self.find(value)
        while node.parent is not None:
            parent = node.parent
            if self._holds(parent.sibling_of(node), other):
                return parent
            node = parent
        raise ValueError(f"no split separates {value!r} from {other!r}")

    def _holds(self, node: Leaf | Split, value: object) -> bool:
        """Whether *value* is a leaf anywhere under *node*."""
        return any(
            leaf.value is value or leaf.value == value
            for leaf in self._iter_leaves(node)
        )

    def next_leaf(self, value: object) -> object:
        """The value after *value* in leaf order, wrapping around. With a
        single leaf this returns the value itself."""
        values = self.leaves()
        for i, v in enumerate(values):
            if v is value or v == value:
                return values[(i + 1) % len(values)]
        raise ValueError(f"value not in tree: {value!r}")

    # -- mutations ---------------------------------------------------------

    def split(self, at_value: object, new_value: object, side: str) -> Split:
        """Split the leaf holding *at_value*, placing *new_value* on *side*
        of it ("left" | "right" | "above" | "below"). Returns the new Split
        node — its orientation and slots tell the GTK layer how to build
        the paned."""
        if side not in _SIDES:
            raise ValueError(f"unknown side: {side!r}")
        if new_value in self:
            raise ValueError("new value is already in the tree")
        orientation, new_first = _SIDES[side]
        leaf = self.find(at_value)
        parent = leaf.parent
        new_leaf = Leaf(new_value)
        pair = (new_leaf, leaf) if new_first else (leaf, new_leaf)
        split = Split(orientation, *pair)
        self._replace(parent, leaf, split)
        return split

    def remove(self, value: object) -> tuple[Split, Leaf | Split]:
        """Remove the leaf holding *value*, dissolving its parent split and
        promoting the sibling into the grandparent's slot. Returns the
        dissolved split and the promoted sibling. The last leaf cannot be
        removed — a dock always keeps its terminal."""
        leaf = self.find(value)
        parent = leaf.parent
        if parent is None:
            raise ValueError("cannot remove the only leaf")
        sibling = parent.sibling_of(leaf)
        self._replace(parent.parent, parent, sibling)
        leaf.parent = None
        parent.parent = None
        return parent, sibling

    def _replace(
        self, parent: Split | None, old: Leaf | Split, new: Leaf | Split
    ) -> None:
        new.parent = parent
        if parent is None:
            self.root = new
        elif parent.a is old:
            parent.a = new
        else:
            parent.b = new
