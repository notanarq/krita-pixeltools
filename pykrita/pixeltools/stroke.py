# SPDX-License-Identifier: GPL-2.0-or-later
"""
stroke.py - a drawing stroke as one undoable unit.

The problem this solves
-----------------------
A live stroke produces a write per mouse move: a hundred updates for a
hundred-pixel drag. If each write landed in Krita's undo stack on its own,
Ctrl+Z would rewind the stroke one mouse move at a time, which is unusable.

So the stroke is built in a local buffer and written to the layer ONCE, on
release. That is the design, not an optimisation:

    begin()   take the pixels the stroke covers, before it starts
    update()  paint into the local buffer only - Krita is never touched
    reset_paint()  return to the layer copy, for a shrinking preview
    commit()  write the union of everything the stroke touched, once
    cancel()  drop the buffer, leaving the layer untouched

Why the union of every touched region
-------------------------------------
commit() writes the rectangle covering the WHOLE stroke, not just the last
frame. Writing only the latest frame would leave the earlier parts of the
stroke unwritten, and writing each frame separately is exactly the many-undo-
steps failure. One rectangle, one command, one Ctrl+Z.

Why the layer is read at begin() and not at commit()
----------------------------------------------------
The write must contain the layer AS IT WAS before the stroke, with our pixels
on top, because the undoable path is a merge and the merge carries the whole
rectangle. Reading at commit() would carry whatever else the user changed
meanwhile into our command, and Ctrl+Z would undo their work too.

No Qt here, and no Krita type beyond what is passed in, so this is testable
on the command line.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

Point = Tuple[int, int]
Rgba = Tuple[int, int, int, int]

BPP = 4  # bytes per pixel, 8-bit BGRA


class StrokeError(Exception):
    """Raised when a stroke is driven out of order; a programming error."""


class StrokeSession:
    """One drawing stroke: local buffer in, one layer write out."""

    def __init__(self) -> None:
        self.base: Optional[bytes] = None      # the layer, as the stroke began
        self._base_clean: Optional[bytes] = None  # pristine copy for repaint
        self.width = 0
        self.height = 0
        self.layer_origin = (0, 0)
        self._touched: Optional[list] = None   # [x0, y0, x1, y1] in image coords
        self.active = False
        self.paint_count = 0

    # -- lifecycle ------------------------------------------------------

    def begin(self, base_raw: bytes, width: int, height: int,
              layer_origin: Tuple[int, int] = (0, 0)) -> None:
        """Starts a stroke over a full copy of the layer's pixels.

        base_raw must be the WHOLE layer in BGRA, exactly as
        Node.pixelData(0, 0, w, h) returns it. The stroke keeps that copy so
        it can build the final write without touching Krita again.
        """
        expected = width * height * BPP
        if len(base_raw) < expected:
            raise StrokeError(
                f"Layer copy is {len(base_raw)} bytes, expected {expected}."
            )

        self._base_clean = bytes(base_raw[:expected])
        self.base = bytes(base_raw[:expected])
        self.width = width
        self.height = height
        self.layer_origin = layer_origin
        self._touched = None
        self.active = True
        self.paint_count = 0

    def update(self, points: Sequence[Point], rgba: Rgba,
               erase: bool = False) -> int:
        """Paints into the local buffer. Returns pixels changed.

        Krita is NOT touched here. That is the whole point: a mouse move is
        cheap and cannot pollute the undo stack.
        """
        if not self.active or self.base is None:
            raise StrokeError("update() before begin()")
        if not points:
            return 0

        # bytearray is mutable, so we work directly on a mutable copy.
        if not isinstance(self.base, bytearray):
            self.base = bytearray(self.base)

        r, g, b, a = rgba
        changed = 0

        for x, y in points:
            if x < 0 or y < 0 or x >= self.width or y >= self.height:
                continue
            i = (y * self.width + x) * BPP
            if erase:
                self.base[i + 0] = 0
                self.base[i + 1] = 0
                self.base[i + 2] = 0
                self.base[i + 3] = 0
            else:
                self.base[i + 0] = b
                self.base[i + 1] = g
                self.base[i + 2] = r
                self.base[i + 3] = a
            self._touch(x, y)
            changed += 1

        self.paint_count += changed
        return changed

    def reset_paint(self) -> None:
        """Discards painted pixels and returns to the layer copy.

        The drawing tools compute their preview from the whole stroke, and
        that preview can SHRINK: the pixel-perfect pass drops a corner when a
        later segment makes it redundant. Repainting from the clean copy is
        the only way the committed result matches what the user saw, since a
        pixel already written cannot be un-written otherwise.
        """
        if self._base_clean is None:
            raise StrokeError("reset_paint() before begin()")
        self.base = bytearray(self._base_clean)
        self._touched = None
        self.paint_count = 0

    def _touch(self, x: int, y: int) -> None:
        if self._touched is None:
            self._touched = [x, y, x, y]
            return
        t = self._touched
        if x < t[0]:
            t[0] = x
        elif x > t[2]:
            t[2] = x
        if y < t[1]:
            t[1] = y
        elif y > t[3]:
            t[3] = y

    # -- output ---------------------------------------------------------

    @property
    def dirty_rect(self) -> Tuple[int, int, int, int]:
        """(x, y, w, h) covering everything the stroke touched."""
        if self._touched is None:
            return (0, 0, 0, 0)
        x0, y0, x1, y1 = self._touched
        return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)

    def payload(self) -> bytes:
        """The rectangle to write: the stroke's pixels over the base copy.

        Returns the BGRA bytes of dirty_rect, ready for painter.write_rect().
        """
        if self.base is None:
            return b""
        x, y, w, h = self.dirty_rect
        if w <= 0 or h <= 0:
            return b""

        out = bytearray(w * h * BPP)
        for row in range(h):
            src = ((y + row) * self.width + x) * BPP
            dst = row * w * BPP
            out[dst:dst + w * BPP] = self.base[src:src + w * BPP]
        return bytes(out)

    def cancel(self) -> None:
        """Abandons the stroke without writing anything to the layer."""
        self.base = None
        self._base_clean = None
        self._touched = None
        self.active = False
        self.paint_count = 0

    def finish(self, doc, node) -> Tuple[bool, str, int]:
        """Writes the stroke to the layer as ONE undoable step.

        Returns (ok, note, pixels_written). The stroke is closed either way,
        so a failure cannot leave it half-open and accept more input.
        """
        from . import painter

        if not self.active:
            raise StrokeError("finish() without an active stroke")

        x, y, w, h = self.dirty_rect
        payload = self.payload()
        pixels = self.paint_count

        self.base = None
        self._base_clean = None
        self._touched = None
        self.active = False
        self.paint_count = 0

        if w <= 0 or h <= 0:
            return (True, "", 0)

        ok, note = painter.write_rect(doc, node, payload, x, y, w, h)
        return (ok, note, pixels)
