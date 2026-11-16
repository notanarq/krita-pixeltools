/*
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *
 *  engine_oracle.cpp - dumps canonical shapes from the C++ engine.
 *
 *  The C++ engine is the specification; pykrita/pixeltools/pixel_engine.py
 *  is a port that has to agree with it pixel for pixel. This tool prints the
 *  reference answer for a fixed set of cases so the parity test can compare
 *  the two implementations without needing Qt or Krita.
 *
 *  Output format, one line per case:
 *      <kind> <args>\t<x>:<y>,<x>:<y>,...
 */

#include "pixel_engine.h"

#include <cstdio>
#include <string>
#include <vector>

using namespace pixeltools;

namespace {

void emit(const std::string &key, const Path &path)
{
    std::printf("%s\t", key.c_str());
    for (std::size_t i = 0; i < path.size(); ++i) {
        if (i) {
            std::printf(",");
        }
        std::printf("%d:%d", path[i].x, path[i].y);
    }
    std::printf("\n");
}

std::string boxKey(const char *kind, int w, int h)
{
    return std::string(kind) + " 0,0," + std::to_string(w - 1) + "," +
           std::to_string(h - 1);
}

} // namespace

int main(int argc, char **argv)
{
    if (argc < 2 || std::string(argv[1]) != "--dump") {
        std::printf("usage: %s --dump\n", argv[0]);
        return 2;
    }

    // Ellipses for every small box, odd and even, square and flat.
    for (int w = 1; w < 20; ++w) {
        for (int h = 1; h < 20; ++h) {
            emit(boxKey("ellipse", w, h),
                 Engine::ellipse(Point{0, 0}, Point{w - 1, h - 1}));
        }
    }

    // Lines in every direction, including negatives.
    for (int dx = -12; dx <= 12; ++dx) {
        for (int dy = -12; dy <= 12; ++dy) {
            emit("line 0,0," + std::to_string(dx) + "," + std::to_string(dy),
                 Engine::line(Point{0, 0}, Point{dx, dy}));
        }
    }

    // Beziers, including degenerate and collinear control polygons.
    struct Bez {
        Point p0, p1, p2, p3;
    };
    const std::vector<Bez> beziers = {
        {{0, 0}, {3, 10}, {12, -6}, {16, 4}},
        {{0, 0}, {5, 0}, {10, 0}, {15, 0}},
        {{2, 2}, {2, 2}, {2, 2}, {2, 2}},
        {{-4, 9}, {11, -3}, {-8, -7}, {6, 12}},
    };
    for (const Bez &b : beziers) {
        char buf[256];
        std::snprintf(buf, sizeof(buf), "bezier %d,%d %d,%d %d,%d %d,%d",
                      b.p0.x, b.p0.y, b.p1.x, b.p1.y, b.p2.x, b.p2.y,
                      b.p3.x, b.p3.y);
        emit(buf, Engine::bezier(b.p0, b.p1, b.p2, b.p3));
    }

    // Filled ellipses.
    const int boxes[][2] = {{7, 5}, {6, 6}, {8, 8}, {11, 4}, {12, 12}};
    for (const auto &b : boxes) {
        emit(boxKey("ellipseFilled", b[0], b[1]),
             Engine::ellipseFilled(Point{0, 0}, Point{b[0] - 1, b[1] - 1}));
    }

    // Thickened single pixels (the brush disc stamp).
    for (int r = 0; r < 4; ++r) {
        emit("thicken 0,0," + std::to_string(r),
             Engine::thicken(Path{{0, 0}}, r));
    }

    return 0;
}
