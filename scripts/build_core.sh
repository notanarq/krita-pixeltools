#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Builds and runs the dependency-free rasterization core tests, then the
# C++/Python parity test. No Krita and no Qt required.
#
#   ./scripts/build_core.sh            # uses g++/clang++ if present
#   CXX=zig ./scripts/build_core.sh    # or any C++17 compiler
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BUILD="$ROOT/build"
mkdir -p "$BUILD"

CXX_BIN="${CXX:-}"
if [ -z "$CXX_BIN" ]; then
    for c in g++ clang++ c++; do
        if command -v "$c" >/dev/null 2>&1 && "$c" --version >/dev/null 2>&1; then
            CXX_BIN="$c"; break
        fi
    done
fi
if [ -z "$CXX_BIN" ]; then
    # Fall back to any zig on PATH or a local zig checkout: `zig c++` is a
    # complete C++17 toolchain and needs no system packages.
    if command -v zig >/dev/null 2>&1; then
        CXX_BIN="zig c++"
    else
        ZIG_GUESS=$(command -v zig 2>/dev/null || true)
    fi
fi
if [ -z "$CXX_BIN" ]; then
    echo "no C++ compiler found; set CXX (e.g. CXX=g++ or CXX='zig c++')" >&2
    exit 1
fi

echo "==> compiler: $CXX_BIN"
$CXX_BIN -std=c++17 -O2 -Wall -Wextra -Icore \
    -o "$BUILD/pixel_engine_tests" core/pixel_engine.cpp core/tests/test_pixel_engine.cpp
$CXX_BIN -std=c++17 -O2 -Icore \
    -o "$BUILD/engine_oracle" tools/engine_oracle.cpp core/pixel_engine.cpp

echo "==> unit tests"
"$BUILD/pixel_engine_tests"

echo "==> python parity test"
python3 core/tests/test_parity.py "$BUILD/engine_oracle"
