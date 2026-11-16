# SPDX-License-Identifier: GPL-2.0-or-later
"""
buffer.py - an in-memory pixel buffer for the K-Sprite canvas.

Why this exists
---------------
Drawing inside Krita from a Python plugin needs a copy of the layer the user
can paint into, then written back once. This module owns that copy: a plain
bytearray in Krita's native 8-bit RGBA layout, with coordinate helpers and a
local undo stack.

No Qt and no Krita imports live here, which is what lets the whole thing be
tested on the command line.

Pixel layout
------------
Krita's 8-bit integer RGBA is stored as **BGRA** - the API reference states
"Integer RGBA: Blue, Green, Red, Alpha". Every offset in this file assumes
that order. Getting it wrong swaps red and blue, which is invisible on a
grayscale test image and glaring in real art, so the byte order is asserted
by the tests.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple

Point = Tuple[int, int]
Rgba = Tuple[int, int, int, int]

BPP = 4  # bytes per pixel for 8-bit RGBA


class BufferError(Exception):
    """Raised when the buffer cannot be built; message is user-facing."""


class DirtyRect:
    """A growing rectangle covering every pixel that changed.

    Used to write back only what was touched, instead of the whole layer,
    which for a 4000x4000 document would be 64 MB per stroke.
    """

    __slots__ = ("x0", "y0", "x1", "y1", "_empty")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._empty = True
        self.x0 = self.y0 = 0
        self.x1 = self.y1 = 0

    def add(self, x: int, y: int) -> None:
        if self._empty:
            self.x0 = self.x1 = x
            self.y0 = self.y1 = y
            self._empty = False
            return
        if x < self.x0:
            self.x0 = x
        elif x > self.x1:
            self.x1 = x
        if y < self.y0:
            self.y0 = y
        elif y > self.y1:
            self.y1 = y

    def add_points(self, points: Iterable[Point]) -> None:
        for x, y in points:
            self.add(x, y)

    def add_rect(self, x: int, y: int, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return
        self.add(x, y)
        self.add(x + w - 1, y + h - 1)

    @property
    def empty(self) -> bool:
        return self._empty

    def as_tuple(self) -> Tuple[int, int, int, int]:
        """(x, y, w, h), or (0, 0, 0, 0) when nothing changed."""
        if self._empty:
            return (0, 0, 0, 0)
        return (self.x0, self.y0, self.x1 - self.x0 + 1, self.y1 - self.y0 + 1)

    def as_xyxy(self) -> Tuple[int, int, int, int]:
        if self._empty:
            return (0, 0, 0, 0)
        return (self.x0, self.y0, self.x1, self.y1)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DirtyRect{self.as_tuple()}"


class LayerBuffer:
    """A writable pixel copy of one Krita layer.

    The buffer always covers the WHOLE document, not just the layer's extent,
    because every Krita paint layer spans the image. Coordinates in this class
    are image coordinates, so nothing has to translate at the edges.
    """

    def __init__(self, width: int, height: int, data: Optional[bytes] = None) -> None:
        if width <= 0 or height <= 0:
            raise BufferError(f"Invalid document size {width}x{height}.")

        self.width = width
        self.height = height
        expected = width * height * BPP

        if data is None:
            self._data = bytearray(expected)  # fully transparent
        else:
            data = bytes(data)
            if len(data) < expected:
                # Krita returns short data for an empty layer; pad it rather
                # than failing, because an empty layer is a normal starting
                # point (the K-Sprite "New layer" case).
                data = data + bytes(expected - len(data))
            elif len(data) > expected:
                data = data[:expected]
            self._data = bytearray(data)

        self.dirty = DirtyRect()

    # -- geometry -------------------------------------------------------

    def contains(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def index_of(self, x: int, y: int) -> int:
        return (y * self.width + x) * BPP

    # -- reading --------------------------------------------------------

    def get(self, x: int, y: int) -> Rgba:
        """Colour at (x, y) as (r, g, b, a); transparent outside the image."""
        if not self.contains(x, y):
            return (0, 0, 0, 0)
        i = self.index_of(x, y)
        b, g, r, a = self._data[i : i + 4]
        return (r, g, b, a)

    def row(self, y: int) -> bytes:
        """Raw BGRA bytes of row y, for building a QImage."""
        start = y * self.width * BPP
        return bytes(self._data[start : start + self.width * BPP])

    def raw(self) -> bytes:
        return bytes(self._data)

    def region(self, x: int, y: int, w: int, h: int) -> bytes:
        """Raw BGRA bytes of a rectangle, for setPixelData on commit."""
        if w <= 0 or h <= 0:
            return b""
        # Clip to the image so callers can pass an unclipped dirty rect.
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.width, x + w)
        y1 = min(self.height, y + h)
        if x1 <= x0 or y1 <= y0:
            return b""

        out = bytearray()
        for row_y in range(y0, y1):
            start = self.index_of(x0, row_y)
            out.extend(self._data[start : start + (x1 - x0) * BPP])
        return bytes(out)

    # -- writing --------------------------------------------------------

    def put(self, x: int, y: int, rgba: Rgba) -> bool:
        """Sets one pixel. Returns False when outside the image."""
        if not self.contains(x, y):
            return False
        r, g, b, a = rgba
        i = self.index_of(x, y)
        self._data[i + 0] = b
        self._data[i + 1] = g
        self._data[i + 2] = r
        self._data[i + 3] = a
        self.dirty.add(x, y)
        return True

    def put_generated(self, points: Sequence[Point], rgba: Rgba) -> int:
        """Paints a list of (already deduplicated) points. Returns how many landed."""
        r, g, b, a = rgba
        count = 0
        for x, y in points:
            if not self.contains(x, y):
                continue
            i = self.index_of(x, y)
            self._data[i + 0] = b
            self._data[i + 1] = g
            self._data[i + 2] = r
            self._data[i + 3] = a
            self.dirty.add(x, y)
            count += 1
        return count

    def erase(self, x: int, y: int) -> bool:
        """Makes a pixel fully transparent - the eraser."""
        return self.put(x, y, (0, 0, 0, 0))

    def paste_raw(self, x: int, y: int, w: int, h: int, raw: bytes) -> None:
        """Restores a chunk of raw BGRA data, used by the local undo stack.

        Copies pixel by pixel and skips anything outside the image. A row
        slice would need careful clamping at both ends, and getting that
        wrong silently RESIZES the bytearray when a slice is assigned past
        its end - which corrupts every later offset. Per-pixel writes cannot
        do that.
        """
        need = w * h * BPP
        if len(raw) < need:
            raise BufferError(f"paste_raw needs {need} bytes, got {len(raw)}.")

        for row in range(h):
            ty = y + row
            if not (0 <= ty < self.height):
                continue
            src = row * w * BPP
            for col in range(w):
                tx = x + col
                if not (0 <= tx < self.width):
                    continue
                i = src + col * BPP
                j = self.index_of(tx, ty)
                self._data[j : j + BPP] = raw[i : i + BPP]

        self.dirty.add_rect(x, y, w, h)

    # -- history --------------------------------------------------------

    def snapshot(self, rect: Tuple[int, int, int, int]) -> bytes:
        """Captures a rectangle so it can be restored later."""
        x, y, w, h = rect
        return self.region(x, y, w, h)

    def restore(self, rect: Tuple[int, int, int, int], raw: bytes) -> None:
        x, y, w, h = rect
        self.paste_raw(x, y, w, h, raw)
