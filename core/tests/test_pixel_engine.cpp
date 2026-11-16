/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *
 *  test_pixel_engine.cpp - unit tests for the rasterization core
 *
 *  No test framework on purpose: this must build with nothing but a C++17
 *  compiler, in Krita's CI or in a phone-sized sandbox. Exit code 0 means
 *  every assertion passed.
 */

#include "pixel_engine.h"

#include <cstdio>
#include <string>
#include <vector>

using namespace pixeltools;

namespace {

int g_failures = 0;
int g_checks = 0;

void check(bool cond, const std::string &what)
{
    ++g_checks;
    if (!cond) {
        ++g_failures;
        std::printf("  FAIL: %s\n", what.c_str());
    }
}

void checkEq(const Path &got, const Path &want, const std::string &what)
{
    ++g_checks;
    if (got == want) {
        return;
    }
    ++g_failures;
    std::printf("  FAIL: %s\n", what.c_str());
    std::printf("    expected %zu points, got %zu\n", want.size(), got.size());
}

void checkAscii(const Path &path, Rect r, const std::vector<std::string> &want,
                const std::string &what)
{
    const std::vector<std::string> got = toAscii(path, r);
    ++g_checks;
    if (got == want) {
        return;
    }
    ++g_failures;
    std::printf("  FAIL: %s\n", what.c_str());
    const Rect b = Engine::bounds(path);
    std::printf("    bounds: (%d,%d)-(%d,%d)\n", b.left, b.top, b.right, b.bottom);
    const std::size_t rows = std::max(got.size(), want.size());
    for (std::size_t i = 0; i < rows; ++i) {
        const std::string g = i < got.size() ? got[i] : "<missing>";
        const std::string w = i < want.size() ? want[i] : "<missing>";
        std::printf("    %-20s %-20s %s\n", g.c_str(), w.c_str(),
                    g == w ? "" : "<-- differs");
    }
}

bool hasPoint(const Path &p, int x, int y)
{
    for (const Point &q : p) {
        if (q.x == x && q.y == y) {
            return true;
        }
    }
    return false;
}

bool hasDuplicates(const Path &p)
{
    for (std::size_t i = 0; i < p.size(); ++i) {
        for (std::size_t k = i + 1; k < p.size(); ++k) {
            if (p[i] == p[k]) {
                return true;
            }
        }
    }
    return false;
}

/**
 * Max-norm step between consecutive pixels. A rasterized path with no
 * visible holes must be 8-connected: every step is at most 1 in each axis.
 */
bool isEightConnected(const Path &p)
{
    for (std::size_t i = 1; i < p.size(); ++i) {
        const int dx = iabs(p[i].x - p[i - 1].x);
        const int dy = iabs(p[i].y - p[i - 1].y);
        if (dx > 1 || dy > 1) {
            return false;
        }
    }
    return true;
}

void section(const char *name)
{
    std::printf("\n[%s]\n", name);
}

// ---------------------------------------------------------------------------
// The acceptance test from the project spec, verbatim.
// ---------------------------------------------------------------------------

void testAcceptanceEllipse()
{
    section("ACCEPTANCE: ellipse 7x5 matches the expected pattern");

    // The 7x5 box (0,0)-(6,4) is what produces the pattern in the spec.
    const Path p = Engine::ellipse(Point{0, 0}, Point{6, 4});

    const std::vector<std::string> want = {
        "0011100",
        "0100010",
        "1000001",
        "0100010",
        "0011100",
    };

    checkAscii(p, Rect{0, 0, 6, 4}, want, "ellipse (0,0)-(6,4)");

    // Sanity properties that must hold for any correct rasterization.
    check(!hasDuplicates(p), "ellipse has no duplicate pixels");
    check(Engine::bounds(p) == (Rect{0, 0, 6, 4}),
          "ellipse touches all four sides of its box");
}

void testEllipseSymmetry()
{
    section("ellipse: 4-way symmetry on many box sizes");

    for (int w = 2; w <= 25; ++w) {
        for (int h = 2; h <= 25; ++h) {
            const Path p = Engine::ellipse(Point{0, 0}, Point{w - 1, h - 1});
            const Rect b = Engine::bounds(p);

            // Touches the box: a correct inscribed ellipse reaches all edges.
            const bool touches = (b.left == 0 && b.right == w - 1 &&
                                  b.top == 0 && b.bottom == h - 1);
            check(touches, "box touch " + std::to_string(w) + "x" +
                               std::to_string(h));
            check(!hasDuplicates(p), "no dups " + std::to_string(w) + "x" +
                                         std::to_string(h));

            // Every point must have its three mirrors present for a
            // symmetric ellipse centered on the box center.
            for (const Point &q : p) {
                const int mx = (w - 1) - q.x;
                const int my = (h - 1) - q.y;
                check(hasPoint(p, mx, q.y), "h-mirror " + std::to_string(w) +
                                                "x" + std::to_string(h));
                check(hasPoint(p, q.x, my), "v-mirror " + std::to_string(w) +
                                                "x" + std::to_string(h));
                check(hasPoint(p, mx, my), "both-mirror " + std::to_string(w) +
                                               "x" + std::to_string(h));
                if (g_failures > 40) {
                    return; // do not spam thousands of lines
                }
            }
        }
    }
}

void testEllipseEvenSizes()
{
    section("ellipse: even diameters keep the pattern the naive midpoint loses");

    // 6x6 is the classic case where the textbook midpoint algorithm
    // produces a flattened shape. Zingl's yields a proper circle.
    const Path p6 = Engine::ellipse(Point{0, 0}, Point{5, 5});
    const std::vector<std::string> want6 = {
        "001100",
        "010010",
        "100001",
        "100001",
        "010010",
        "001100",
    };
    checkAscii(p6, Rect{0, 0, 5, 5}, want6, "ellipse 6x6");

    const Path p8 = Engine::ellipse(Point{0, 0}, Point{7, 7});
    const std::vector<std::string> want8 = {
        "00111100",
        "01000010",
        "10000001",
        "10000001",
        "10000001",
        "10000001",
        "01000010",
        "00111100",
    };
    checkAscii(p8, Rect{0, 0, 7, 7}, want8, "ellipse 8x8");
}

void testEllipseFlat()
{
    section("ellipse: degenerate (very flat) boxes do not crash or gap");

    for (int w = 1; w <= 12; ++w) {
        const Path p = Engine::ellipse(Point{0, 0}, Point{w - 1, 0});
        check(!p.empty(), "flat ellipse w=" + std::to_string(w));
        check(!hasDuplicates(p), "flat no dups w=" + std::to_string(w));
    }
    // A 1x1 box is a single pixel.
    const Path dot = Engine::ellipse(Point{4, 4}, Point{4, 4});
    check(dot.size() == 1 && dot[0] == (Point{4, 4}), "1x1 box is one pixel");
}

void testEllipseFilled()
{
    section("ellipse: filled variant is solid");

    const Path outline = Engine::ellipse(Point{0, 0}, Point{6, 4});
    const Path filled = Engine::ellipseFilled(Point{0, 0}, Point{6, 4});

    check(filled.size() > outline.size(), "filled is larger than outline");
    check(!hasDuplicates(filled), "filled has no duplicates");

    // Center must be filled, and every outline pixel must be present.
    check(hasPoint(filled, 3, 2), "filled contains the center");
    for (const Point &p : outline) {
        check(hasPoint(filled, p.x, p.y), "filled contains outline pixel");
    }

    // Nothing may escape the bounding box.
    for (const Point &p : filled) {
        check(p.x >= 0 && p.x <= 6 && p.y >= 0 && p.y <= 4,
              "filled stays inside the box");
    }
}

void testLine()
{
    section("line: Bresenham is exact and 8-connected");

    checkEq(Engine::line(Point{0, 0}, Point{0, 0}), Path{{0, 0}},
            "single point");
    checkEq(Engine::line(Point{0, 0}, Point{3, 0}),
            (Path{{0, 0}, {1, 0}, {2, 0}, {3, 0}}), "horizontal");
    checkEq(Engine::line(Point{0, 0}, Point{0, 2}),
            (Path{{0, 0}, {0, 1}, {0, 2}}), "vertical");
    checkEq(Engine::line(Point{0, 0}, Point{3, 3}),
            (Path{{0, 0}, {1, 1}, {2, 2}, {3, 3}}), "45 degrees");

    // Reversal must produce exactly the same pixel set.
    for (int dx = -8; dx <= 8; ++dx) {
        for (int dy = -8; dy <= 8; ++dy) {
            const Path fwd = Engine::line(Point{0, 0}, Point{dx, dy});
            const Path rev = Engine::line(Point{dx, dy}, Point{0, 0});
            check(fwd.size() == rev.size(),
                  "reversal keeps length " + std::to_string(dx) + "," +
                      std::to_string(dy));
            for (const Point &p : fwd) {
                check(hasPoint(rev, p.x, p.y),
                      "reversal keeps pixels " + std::to_string(dx) + "," +
                          std::to_string(dy));
            }
            check(isEightConnected(fwd),
                  "8-connected " + std::to_string(dx) + "," + std::to_string(dy));
            check(!hasDuplicates(fwd), "no dups " + std::to_string(dx) + "," +
                                           std::to_string(dy));
            if (g_failures > 40) {
                return;
            }
        }
    }

    // The number of pixels of a Bresenham line is max(|dx|,|dy|)+1.
    const Path diag = Engine::line(Point{0, 0}, Point{10, 4});
    check(diag.size() == 11, "line length is max(|dx|,|dy|)+1");
    check(diag.front() == (Point{0, 0}) && diag.back() == (Point{10, 4}),
          "line endpoints are exact");
}

void testRectangle()
{
    section("rectangle: outline and filled");

    const Path out = Engine::rectangle(Point{0, 0}, Point{3, 2});
    check(!hasDuplicates(out), "outline has no duplicates");
    check(hasPoint(out, 0, 0) && hasPoint(out, 3, 0) && hasPoint(out, 0, 2) &&
              hasPoint(out, 3, 2),
          "outline has all four corners");
    check(!hasPoint(out, 1, 1), "outline does not contain the interior");

    const Path fill = Engine::rectangleFilled(Point{0, 0}, Point{3, 2});
    check(fill.size() == 4 * 3, "filled covers w*h pixels");
    check(hasPoint(fill, 1, 1), "filled contains the interior");

    // Swapped corners must give the same shape.
    const Path swapped = Engine::rectangle(Point{3, 2}, Point{0, 0});
    check(swapped.size() == out.size(), "corner order does not change size");
}

void testBezier()
{
    section("bezier: no holes, endpoints exact, deterministic");

    const Point p0{0, 0};
    const Point p1{3, 10};
    const Point p2{12, -6};
    const Point p3{16, 4};

    const Path c = Engine::bezier(p0, p1, p2, p3);

    check(!c.empty(), "curve is not empty");
    check(c.front() == p0, "curve starts exactly at p0");
    check(c.back() == p3, "curve ends exactly at p3");
    check(!hasDuplicates(c), "curve has no duplicates");
    check(isEightConnected(c), "curve is 8-connected (no holes)");

    // Same inputs, same output, every time.
    const Path c2 = Engine::bezier(p0, p1, p2, p3);
    checkEq(c, c2, "curve is deterministic");

    // A straight-line control polygon must rasterize to exactly the
    // equivalent Bresenham line, with no stray pixels.
    const Path straight =
        Engine::bezier(Point{0, 0}, Point{3, 0}, Point{6, 0}, Point{9, 0});
    checkEq(straight, Engine::line(Point{0, 0}, Point{9, 0}),
            "collinear control points collapse to a line");

    // Degenerate control points (all equal) must not hang or crash.
    const Path dot =
        Engine::bezier(Point{5, 5}, Point{5, 5}, Point{5, 5}, Point{5, 5});
    check(dot.size() == 1 && dot[0] == (Point{5, 5}), "degenerate curve is one pixel");

    // Higher density must still be hole-free.
    const Path dense = Engine::bezier(p0, p1, p2, p3, 4.0);
    check(isEightConnected(dense), "dense curve is 8-connected");
    check(dense.size() >= c.size(), "dense curve has at least as many pixels");
}

void testThicken()
{
    section("thicken: disc stamp is symmetric and grows monotonically");

    const Path dot = Engine::thicken(Path{{0, 0}}, 0);
    check(dot.size() == 1, "radius 0 is a no-op");

    const Path r1 = Engine::thicken(Path{{0, 0}}, 1);
    // A radius-1 disc is a plus shape: center + 4 orthogonal neighbours.
    check(r1.size() == 5, "radius 1 disc has 5 pixels");
    check(hasPoint(r1, 0, 0) && hasPoint(r1, 1, 0) && hasPoint(r1, -1, 0) &&
              hasPoint(r1, 0, 1) && hasPoint(r1, 0, -1),
          "radius 1 disc is a plus");

    const Path r2 = Engine::thicken(Path{{0, 0}}, 2);
    check(!hasDuplicates(r2), "radius 2 disc has no duplicates");
    check(hasPoint(r2, 2, 0) && hasPoint(r2, 0, 2), "radius 2 reaches 2 pixels");
    check(r2.size() > r1.size(), "radius 2 is larger than radius 1");

    // Thickening a line must contain the original line.
    const Path l = Engine::line(Point{0, 0}, Point{10, 10});
    const Path thick = Engine::thicken(l, 2);
    for (const Point &p : l) {
        check(hasPoint(thick, p.x, p.y), "thickened line contains the line");
    }
    check(!hasDuplicates(thick), "thickened line has no duplicates");
}

void testBoundsClip()
{
    section("bounds and clip");

    const Path p{{2, 3}, {5, 1}, {-1, 8}};
    check(Engine::bounds(p) == (Rect{-1, 1, 5, 8}), "bounds are correct");
    check(Engine::bounds(Path()).empty(), "empty path has empty bounds");

    const Path clipped = Engine::clip(p, Rect{0, 0, 4, 4});
    check(clipped.size() == 1 && clipped[0] == (Point{2, 3}), "clip keeps inside points");
    check(Engine::clip(p, Rect{}).empty(), "clip to empty rect drops everything");
}

} // namespace

int main()
{
    std::printf("pixeltools rasterization core - unit tests\n");

    testAcceptanceEllipse();
    testEllipseSymmetry();
    testEllipseEvenSizes();
    testEllipseFlat();
    testEllipseFilled();
    testLine();
    testRectangle();
    testBezier();
    testThicken();
    testBoundsClip();

    std::printf("\n----------------------------------------\n");
    std::printf("%d checks, %d failure(s)\n", g_checks, g_failures);

    if (g_failures == 0) {
        std::printf("RESULT: ALL TESTS PASSED\n");
        return 0;
    }
    std::printf("RESULT: TESTS FAILED\n");
    return 1;
}
