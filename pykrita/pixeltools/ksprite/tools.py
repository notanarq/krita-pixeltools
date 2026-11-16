# SPDX-License-Identifier: GPL-2.0-or-later
"""
tools.py - the K-Sprite drawing tools, with no Qt and no Krita imports.

Each tool is a small state machine driven by three calls:

    begin(px, py)   the mouse went down at a pixel
    drag(px, py)    the mouse moved while down
    end(px, py)     the mouse came up

`points()` returns what should be painted right now, so the canvas can show a
live preview while the user drags, and `commit_points()` returns what to
write when the stroke is final.

Which tools get the pixel-perfect pass
-------------------------------------
Only Brush and Line. That is not a style choice, it was measured:

  * Brush is the tool the algorithm was invented for: a freehand path of
    single-pixel steps gains a doubled corner at every diagonal step.
  * Line is a straight Bresenham run, where the pass can only ever remove a
    redundant stair corner.
  * Bezier, Square and Circle are exact by construction. Filtering them
    causes real damage: a 7x5 rectangle loses all four corners and becomes a
    chamfered octagon, and the circle lost 7 of its 28 pixels with holes.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

Point = Tuple[int, int]
Rgba = Tuple[int, int, int, int]

# Tools, in the order the toolbar shows them.
TOOL_BRUSH = "brush"
TOOL_LINE = "line"
TOOL_BEZIER = "bezier"
TOOL_SQUARE = "square"
TOOL_CIRCLE = "circle"

TOOLS = [TOOL_BRUSH, TOOL_LINE, TOOL_BEZIER, TOOL_SQUARE, TOOL_CIRCLE]

# Tools that run the pixel-perfect pass. See the module docstring.
PERFECT_TOOLS = {TOOL_BRUSH, TOOL_LINE}


class ToolError(Exception):
    """Raised when a tool is driven out of order; a programming error."""


class Tool:
    """Base class: tracks the drag and asks the engine for points."""

    name = "tool"

    def __init__(self, engine) -> None:
        self.pe = engine
        self.start: Optional[Point] = None
        self.current: Optional[Point] = None
        self._points: List[Point] = []

    # -- lifecycle ------------------------------------------------------

    def begin(self, x: int, y: int) -> None:
        self.start = (x, y)
        self.current = (x, y)
        self._points = []

    def drag(self, x: int, y: int) -> None:
        if self.start is None:
            raise ToolError("drag() before begin()")
        self.current = (x, y)

    def end(self, x: int, y: int) -> None:
        if self.start is None:
            raise ToolError("end() before begin()")
        self.current = (x, y)

    @property
    def active(self) -> bool:
        return self.start is not None

    def reset(self) -> None:
        self.start = None
        self.current = None
        self._points = []

    # -- output ---------------------------------------------------------

    def points(self) -> List[Point]:
        """What to paint right now - the live preview."""
        raise NotImplementedError

    def commit_points(self) -> List[Point]:
        """What to write when the stroke ends."""
        return self.points()

    def describe(self) -> str:
        """One line for the info bar."""
        if self.start is None:
            return "Ready"
        x0, y0 = self.start
        x1, y1 = self.current or self.start
        return f"{self.name}: ({x0},{y0}) -> ({x1},{y1})"


class BrushTool(Tool):
    """Freehand, 1px, pixel-perfect.

    The path accumulates every pixel the pointer visits. Because the mouse can
    jump several pixels between events, each new position is JOINED to the
    previous one with a Bresenham line, otherwise fast strokes come out
    dotted.
    """

    name = "brush"

    def __init__(self, engine, perfect: bool = True) -> None:
        super().__init__(engine)
        self.perfect = perfect
        self._path: List[Point] = []
        self._seen: set = set()

    def begin(self, x: int, y: int) -> None:
        super().begin(x, y)
        self._path = [(x, y)]
        self._seen = {(x, y)}

    def drag(self, x: int, y: int) -> None:
        if self.start is None:
            raise ToolError("drag() before begin()")
        last = self._path[-1] if self._path else (x, y)
        # Bridge the gap so a fast stroke is continuous.
        for p in self.pe.line(last, (x, y)):
            if p not in self._seen:
                self._seen.add(p)
                self._path.append(p)
        self.current = (x, y)

    def end(self, x: int, y: int) -> None:
        self.drag(x, y)

    def points(self) -> List[Point]:
        """The whole stroke so far, filtered when pixel-perfect is on.

        Filtering the WHOLE path each time (rather than only the new pixels)
        is what makes the preview match the committed result: the pass looks
        at neighbours, so a pixel that was kept mid-stroke can become
        redundant once the next segment arrives.
        """
        if not self._path:
            return []
        if self.perfect:
            return self.pe.pixel_perfect(self._path)
        return list(self._path)

    def stroke_length(self) -> int:
        return len(self._path)


class LineTool(Tool):
    """Straight line between press and release."""

    name = "line"

    def __init__(self, engine, perfect: bool = True) -> None:
        super().__init__(engine)
        self.perfect = perfect

    def points(self) -> List[Point]:
        if self.start is None:
            return []
        a = self.start
        b = self.current or self.start
        return self.pe.stroke(a, b, perfect=self.perfect)


class BezierTool(Tool):
    """Cubic Bezier from `start` to `current`, with two control points.

    The control points are given relative to the endpoints so the curve keeps
    its shape while the user drags the end point around.
    """

    name = "bezier"

    DEFAULT_K1 = (0.35, 1.0)   # fraction of the drag vector, x and y
    DEFAULT_K2 = (0.65, -0.6)

    def __init__(self, engine, k1=None, k2=None) -> None:
        super().__init__(engine)
        self.k1 = k1 or self.DEFAULT_K1
        self.k2 = k2 or self.DEFAULT_K2

    def control_points(self):
        """Returns (p0, p1, p2, p3) in image coordinates, integers."""
        if self.start is None:
            return None
        p0 = self.start
        p3 = self.current or self.start
        dx = p3[0] - p0[0]
        dy = p3[1] - p0[1]
        p1 = (int(round(p0[0] + dx * self.k1[0] + dy * 0.0)),
              int(round(p0[1] + dy * self.k1[1])))
        p2 = (int(round(p0[0] + dx * self.k2[0])),
              int(round(p0[1] + dy * self.k2[1])))
        return (p0, p1, p2, p3)

    def points(self) -> List[Point]:
        ctrl = self.control_points()
        if ctrl is None:
            return []
        return self.pe.bezier(*ctrl)


class SquareTool(Tool):
    """Rectangle from press to release. Filled or outline."""

    name = "square"

    def __init__(self, engine, filled: bool = False) -> None:
        super().__init__(engine)
        self.filled = filled

    def points(self) -> List[Point]:
        if self.start is None:
            return []
        a = self.start
        b = self.current or self.start
        if self.filled:
            return self.pe.rectangle_filled(a, b)
        return self.pe.rectangle(a, b)


class CircleTool(Tool):
    """Ellipse inscribed in the press-release box. Filled or outline.

    The engine uses Zingl's corrected integer rasterizer, which is exact for
    even diameters and flat ellipses - the "improved circles" the original
    plugin advertises. Hold shift (handled by the canvas) for a square box.
    """

    name = "circle"

    def __init__(self, engine, filled: bool = False) -> None:
        super().__init__(engine)
        self.filled = filled

    def points(self) -> List[Point]:
        if self.start is None:
            return []
        a = self.start
        b = self.current or self.start
        if self.filled:
            return self.pe.ellipse_filled(a, b)
        return self.pe.ellipse(a, b)


def make_tool(name: str, engine, **options) -> Tool:
    """Builds the tool for a toolbar id, applying the matching options."""
    filled = bool(options.get("filled", False))
    perfect = bool(options.get("perfect", True))

    if name == TOOL_BRUSH:
        return BrushTool(engine, perfect=perfect)
    if name == TOOL_LINE:
        return LineTool(engine, perfect=perfect)
    if name == TOOL_BEZIER:
        return BezierTool(engine)
    if name == TOOL_SQUARE:
        return SquareTool(engine, filled=filled)
    if name == TOOL_CIRCLE:
        return CircleTool(engine, filled=filled)
    raise ToolError(f"Unknown tool {name!r}")


def uses_pixel_perfect(name: str) -> bool:
    return name in PERFECT_TOOLS
