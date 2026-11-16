#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Builds the installable Krita plugin ZIP.

Krita's Python plugin importer expects, at the ROOT of the zip:
  - <name>.desktop
  - <name>/__init__.py  (and the rest of the module)
  - optionally <name>.action

CRITICAL DETAIL: the importer finds the module by scanning the archive for
an entry that ENDS WITH "/<name>/" (a trailing slash), i.e. it looks for an
explicit DIRECTORY entry, not just for files under that prefix. A zip built
only from files ("pixeltools/__init__.py", ...) contains no such entry and
Krita answers "No plugins found in archive".

So the directory entry must be written explicitly before its files. This is
why the archive is built entry by entry instead of with ZipFile.write() on
the members alone.

Anything else (docs, CMake, the C++ core) is excluded: the importer copies
the module into Krita's pykrita folder, so shipping build files there would
just be clutter the user cannot use.
"""

import os
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "pykrita")
OUT_DIR = os.path.join(ROOT, "dist")
OUT = os.path.join(OUT_DIR, "pixeltools.zip")

# (zip entry name, path on disk or None for a directory entry)
ENTRIES = [
    ("pixeltools.desktop", "pixeltools.desktop"),
    ("pixeltools.action", "pixeltools.action"),
    ("pixeltools/", None),
    ("pixeltools/__init__.py", "pixeltools/__init__.py"),
    ("pixeltools/pixeltools_docker.py", "pixeltools/pixeltools_docker.py"),
    ("pixeltools/painter.py", "pixeltools/painter.py"),
    ("pixeltools/pixel_engine.py", "pixeltools/pixel_engine.py"),
]


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)

    missing = [
        rel for _, rel in ENTRIES if rel and not os.path.exists(os.path.join(SRC, rel))
    ]
    if missing:
        print("missing files:", missing)
        return 1

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for arcname, rel in ENTRIES:
            if rel is None:
                # Explicit directory entry. Without it Krita's importer cannot
                # resolve the module directory. See the note in the docstring.
                info = zipfile.ZipInfo(arcname)
                info.external_attr = (0o40755 << 16) | 0x10  # dir, rwxr-xr-x
                info.date_time = time.localtime()[:6]
                z.writestr(info, b"")
                continue
            z.write(os.path.join(SRC, rel), arcname)

    size = os.path.getsize(OUT)
    print(f"wrote {OUT} ({size} bytes, {len(ENTRIES)} entries)")
    with zipfile.ZipFile(OUT) as z:
        for name in z.namelist():
            print("  ", name + ("/" if name.endswith("/") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
