/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *
 *  pixel_engine.cpp - geometric rasterization core for pixeltools
 */

#include "pixel_engine.h"

#include <algorithm>
#include <cmath>
#include <string>

namespace pixeltools {

// ---------------------------------------------------------------------------
// PointAccumulator
// ---------------------------------------------------------------------------

long long PointAccumulator::keyOf(Point p)
{
    const unsigned long long ux = static_cast<unsigned int>(p.x);
    const unsigned long long uy = static_cast<unsigned int>(p.y);
    return static_cast<long long>((ux << 32) ^ uy);
}

PointAccumulator::PointAccumulator(std::size_t reserve)
{
    // Start at a power of two that leaves room without being wasteful.
    std::size_t cap = 64;
    while (cap < reserve * 2 && cap < (std::size_t(1) << 20)) {
        cap <<= 1;
    }
    m_keys.assign(cap, 0);
    m_used.assign(cap, 0);
    m_mask = cap - 1;
    m_points.reserve(reserve);
}

bool PointAccumulator::insertKey(long long key)
{
    const std::size_t mask = m_mask;
    // 64-bit multiplicative hash (Knuth).
    std::size_t idx = static_cast<std::size_t>(key * 11400714819323198485ULL) & mask;
    while (m_used[idx]) {
        if (m_keys[idx] == key) {
            return false; // already present
        }
        idx = (idx + 1) & mask;
    }
    m_used[idx] = 1;
    m_keys[idx] = key;
    ++m_size;
    return true;
}

void PointAccumulator::grow()
{
    std::vector<signed long long> oldKeys;
    std::vector<unsigned char> oldUsed;
    oldKeys.swap(m_keys);
    oldUsed.swap(m_used);

    const std::size_t cap = (m_mask + 1) * 2;
    m_keys.assign(cap, 0);
    m_used.assign(cap, 0);
    m_mask = cap - 1;
    m_size = 0;

    for (std::size_t i = 0; i < oldKeys.size(); ++i) {
        if (oldUsed[i]) {
            insertKey(oldKeys[i]);
        }
    }
}

bool PointAccumulator::add(Point p)
{
    // Keep the load factor at or below 50% so probing stays cheap.
    if ((m_size + 1) * 2 > m_mask + 1) {
        grow();
    }
    if (!insertKey(keyOf(p))) {
        return false;
    }
    m_points.push_back(p);
    return true;
}

void PointAccumulator::addPath(const Path &path)
{
    for (const Point &p : path) {
        add(p);
    }
}

Path PointAccumulator::take()
{
    Path out;
    out.swap(m_points);
    return out;
}

// ---------------------------------------------------------------------------
// Engine::line - Bresenham
// ---------------------------------------------------------------------------

/**
 * Bresenham from `a` to `b`, both endpoints included, emitted in the
 * order a -> b.
 *
 * The plain textbook loop is NOT invariant under swapping the endpoints:
 * where the ideal line passes exactly through a pixel corner, the
 * tie-break rule picks a different pixel depending on the direction of
 * travel. For a drawing tool that means the same stroke drawn backwards
 * lands on different pixels, which is unacceptable for pixel art.
 *
 * We therefore compute the path from the lexicographically smaller
 * endpoint (a canonical choice that does not depend on the caller's
 * argument order) and reverse the result when needed. The pixel SET is
 * then identical for a->b and b->a, while the output order still follows
 * the caller's request, so brushes that react to stroke direction keep
 * working.
 */
Path Engine::line(Point a, Point b)
{
    const bool reversed = (b.x < a.x) || (b.x == a.x && b.y < a.y);

    const Point from = reversed ? b : a;
    const Point to = reversed ? a : b;

    PointAccumulator acc;

    const int dx = iabs(to.x - from.x);
    const int dy = iabs(to.y - from.y);
    const int sx = (from.x < to.x) ? 1 : -1;
    const int sy = (from.y < to.y) ? 1 : -1;

    int x = from.x;
    int y = from.y;
    int err = dx - dy;

    while (true) {
        acc.add(Point{x, y});
        if (x == to.x && y == to.y) {
            break;
        }
        const int e2 = 2 * err;
        if (e2 > -dy) {
            err -= dy;
            x += sx;
        }
        if (e2 < dx) {
            err += dx;
            y += sy;
        }
    }

    Path out = acc.take();
    if (reversed) {
        std::reverse(out.begin(), out.end());
    }
    return out;
}

// ---------------------------------------------------------------------------
// Engine::ellipse - Zingl's integer rectangle ellipse
// ---------------------------------------------------------------------------
//
// Reference: Alois Zingl, "A Rasterizing Algorithm for Drawing Curves",
// plotEllipseRect(). This is the corrected midpoint algorithm: it produces a
// 4-way symmetric outline for both odd and even bounding box dimensions,
// where the textbook midpoint algorithm drops the last pixel on even sizes
// and flattens wrongly on very flat ellipses.
//
// Verified against the expected 7x5 pattern used by the acceptance test.

Path Engine::ellipse(Point a, Point b)
{
    PointAccumulator acc;

    int x0 = std::min(a.x, b.x);
    int y0 = std::min(a.y, b.y);
    int x1 = std::max(a.x, b.x);
    int y1 = std::max(a.y, b.y);

    long long aa = static_cast<long long>(x1 - x0); // width  - 1
    long long bb = static_cast<long long>(y1 - y0); // height - 1
    long long b1 = bb & 1;

    long long dx = 4LL * (1LL - aa) * bb * bb;
    long long dy = 4LL * (b1 + 1LL) * aa * aa;
    long long err = dx + dy + b1 * aa * aa;

    // Start from the middle of the left/right edges.
    y0 += static_cast<int>((bb + 1) / 2);
    y1 = y0 - static_cast<int>(b1);

    aa *= 8LL * aa;
    b1 = 8LL * bb * bb;

    do {
        acc.add(Point{x1, y0}); //   I. quadrant
        acc.add(Point{x0, y0}); //  II. quadrant
        acc.add(Point{x0, y1}); // III. quadrant
        acc.add(Point{x1, y1}); //  IV. quadrant

        const long long e2 = 2LL * err;
        if (e2 <= dy) {
            ++y0;
            --y1;
            dy += aa;
            err += dy;
        }
        if (e2 >= dx || 2LL * err > dy) {
            ++x0;
            --x1;
            dx += b1;
            err += dx;
        }
    } while (x0 <= x1);

    // Flat ellipses (aa == 1) stop too early above; finish the tips.
    while (y0 - y1 <= static_cast<long long>(bb)) {
        acc.add(Point{x0 - 1, y0});
        acc.add(Point{x1 + 1, y0});
        ++y0;
        acc.add(Point{x0 - 1, y1});
        acc.add(Point{x1 + 1, y1});
        --y1;
    }

    return acc.take();
}

Path Engine::ellipseFilled(Point a, Point b)
{
    return fillOutline(ellipse(a, b));
}

// ---------------------------------------------------------------------------
// Engine::rectangle
// ---------------------------------------------------------------------------

Path Engine::rectangle(Point a, Point b)
{
    PointAccumulator acc;

    const int x0 = std::min(a.x, b.x);
    const int y0 = std::min(a.y, b.y);
    const int x1 = std::max(a.x, b.x);
    const int y1 = std::max(a.y, b.y);

    acc.addPath(line(Point{x0, y0}, Point{x1, y0}));
    acc.addPath(line(Point{x1, y0}, Point{x1, y1}));
    acc.addPath(line(Point{x1, y1}, Point{x0, y1}));
    acc.addPath(line(Point{x0, y1}, Point{x0, y0}));

    return acc.take();
}

Path Engine::rectangleFilled(Point a, Point b)
{
    PointAccumulator acc;

    const int x0 = std::min(a.x, b.x);
    const int y0 = std::min(a.y, b.y);
    const int x1 = std::max(a.x, b.x);
    const int y1 = std::max(a.y, b.y);

    for (int y = y0; y <= y1; ++y) {
        for (int x = x0; x <= x1; ++x) {
            acc.add(Point{x, y});
        }
    }
    return acc.take();
}

// ---------------------------------------------------------------------------
// Engine::bezier
// ---------------------------------------------------------------------------

namespace {

Point cubicAt(Point p0, Point p1, Point p2, Point p3, double t)
{
    const double u = 1.0 - t;
    const double w0 = u * u * u;
    const double w1 = 3.0 * u * u * t;
    const double w2 = 3.0 * u * t * t;
    const double w3 = t * t * t;

    const double x = w0 * p0.x + w1 * p1.x + w2 * p2.x + w3 * p3.x;
    const double y = w0 * p0.y + w1 * p1.y + w2 * p2.y + w3 * p3.y;

    // Round half away from zero so the result is symmetric for mirrored
    // curves; std::lround does exactly that.
    return Point{static_cast<int>(std::lround(x)),
                 static_cast<int>(std::lround(y))};
}

double distance(Point a, Point b)
{
    const double dx = static_cast<double>(a.x) - static_cast<double>(b.x);
    const double dy = static_cast<double>(a.y) - static_cast<double>(b.y);
    return std::sqrt(dx * dx + dy * dy);
}

} // namespace

Path Engine::bezier(Point p0, Point p1, Point p2, Point p3,
                    double samplesPerUnit)
{
    PointAccumulator acc;

    if (samplesPerUnit <= 0.0) {
        samplesPerUnit = 1.0;
    }

    // The control polygon length bounds the curve length; sampling once per
    // polygon unit keeps every segment shorter than ~1 pixel, so joining the
    // samples with Bresenham yields a continuous (8-connected) path.
    const double polygonLength =
        distance(p0, p1) + distance(p1, p2) + distance(p2, p3);

    int steps = static_cast<int>(std::ceil(polygonLength * samplesPerUnit));
    if (steps < 2) {
        steps = 2;
    }
    // Cap so a pathological control polygon cannot hang the UI thread.
    const int kMaxSteps = 100000;
    if (steps > kMaxSteps) {
        steps = kMaxSteps;
    }

    Point prev = p0;
    acc.add(prev);

    for (int i = 1; i <= steps; ++i) {
        const double t = static_cast<double>(i) / static_cast<double>(steps);
        const Point cur = cubicAt(p0, p1, p2, p3, t);

        // Connect consecutive rounded samples so the curve cannot have holes.
        acc.addPath(line(prev, cur));
        prev = cur;
    }

    return acc.take();
}

// ---------------------------------------------------------------------------
// Engine::thicken
// ---------------------------------------------------------------------------

Path Engine::thicken(const Path &src, int radius)
{
    if (radius <= 0) {
        return src;
    }

    PointAccumulator acc(src.size() * 4);

    // Precompute the disc offsets once; a disc is the brush stamp shape.
    std::vector<Point> disc;
    const int r2 = radius * radius;
    for (int dy = -radius; dy <= radius; ++dy) {
        for (int dx = -radius; dx <= radius; ++dx) {
            if (dx * dx + dy * dy <= r2) {
                disc.push_back(Point{dx, dy});
            }
        }
    }

    for (const Point &p : src) {
        for (const Point &o : disc) {
            acc.add(Point{p.x + o.x, p.y + o.y});
        }
    }
    return acc.take();
}

// ---------------------------------------------------------------------------
// Engine::fillOutline
// ---------------------------------------------------------------------------

Path Engine::fillOutline(const Path &outline)
{
    if (outline.empty()) {
        return Path();
    }

    const Rect r = bounds(outline);
    PointAccumulator acc(outline.size() * 2);

    // Keep the outline pixels exactly as they are, preserving order.
    acc.addPath(outline);

    // Group outline x-coordinates per row.
    std::vector<std::vector<int>> rows(static_cast<std::size_t>(r.height()));
    for (const Point &p : outline) {
        if (p.y >= r.top && p.y <= r.bottom) {
            rows[static_cast<std::size_t>(p.y - r.top)].push_back(p.x);
        }
    }

    // Even-odd span fill between consecutive outline crossings. This is
    // correct for convex outlines (ellipse, rectangle), which is what the
    // tools produce.
    for (std::size_t i = 0; i < rows.size(); ++i) {
        std::vector<int> &xs = rows[i];
        if (xs.size() < 2) {
            continue;
        }
        std::sort(xs.begin(), xs.end());
        xs.erase(std::unique(xs.begin(), xs.end()), xs.end());

        const int y = r.top + static_cast<int>(i);
        for (std::size_t k = 0; k + 1 < xs.size(); k += 2) {
            for (int x = xs[k]; x <= xs[k + 1]; ++x) {
                acc.add(Point{x, y});
            }
        }
    }

    return acc.take();
}

// ---------------------------------------------------------------------------
// Engine::bounds / clip / toAscii
// ---------------------------------------------------------------------------

Rect Engine::bounds(const Path &path)
{
    if (path.empty()) {
        return Rect();
    }

    Rect r;
    r.left = r.right = path.front().x;
    r.top = r.bottom = path.front().y;

    for (const Point &p : path) {
        r.left = std::min(r.left, p.x);
        r.right = std::max(r.right, p.x);
        r.top = std::min(r.top, p.y);
        r.bottom = std::max(r.bottom, p.y);
    }
    return r;
}

Path Engine::clip(const Path &path, Rect r)
{
    if (r.empty()) {
        return Path();
    }
    Path out;
    out.reserve(path.size());
    for (const Point &p : path) {
        if (p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom) {
            out.push_back(p);
        }
    }
    return out;
}

std::vector<std::string> toAscii(const Path &path, Rect r)
{
    std::vector<std::string> out;
    if (r.empty()) {
        return out;
    }

    const int w = r.width();
    const int h = r.height();
    out.assign(static_cast<std::size_t>(h),
               std::string(static_cast<std::size_t>(w), '0'));

    // Mark in a set-free way: we only need membership, so build a lookup
    // grid. Bounded by the caller's rect, which is the test window.
    std::vector<unsigned char> grid(static_cast<std::size_t>(w) *
                                        static_cast<std::size_t>(h),
                                    0);
    for (const Point &p : path) {
        const int gx = p.x - r.left;
        const int gy = p.y - r.top;
        if (gx >= 0 && gx < w && gy >= 0 && gy < h) {
            grid[static_cast<std::size_t>(gy) * static_cast<std::size_t>(w) +
                 static_cast<std::size_t>(gx)] = 1;
        }
    }

    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            out[static_cast<std::size_t>(y)][static_cast<std::size_t>(x)] =
                grid[static_cast<std::size_t>(y) * static_cast<std::size_t>(w) +
                     static_cast<std::size_t>(x)]
                    ? '1'
                    : '0';
        }
    }
    return out;
}

} // namespace pixeltools
