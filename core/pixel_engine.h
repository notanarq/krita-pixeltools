/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *
 *  pixel_engine.h - geometric rasterization core for pixeltools
 *
 *  Pure C++17, zero dependencies on Krita or Qt, so it can be built and
 *  unit-tested outside of Krita and later linked into a native tool.
 *
 *  Design rule: every coordinate in this file is an INTEGER pixel
 *  coordinate. No floating point ever decides where a pixel goes. The
 *  only float math is used to SAMPLE a Bezier, and its output is
 *  immediately rounded to integers and then connected with Bresenham,
 *  so the resulting path is a set of exact integer pixels.
 */

#ifndef PIXELTOOLS_PIXEL_ENGINE_H
#define PIXELTOOLS_PIXEL_ENGINE_H

#include <cstddef>
#include <string>
#include <vector>

namespace pixeltools {

/**
 * An integer pixel coordinate.
 *
 * Krita's canvas is a continuous coordinate space; a pixel (x, y) covers
 * the square [x, x+1) x [y, y+1). We work with the integer *index* of the
 * pixel, which is what makes the output reproducible.
 */
struct Point {
    int x {0};
    int y {0};

    bool operator==(const Point &o) const { return x == o.x && y == o.y; }
    bool operator!=(const Point &o) const { return !(*this == o); }
};

/**
 * An ordered list of unique pixels.
 *
 * Order matters for brushes that react to drawing direction, and
 * uniqueness guarantees no pixel is visited twice (no double-dab, no
 * over-accumulation of alpha at self-intersections).
 */
using Path = std::vector<Point>;

/** Axis-aligned integer rectangle, inclusive of both corners. */
struct Rect {
    int left {0};
    int top {0};
    int right {-1};
    int bottom {-1};

    bool empty() const { return right < left || bottom < top; }
    int width() const { return empty() ? 0 : right - left + 1; }
    int height() const { return empty() ? 0 : bottom - top + 1; }

    bool operator==(const Rect &o) const
    {
        if (empty() && o.empty()) {
            return true;
        }
        return left == o.left && top == o.top && right == o.right &&
               bottom == o.bottom;
    }
    bool operator!=(const Rect &o) const { return !(*this == o); }
};

/**
 * Order-preserving deduplicating point accumulator.
 *
 * Used internally so that a shape assembled from several sub-paths keeps
 * the draw order while never emitting the same pixel twice.
 */
class PointAccumulator {
public:
    explicit PointAccumulator(std::size_t reserve = 0);

    /** Adds the point if it was not added before. Returns true if added. */
    bool add(Point p);

    /** Appends a whole path, skipping points already present. */
    void addPath(const Path &path);

    const Path &points() const { return m_points; }
    Path take();

private:
    Path m_points;
    // Open-addressing hash set over (x, y) for O(1) dedup. We keep our own
    // because Point has no std::hash specialization and we do not want to
    // depend on <unordered_set> iteration order for anything.
    std::vector<signed long long> m_keys;
    std::vector<unsigned char> m_used;
    std::size_t m_mask {0};
    std::size_t m_size {0};

    void grow();
    bool insertKey(long long key);
    static long long keyOf(Point p);
};

/**
 * The rasterization engine.
 *
 * All methods are static and stateless: same input, same output, always.
 * Every output path is ordered, deduplicated, and contains only integer
 * pixel coordinates.
 */
class Engine {
public:
    /** Bresenham line from `a` to `b`, both endpoints included. */
    static Path line(Point a, Point b);

    /**
     * Ellipse outline inscribed in the bounding box defined by the two
     * opposite corners `a` and `b`.
     *
     * Uses Alois Zingl's integer ellipse rasterizer (the "plotEllipseRect"
     * variant), which is a corrected midpoint algorithm: it stays correct
     * for even diameters and for flat ellipses where the naive midpoint
     * algorithm drops pixels or doubles them.
     */
    static Path ellipse(Point a, Point b);

    /** Ellipse outline plus everything strictly inside it. */
    static Path ellipseFilled(Point a, Point b);

    /** Rectangle outline through the corners of the box `a`..`b`. */
    static Path rectangle(Point a, Point b);

    /** Filled rectangle covering the box `a`..`b`. */
    static Path rectangleFilled(Point a, Point b);

    /**
     * Cubic Bezier rasterized without holes.
     *
     * The curve is sampled at a density derived from the control polygon
     * length, the samples are rounded to integer pixels, and consecutive
     * samples are joined with Bresenham. The result is 8-connected by
     * construction: there is no visible gap between consecutive pixels.
     *
     * `samplesPerUnit` controls density; values below 0.5 start to show
     * chord error on tight curves, above 4 is usually wasted work.
     */
    static Path bezier(Point p0, Point p1, Point p2, Point p3,
                       double samplesPerUnit = 1.0);

    /**
     * Grows every point of `src` into a square-free disc of `radius`
     * pixels. radius 0 returns a copy of `src`.
     *
     * This is the "thickness" of a line: a disc stamp at each path pixel,
     * which is what a brush does, but here it is deterministic and integer.
     */
    static Path thicken(const Path &src, int radius);

    /**
     * Fills the interior of a closed outline with a scanline pass using
     * the even-odd rule. The outline pixels themselves are always kept.
     */
    static Path fillOutline(const Path &outline);

    /** Bounding box of a path. Empty path yields an empty rect. */
    static Rect bounds(const Path &path);

    /**
     * Clips a path to the rectangle `r`, dropping points outside it.
     * Order is preserved.
     */
    static Path clip(const Path &path, Rect r);
};

// ---------------------------------------------------------------------------
// Free helpers, kept out of the class so they are trivially testable.
// ---------------------------------------------------------------------------

/** Integer absolute value, avoiding <cstdlib>. */
inline int iabs(int v) { return v < 0 ? -v : v; }

/** Euclidean remainder that is always non-negative. */
inline int imod(int v, int m) { return ((v % m) + m) % m; }

/**
 * Renders a path as ASCII art inside `r`, one character per pixel, for
 * tests and for the plugin's dry-run preview.
 * Points in the path become '1', everything else '0'.
 */
std::vector<std::string> toAscii(const Path &path, Rect r);

} // namespace pixeltools

#endif // PIXELTOOLS_PIXEL_ENGINE_H
