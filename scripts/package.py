#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Builds the installable Krita plugin ZIP.

Krita's Python plugin importer expects, at the ROOT of the zip:
  - <name>.desktop
  - <name>/__init__.py  (and the rest of the module)
  - optionally <name>.action

Anything else (docs, CMake, the C++ core) is excluded: the importer copies
the module into Krita's pykrita folder, so shipping build files there would
just be clutter the user cannot use.
"""

import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "pykrita")
OUT_DIR = os.path.join(ROOT, "dist")
OUT = os.path.join(OUT_DIR, "pixeltools.zip")

INCLUDE = [
    "pixeltools.desktop",
    "pixeltools.action",
    "pixeltools/__init__.py",
    "pixeltools/pixeltools_docker.py",
    "pixeltools/painter.py",
    "pixeltools/pixel_engine.py",
]


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    missing = [p for p in INCLUDE if not os.path.exists(os.path.join(SRC, p))]
    if missing:
        print("missing files:", missing)
        return 1

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in INCLUDE:
            z.write(os.path.join(SRC, rel), rel)

    size = os.path.getsize(OUT)
    print(f"wrote {OUT} ({size} bytes, {len(INCLUDE)} files)")
    with zipfile.ZipFile(OUT) as z:
        for name in z.namelist():
            print("  ", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
