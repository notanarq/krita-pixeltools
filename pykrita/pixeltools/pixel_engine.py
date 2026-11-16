# SPDX-License-Identifier: GPL-2.0-or-later
"""
pixel_engine.py - pure-Python port of the C++ rasterization core.

This module is deliberately free of any Krita import so it can be tested
with plain Python. It mirrors core/pixel_engine.cpp one function at a time;
the C++ unit tests and core/tests/test_parity.py check that both
implementations agree pixel for pixel.

Every coordinate is an INTEGER pixel coordinate. Floats appear only when
sampling a Bezier, and their result is rounded to int before being joined
with the integer line rasterizer, so no float ever decides where a pixel
lands.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

Point = Tuple[int, int]


# ---------------------------------------------------------------------------
# Ordered deduplicating accumulator
# ---------------------------------------------------------------------------


class PointAccumulator:
    """Collects points keeping insertion order, silently dropping repeats.

    A pixel must be painted once per stroke: painting the same pixel twice
    with a semi-transparent brush would darken it, and would make the
    result depend on the order the shape was assembled.
    """

    __slots__ = ("_points", "_seen")

    def __init__(self, reserve: int = 0) -> None:
        self._points: List[Point] = []
        self._seen: set[Point] = set()

    def add(self, p: Point) -> bool:
        if p in self._seen:
            return False
        self._seen.add(p)
        self._points.append(p)
        return True

    def add_path(self, path: Iterable[Point]) -> None:
        for p in path:
            self.add(p)

    def points(self) -> List[Point]:
        return list(self._points)


# ---------------------------------------------------------------------------
# Line - Bresenham, canonicalized so a->b and b->a give the same pixel set
# ---------------------------------------------------------------------------


def line(a: Point, b: Point) -> List[Point]:
    """Bresenham line from a to b, both endpoints included.

    The plain textbook loop is not invariant under swapping the endpoints:
    where the ideal line passes exactly through a pixel corner the tie-break
    picks a different pixel depending on travel direction. We therefore
    rasterize from the lexicographically smaller endpoint and reverse
    afterwards, so the pixel SET is direction independent while the emitted
    ORDER still follows the caller (brushes may react to direction).
    """
    reversed_order = (b[0] < a[0]) or (b[0] == a[0] and b[1] < a[1])
    start, end = (b, a) if reversed_order else (a, b)

    dx = abs(end[0] - start[0])
    dy = abs(end[1] - start[1])
    sx = 1 if start[0] < end[0] else -1
    sy = 1 if start[1] < end[1] else -1

    x, y = start
    err = dx - dy

    acc = PointAccumulator()
    while True:
        acc.add((x, y))
        if x == end[0] and y == end[1]:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy

    out = acc.points()
    if reversed_order:
        out.reverse()
    return out


def is_connected_chain(path: Sequence[Point]) -> bool:
    """True when each point is 8-adjacent to the previous one.

    The pixel-perfect pass compares path[c-1] and path[c+1] as if they were
    the two neighbours of path[c] IN THE IMAGE. That is only true when the
    sequence is a walk through adjacent pixels.

    Rasterizers that emit symmetric points quadratically (the ellipse emits
    its four quadrants at once) produce a SET, not a walk: consecutive
    entries can be on opposite sides of the shape. Running the pass on such a
    list deletes structural pixels - an 11x11 circle of 28 px collapses to 21
    with visible holes.

    So the pass is guarded by this check instead of being applied blindly.
    """
    for i in range(1, len(path)):
        dx = abs(path[i][0] - path[i - 1][0])
        dy = abs(path[i][1] - path[i - 1][1])
        if dx > 1 or dy > 1:
            return False
    return True


def pixel_perfect(path: Sequence[Point]) -> List[Point]:
    """Removes the redundant corner pixel of every L-shape in a freehand path.

    This is the canonical "pixel perfect" pass that every serious pixel art
    editor applies to pencil strokes (Graphics Gale, Aseprite, Hexels). A
    freehand path taken from the mouse is a chain of single-pixel steps, so a
    diagonal movement leaves a stair with a doubled corner:

        0 1 0        the pixel at (1,1) is adjacent to BOTH its neighbours,
        0 1 1   ->   so the stroke reads as 2px thick at the corner.
        0 0 1

    The rule: drop path[c] when its two neighbours each share exactly one axis
    with it (so it is an L-corner) AND those neighbours differ on BOTH axes
    (so the corner is redundant, not a genuine turn). Everything else is kept.

    WHEN TO USE IT - and when not to. This matters more than the algorithm:

      * FREEHAND / LINE strokes: use it. That is what it was invented for.
      * GEOMETRIC SHAPES: never. They are already exact. Filtering them does
        real damage, and both failure modes were measured:

          - the ellipse rasterizer emits its four quadrants at once, so
            consecutive entries are not neighbouring pixels; an 11x11 circle
            collapsed from 28 px to 21, with holes.
          - a rectangle and a bezier ARE connected chains, so they pass the
            guard below, but their corners are structural: a 7x5 rectangle
            lost all four corners and became a chamfered octagon.

    So the guard can only catch the disconnected case. Keeping shapes away
    from this function is the caller's job: tools.py decides, and only the
    brush and the line tool route through here.

    GUARD: requires a connected chain (each pixel adjacent to the previous
    one). A disconnected point set is returned unchanged, because "the
    previous entry" would not be a neighbouring pixel.
    """
    if len(path) < 3 or not is_connected_chain(path):
        return list(path)

    out: List[Point] = []
    c = 0
    n = len(path)

    while c < n:
        if c > 0 and c + 1 < n:
            prev = path[c - 1]
            cur = path[c]
            nxt = path[c + 1]

            # The corner pixel shares one axis with each neighbour...
            shares_axis_prev = prev[0] == cur[0] or prev[1] == cur[1]
            shares_axis_next = nxt[0] == cur[0] or nxt[1] == cur[1]
            # ...and the neighbours are diagonal to each other, which is what
            # makes this pixel redundant rather than structural.
            neighbours_diagonal = prev[0] != nxt[0] and prev[1] != nxt[1]

            if shares_axis_prev and shares_axis_next and neighbours_diagonal:
                c += 1  # skip: this is the redundant corner

        out.append(path[c])
        c += 1

    return out


def bresenham_points(a: Point, b: Point) -> List[Point]:
    """Every pixel Bresenham touches from a to b, WITHOUT the perfect pass.

    This is what a pencil tool needs: the raw staircase the pointer follows.
    `line()` already returns exactly that, kept here as a named alias so the
    intent is explicit where the two are used together.
    """
    return line(a, b)


def stroke(a: Point, b: Point, perfect: bool = True) -> List[Point]:
    """A LINE stroke between two points, optionally pixel-perfect.

    This is the only shape-producing helper that applies the pass, and it is
    a straight line, so the worst case is dropping a redundant stair corner.
    Geometric shapes are deliberately absent from this file's filter path.
    """
    raw = line(a, b)
    return pixel_perfect(raw) if perfect else raw


# ---------------------------------------------------------------------------
# Ellipse - Zingl's integer rasterizer (corrected midpoint)
# ---------------------------------------------------------------------------


def ellipse(a: Point, b: Point) -> List[Point]:
    """Ellipse outline inscribed in the box defined by corners a and b.

    Uses Alois Zingl's plotEllipseRect(), the corrected midpoint algorithm.
    The textbook midpoint ellipse drops the last pixel on even diameters and
    flattens badly on very thin ellipses; this one is symmetric for every
    box size, which is exactly what pixel art needs.

    NOTE: the four quadrants are emitted symmetrically, so consecutive
    entries in the returned list are NOT neighbouring pixels. That is fine
    for painting (order does not matter) but it is why this output must never
    be passed to pixel_perfect().
    """
    x0, x1 = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
    y0, y1 = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])

    aa = x1 - x0  # width - 1
    bb = y1 - y0  # height - 1
    b1 = bb & 1

    dx = 4 * (1 - aa) * bb * bb
    dy = 4 * (b1 + 1) * aa * aa
    err = dx + dy + b1 * aa * aa

    y0 += (bb + 1) // 2
    y1 = y0 - b1

    aa *= 8 * aa
    b1 = 8 * bb * bb

    acc = PointAccumulator()
    while True:
        acc.add((x1, y0))
        acc.add((x0, y0))
        acc.add((x0, y1))
        acc.add((x1, y1))

        e2 = 2 * err
        if e2 <= dy:
            y0 += 1
            y1 -= 1
            dy += aa
            err += dy
        if e2 >= dx or 2 * err > dy:
            x0 += 1
            x1 -= 1
            dx += b1
            err += dx
        if x0 > x1:
            break

    # Flat ellipses (aa == 1) stop early above; finish the tips.
    while y0 - y1 <= bb:
        acc.add((x0 - 1, y0))
        acc.add((x1 + 1, y0))
        y0 += 1
        acc.add((x0 - 1, y1))
        acc.add((x1 + 1, y1))
        y1 -= 1

    return acc.points()


def ellipse_filled(a: Point, b: Point) -> List[Point]:
    return fill_outline(ellipse(a, b))


# ---------------------------------------------------------------------------
# Rectangle
# ---------------------------------------------------------------------------


def rectangle(a: Point, b: Point) -> List[Point]:
    x0, x1 = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
    y0, y1 = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])

    acc = PointAccumulator()
    acc.add_path(line((x0, y0), (x1, y0)))
    acc.add_path(line((x1, y0), (x1, y1)))
    acc.add_path(line((x1, y1), (x0, y1)))
    acc.add_path(line((x0, y1), (x0, y0)))
    return acc.points()


def rectangle_filled(a: Point, b: Point) -> List[Point]:
    x0, x1 = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
    y0, y1 = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])

    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


# ---------------------------------------------------------------------------
# Bezier
# ---------------------------------------------------------------------------


def _cubic_at(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    u = 1.0 - t
    w0 = u * u * u
    w1 = 3.0 * u * u * t
    w2 = 3.0 * u * t * t
    w3 = t * t * t

    x = w0 * p0[0] + w1 * p1[0] + w2 * p2[0] + w3 * p3[0]
    y = w0 * p0[1] + w1 * p1[1] + w2 * p2[1] + w3 * p3[1]

    # Round half away from zero so mirrored curves stay mirrored. Python's
    # round() is banker's rounding, which would break that symmetry.
    def r(v: float) -> int:
        return int(math.floor(v + 0.5)) if v >= 0 else int(math.ceil(v - 0.5))

    return (r(x), r(y))


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bezier(
    p0: Point, p1: Point, p2: Point, p3: Point, samples_per_unit: float = 1.0
) -> List[Point]:
    """Cubic Bezier rasterized with no holes.

    Samples the curve at a density derived from the control polygon length,
    rounds each sample to an integer pixel and joins consecutive samples with
    Bresenham. The result is 8-connected by construction, so there is never a
    visible gap - the exact failure mode of drawing a curve by stamping a
    brush at floating-point positions.

    NOTE: unlike the ellipse this IS a connected chain, so it passes
    pixel_perfect's guard - but its corners are structural turn points, so the
    pass still must not be applied to it.
    """
    if samples_per_unit <= 0.0:
        samples_per_unit = 1.0

    polygon_length = _dist(p0, p1) + _dist(p1, p2) + _dist(p2, p3)
    steps = int(math.ceil(polygon_length * samples_per_unit))
    steps = max(2, min(steps, 100000))

    acc = PointAccumulator()
    prev = p0
    acc.add(prev)

    for i in range(1, steps + 1):
        t = i / steps
        cur = _cubic_at(p0, p1, p2, p3, t)
        acc.add_path(line(prev, cur))
        prev = cur

    return acc.points()


# ---------------------------------------------------------------------------
# Thicken / fill / bounds / clip
# ---------------------------------------------------------------------------


def thicken(path: Sequence[Point], radius: int) -> List[Point]:
    """Stamps a filled disc of `radius` pixels at every point of `path`."""
    if radius <= 0:
        return list(path)

    disc: List[Point] = []
    r2 = radius * radius
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy <= r2:
                disc.append((dx, dy))

    acc = PointAccumulator()
    for px, py in path:
        for ox, oy in disc:
            acc.add((px + ox, py + oy))
    return acc.points()


def fill_outline(outline: Sequence[Point]) -> List[Point]:
    """Fills the interior of a closed outline using an even-odd scanline."""
    if not outline:
        return []

    left, top, right, bottom = bounds(outline)

    acc = PointAccumulator()
    acc.add_path(outline)

    rows: dict = {}
    for x, y in outline:
        rows.setdefault(y, []).append(x)

    for y in range(top, bottom + 1):
        xs = sorted(set(rows.get(y, [])))
        if len(xs) < 2:
            continue
        for k in range(0, len(xs) - 1, 2):
            for x in range(xs[k], xs[k + 1] + 1):
                acc.add((x, y))

    return acc.points()


def bounds(path: Sequence[Point]) -> Tuple[int, int, int, int]:
    """Returns (left, top, right, bottom); an empty path gives (0,0,-1,-1)."""
    if not path:
        return (0, 0, -1, -1)

    xs = [p[0] for p in path]
    ys = [p[1] for p in path]
    return (min(xs), min(ys), max(xs), max(ys))


def clip(path: Sequence[Point], rect: Tuple[int, int, int, int]) -> List[Point]:
    left, top, right, bottom = rect
    if right < left or bottom < top:
        return []
    return [
        p
        for p in path
        if left <= p[0] <= right and top <= p[1] <= bottom
    ]


def to_ascii(path: Sequence[Point], rect: Tuple[int, int, int, int]) -> List[str]:
    """Renders a path as rows of '0'/'1' inside `rect`, for tests and previews."""
    left, top, right, bottom = rect
    if right < left or bottom < top:
        return []

    width = right - left + 1
    height = bottom - top + 1
    grid = [["0"] * width for _ in range(height)]

    for x, y in path:
        gx = x - left
        gy = y - top
        if 0 <= gx < width and 0 <= gy < height:
            grid[gy][gx] = "1"

    return ["".join(row) for row in grid]
