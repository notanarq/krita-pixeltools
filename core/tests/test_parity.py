# SPDX-License-Identifier: GPL-2.0-or-later
"""
test_parity.py - checks the Python port against the C++ reference.

The C++ engine is the specification; the Python module exists because the
plugin has to import into Krita as a plain ZIP, without shipping compiled
binaries. That is only safe if both implementations produce the SAME pixels,
so this test drives both and compares them exhaustively.

Usage:
    python3 core/tests/test_parity.py [path/to/oracle binary]

If the oracle binary is missing, the test still runs the Python-only
assertions and reports that parity was not checked.
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# Import the engine module DIRECTLY, not through the package: the package's
# __init__.py registers the docker and therefore imports `krita`, which does
# not exist outside Krita. The engine itself has no Krita dependency, and
# keeping it importable on its own is what makes this test possible.
import importlib.util  # noqa: E402

_ENGINE_PATH = os.path.join(ROOT, "pykrita", "pixeltools", "pixel_engine.py")
_spec = importlib.util.spec_from_file_location("pixel_engine", _ENGINE_PATH)
pe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pe)

failures: list[str] = []
checks = 0


def check(cond: bool, what: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(what)


# ---------------------------------------------------------------------------
# The acceptance pattern from the project spec
# ---------------------------------------------------------------------------

EXPECTED_7x5 = [
    "0011100",
    "0100010",
    "1000001",
    "0100010",
    "0011100",
]


def test_acceptance() -> None:
    got = pe.to_ascii(pe.ellipse((0, 0), (6, 4)), (0, 0, 6, 4))
    check(got == EXPECTED_7x5, "acceptance ellipse 7x5")


def test_properties() -> None:
    for w in range(2, 22):
        for h in range(2, 22):
            p = pe.ellipse((0, 0), (w - 1, h - 1))
            b = pe.bounds(p)
            check(b == (0, 0, w - 1, h - 1), f"ellipse touches box {w}x{h}")
            check(len(p) == len(set(p)), f"ellipse no dup {w}x{h}")
            pts = set(p)
            for x, y in p:
                mx, my = (w - 1) - x, (h - 1) - y
                check((mx, y) in pts, f"h-mirror {w}x{h}")
                check((x, my) in pts, f"v-mirror {w}x{h}")
                check((mx, my) in pts, f"both-mirror {w}x{h}")

    # Lines must be direction independent as a SET.
    for dx in range(-10, 11):
        for dy in range(-10, 11):
            fwd = pe.line((0, 0), (dx, dy))
            rev = pe.line((dx, dy), (0, 0))
            check(set(fwd) == set(rev), f"line set symmetric {dx},{dy}")
            check(len(fwd) == max(abs(dx), abs(dy)) + 1, f"line len {dx},{dy}")
            for i in range(1, len(fwd)):
                sx = abs(fwd[i][0] - fwd[i - 1][0])
                sy = abs(fwd[i][1] - fwd[i - 1][1])
                check(sx <= 1 and sy <= 1, f"line 8-connected {dx},{dy}")

    # Bezier: endpoints exact, no holes.
    p = pe.bezier((0, 0), (3, 10), (12, -6), (16, 4))
    check(p[0] == (0, 0), "bezier starts at p0")
    check(p[-1] == (16, 4), "bezier ends at p3")
    check(len(p) == len(set(p)), "bezier no dup")
    for i in range(1, len(p)):
        sx = abs(p[i][0] - p[i - 1][0])
        sy = abs(p[i][1] - p[i - 1][1])
        check(sx <= 1 and sy <= 1, "bezier 8-connected")

    # Filled ellipse contains its outline and its center.
    out = pe.ellipse((0, 0), (6, 4))
    fil = pe.ellipse_filled((0, 0), (6, 4))
    check(set(out).issubset(set(fil)), "filled contains outline")
    check((3, 2) in set(fil), "filled contains center")


# ---------------------------------------------------------------------------
# Parity against the C++ oracle
# ---------------------------------------------------------------------------


def run_oracle(binary: str) -> dict[str, str]:
    """Asks the C++ binary for canonical shapes and returns its answers."""
    proc = subprocess.run(
        [binary, "--dump"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or "oracle failed")

    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "\t" in line:
            key, value = line.split("\t", 1)
            out[key] = value
    return out


def test_parity(binary: str) -> None:
    oracle = run_oracle(binary)

    def compare(key: str, py_points: list[tuple[int, int]]) -> None:
        want = oracle.get(key)
        check(want is not None, f"oracle has {key}")
        if want is None:
            return
        got = ",".join(f"{x}:{y}" for x, y in py_points)
        check(got == want, f"parity {key}")

    # Ellipses, all small boxes.
    for w in range(1, 20):
        for h in range(1, 20):
            key = f"ellipse 0,0,{w - 1},{h - 1}"
            compare(key, pe.ellipse((0, 0), (w - 1, h - 1)))

    # Lines, including negative directions.
    for dx in range(-12, 13):
        for dy in range(-12, 13):
            key = f"line 0,0,{dx},{dy}"
            compare(key, pe.line((0, 0), (dx, dy)))

    # Beziers.
    for pts in [
        ((0, 0), (3, 10), (12, -6), (16, 4)),
        ((0, 0), (5, 0), (10, 0), (15, 0)),
        ((2, 2), (2, 2), (2, 2), (2, 2)),
        ((-4, 9), (11, -3), (-8, -7), (6, 12)),
    ]:
        key = "bezier " + " ".join(f"{x},{y}" for x, y in pts)
        compare(key, pe.bezier(*pts))

    # Filled shapes and thick lines.
    for w, h in [(7, 5), (6, 6), (8, 8), (11, 4), (12, 12)]:
        key = f"ellipseFilled 0,0,{w - 1},{h - 1}"
        compare(key, pe.ellipse_filled((0, 0), (w - 1, h - 1)))

    for r in range(0, 4):
        key = f"thicken 0,0,{r}"
        compare(key, pe.thicken([(0, 0)], r))


def main() -> int:
    print("pixeltools - Python engine tests")

    test_acceptance()
    test_properties()

    if len(sys.argv) > 1:
        binary = sys.argv[1]
        if os.path.exists(binary):
            print(f"\nParity check against C++ oracle: {binary}")
            test_parity(binary)
        else:
            print(f"\nOracle not found at {binary}; skipping parity check")
    else:
        # Look for the oracle next to the test, then in the build dir.
        for candidate in [
            os.path.join(HERE, "engine_oracle"),
            os.path.join(ROOT, "build", "engine_oracle"),
        ]:
            if os.path.exists(candidate):
                print(f"\nParity check against C++ oracle: {candidate}")
                test_parity(candidate)
                break
        else:
            print("\nNo oracle binary found; Python-only checks ran")

    print("-" * 40)
    if failures:
        print(f"{checks} checks, {len(failures)} FAILURE(S)")
        for f in failures[:40]:
            print("  FAIL:", f)
        return 1

    print(f"{checks} checks, 0 failures")
    print("RESULT: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
