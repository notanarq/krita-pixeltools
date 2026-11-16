# SPDX-License-Identifier: GPL-2.0-or-later
"""
test_painter.py - paints into a simulated document, with the REAL Node contract.

Why this exists
---------------
The user reported "Could not merge the stroke into the layer" on a brand new
paint layer - a case where merging obviously should work. Three separate
mistakes produced that:

  1. mergeDown() was checked for a return value, but the API documents it as
     void. The merge SUCCEEDED and the code claimed failure anyway.
  2. addChildNode() was called on the target layer instead of its parent, so
     the temp layer became a mask inside it.
  3. mergeDown() merges with the first VISIBLE node underneath, not
     necessarily with the target.

The fake document below mirrors the documented Node contract - including
mergeDown() returning None - so those mistakes fail here.

Run:  python3 core/tests/test_painter.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "pykrita", "pixeltools", "painter.py")

failures = []
checks = 0


def check(cond, what):
    global checks
    checks += 1
    if cond:
        print("  ok  :", what)
    else:
        failures.append(what)
        print("  FAIL:", what)


def load_painter():
    spec = importlib.util.spec_from_file_location("painter_under_test", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# Fake Krita document
# ---------------------------------------------------------------------------

BGRA_BPP = 4


class FakeNode:
    """Mirrors Node: setPixelData/pixelData are BGRA, mergeDown returns None."""

    _counter = 0

    def __init__(self, doc, name, node_type="paintlayer",
                 color_model="RGBA", color_depth="U8"):
        FakeNode._counter += 1
        self._uid = FakeNode._counter
        self.doc = doc
        self._name = name
        self._type = node_type
        self._cm = color_model
        self._cd = color_depth
        self._parent = None
        self._visible = True
        self._locked = False
        self._pixels = {}  # (x, y) -> (b, g, r, a)
        self._children = []  # bottom-up, like Krita's childNodes()
        # Records so tests can assert on the call pattern.
        self.add_calls = []
        self.merge_calls = 0

    # --- identity ------------------------------------------------------
    def uniqueId(self):
        return self._uid

    def name(self):
        return self._name

    def setName(self, n):
        self._name = n

    def type(self):
        return self._type

    def colorModel(self):
        return self._cm

    def colorDepth(self):
        return self._cd

    def visible(self):
        return self._visible

    def setVisible(self, v):
        self._visible = v

    def locked(self):
        return self._locked

    def setLocked(self, v):
        self._locked = v

    # --- tree ----------------------------------------------------------
    def parentNode(self):
        return self._parent

    def childNodes(self):
        return list(self._children)

    def addChildNode(self, child, above):
        """Documented: add `child` above `above` among THIS node's children.

        `above=None` appends on top of the stack. Otherwise the child is
        inserted immediately above the given sibling, which is what the
        merge path relies on.
        """
        self.add_calls.append((child, above))
        child._parent = self
        if above is None:
            self._children.append(child)
            return True
        if above not in self._children:
            return False
        self._children.insert(self._children.index(above) + 1, child)
        return True

    def remove(self):
        if self._parent is not None and self in self._parent._children:
            self._parent._children.remove(self)
        self.doc.all_nodes.discard(self)
        self._parent = None
        return True

    # --- pixels --------------------------------------------------------
    def setPixelData(self, data, x, y, w, h):
        if self._type != "paintlayer":
            return False  # documented: silently does nothing for others
        if self._locked:
            return False
        data = bytes(data)
        need = w * h * BGRA_BPP
        if len(data) < need:
            return False
        for row in range(h):
            for col in range(w):
                i = (row * w + col) * BGRA_BPP
                self._pixels[(x + col, y + row)] = tuple(data[i:i + BGRA_BPP])
        return True

    def pixelData(self, x, y, w, h):
        out = bytearray()
        for row in range(h):
            for col in range(w):
                out.extend(self._pixels.get((x + col, y + row), (0, 0, 0, 0)))
        return bytes(out)

    def mergeDown(self):
        """Merges into the first VISIBLE sibling underneath. Returns None."""
        self.merge_calls += 1
        parent = self._parent
        if parent is None:
            return None
        sibs = parent.childNodes()
        try:
            idx = sibs.index(self)
        except ValueError:
            return None
        target = None
        for below in reversed(sibs[:idx]):
            if below.visible():
                target = below
                break
        if target is None:
            return None
        # composite our pixels onto the target
        for pos, px in self._pixels.items():
            if px[3] != 0:
                target._pixels[pos] = px
        if self in parent._children:
            parent._children.remove(self)
        self.doc.all_nodes.discard(self)
        self._parent = None
        return None  # <-- the documented contract


class FakeDoc:
    def __init__(self):
        self.all_nodes = set()
        self._counter = 0
        self.root = self._mk("root", "grouplayer")

    def _mk(self, name, node_type="paintlayer", **kw):
        n = FakeNode(self, name, node_type, **kw)
        self.all_nodes.add(n)
        return n

    def createNode(self, name, node_type):
        n = self._mk(name, node_type)
        return n

    def add_top(self, name, node_type="paintlayer", **kw):
        """Adds a layer at the top of the stack, as Krita's UI does."""
        n = self._mk(name, node_type, **kw)
        n._parent = self.root
        self.root._children.append(n)
        return n


def main():
    print("pixeltools - painter test against the real Node contract\n")
    pe = load_painter()

    print("1. mergeDown() returning None must NOT be read as failure")
    doc = FakeDoc()
    layer = doc.add_top("Layer 1")
    check(pe._is_topmost_visible(layer), "brand-new layer is topmost visible")
    written, note = pe.stamp(doc, layer, [(0, 0), (1, 0), (2, 0)], (255, 0, 0, 255))
    check(written == 3, f"wrote 3 px (got {written})")
    check(note == "", f"no caveat on the undoable path (got {note!r})")
    check(
        layer._pixels.get((0, 0)) == (0, 0, 255, 255),
        "pixel landed as BGRA red",
    )

    print("\n2. addChildNode must be called on the PARENT, not the layer")
    # After the merge path, no layer should have gained a child.
    check(
        len(layer.add_calls) == 0,
        "target layer received no addChildNode call",
    )
    check(
        len(doc.root.add_calls) == 1,
        f"parent received the addChildNode call (got {len(doc.root.add_calls)})",
    )
    check(
        doc.root.add_calls and doc.root.add_calls[0][1] is layer,
        "temp was inserted above the target",
    )

    print("\n3. a layer with a visible layer above it uses the direct path")
    doc2 = FakeDoc()
    lower = doc2.add_top("Lower")
    upper = doc2.add_top("Upper")
    check(not pe._is_topmost_visible(lower), "lower layer is not topmost")
    written, note = pe.stamp(doc2, lower, [(5, 5), (6, 5)], (0, 255, 0, 255))
    check(written == 2, "wrote 2 px into the lower layer")
    check("Ctrl+Z" in note, f"the undo caveat is reported (got {note!r})")
    check(
        lower._pixels.get((5, 5)) == (0, 255, 0, 255),
        "pixels landed in the TARGET, not the layer above",
    )
    check(
        (5, 5) not in upper._pixels,
        "the layer above was not touched",
    )

    print("\n4. non paint layers are refused with a clear message")
    doc3 = FakeDoc()
    grp = doc3.add_top("Group", node_type="grouplayer")
    try:
        pe.stamp(doc3, grp, [(0, 0)], (255, 255, 255, 255))
        check(False, "group layer should have raised")
    except pe.PaintError as exc:
        check("paint layer" in str(exc).lower(), f"refused a group: {exc}")

    print("\n5. wrong colour depth is refused, not silently miswritten")
    doc4 = FakeDoc()
    l16 = doc4.add_top("16-bit", color_depth="U16")
    try:
        pe.stamp(doc4, l16, [(0, 0)], (1, 2, 3, 255))
        check(False, "U16 should have raised")
    except pe.PaintError as exc:
        check("8-bit" in str(exc) or "U16" in str(exc), f"refused U16: {exc}")

    print("\n6. the failure message from the bug report can no longer occur")
    doc5 = FakeDoc()
    fresh = doc5.add_top("Nova camada")
    try:
        pe.stamp(doc5, fresh, [(0, 0), (1, 1)], (10, 20, 30, 255))
        check(True, "a brand-new paint layer draws without raising")
    except pe.PaintError as exc:
        check(False, f"brand-new layer raised: {exc}")

    print("\n" + "-" * 54)
    if failures:
        print(f"{checks} checks, {len(failures)} FAILURE(S)")
        for f in failures:
            print("  FAIL:", f)
        print("RESULT: FAILED")
        return 1
    print(f"{checks} checks, 0 failures")
    print("RESULT: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
