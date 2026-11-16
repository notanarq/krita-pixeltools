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


# ---------------------------------------------------------------------------
# Ellipse - Zingl's integer rasterizer (corrected midpoint)
# ---------------------------------------------------------------------------


def ellipse(a: Point, b: Point) -> List[Point]:
    """Ellipse outline inscribed in the box defined by corners a and b.

    Uses Alois Zingl's plotEllipseRect(), the corrected midpoint algorithm.
    The textbook midpoint ellipse drops the last pixel on even diameters and
    flattens badly on very thin ellipses; this one is symmetric for every
    box size, which is exactly what pixel art needs.
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

    rows: dict[int, List[int]] = {}
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
