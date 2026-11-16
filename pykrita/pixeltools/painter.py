# SPDX-License-Identifier: GPL-2.0-or-later
"""
painter.py - writes exact pixels into a Krita document, undoably.

Why this module exists
----------------------

The obvious approach, Node.setPixelData(), cannot be undone: Krita does not
expose its undo stack to Python scripting (confirmed by the Krita
maintainers). A plugin that writes pixels that way leaves the user with a
change they cannot Ctrl+Z. For a drawing tool that is disqualifying.

The approach used here relies on the fact that C++-implemented Krita
*actions* do register undo steps. Node.mergeDown() calls
KisImage::mergeDown -> KisLayerUtils::mergeDown, which runs inside a
KisProcessingApplicator with the "Merge Down" undo command. So:

    1. create a temporary paint layer above the target
    2. write our exact pixels into the temporary layer
    3. merge the temporary layer down into the target

Step 3 is a single undoable command, so one Ctrl+Z removes the whole stroke,
and the visible result is identical to painting directly on the target layer.

Channel order
-------------

Node.pixelData()/setPixelData() use the paint device's native byte order,
which for 8-bit integer RGBA is **BGRA** (see the Krita API docs: "Integer
RGBA: Blue, Green, Red, Alpha"). Getting this wrong swaps red and blue,
which is invisible in a grayscale test and very visible in real art.

Only U8 RGBA (4 channels, 1 byte each) is written directly. Other colour
spaces are reported as unsupported rather than silently mis-coloured.
"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

Point = Tuple[int, int]


class PaintError(Exception):
    """Raised when the stroke cannot be applied; message is user-facing."""


# Bytes per pixel for the colour spaces we support directly.
_RGBA_U8_BPP = 4


def _write_points(ba: bytearray, w: int, h: int, left: int, top: int,
                  points: Iterable[Point], rgba: Tuple[int, int, int, int]) -> int:
    """Sets the given pixels in a BGRA byte buffer. Returns pixels written.

    Writes are idempotent: pixels are deduplicated by the engine already, so
    a repeated call cannot double-composite and darken semi-transparent art.
    """
    r, g, b, a = rgba
    written = 0

    for x, y in points:
        gx = x - left
        gy = y - top
        if gx < 0 or gy < 0 or gx >= w or gy >= h:
            continue
        offset = (gy * w + gx) * _RGBA_U8_BPP
        ba[offset + 0] = b
        ba[offset + 1] = g
        ba[offset + 2] = r
        ba[offset + 3] = a
        written += 1

    return written


def _supports_direct_write(node) -> bool:
    """True when the node stores 8-bit RGBA, the format we can address."""
    try:
        return node.colorModel() == "RGBA" and node.colorDepth() == "U8"
    except Exception:  # noqa: BLE001 - older/newer API differences
        return False


def stamp(doc, target_node, points: Sequence[Point],
          rgba: Tuple[int, int, int, int]) -> int:
    """Paints `points` onto `target_node` as one undoable step.

    Returns the number of pixels actually written.
    Raises PaintError with a message fit for the user when it cannot proceed.
    """
    if not points:
        raise PaintError("Nothing to draw.")

    if not _supports_direct_write(target_node):
        raise PaintError(
            "This layer is not 8-bit RGBA. Krita's Python API cannot write "
            "other colour depths safely. Convert the image to 8-bit RGBA "
            "first (Image > Convert Image Colour Space)."
        )

    # Bounding box of the stroke, padded by one pixel so a single-pixel shape
    # still has a valid buffer.
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)

    w = right - left + 1
    h = bottom - top + 1

    parent = target_node.parentNode()
    if parent is None:
        raise PaintError("The active layer has no parent; cannot draw on it.")

    temp = None
    try:
        temp = doc.createNode("pixeltools stroke", "paintlayer")
        if temp is None:
            raise PaintError("Krita refused to create the temporary layer.")

        # Insert directly ABOVE the target: mergeDown() merges a layer into
        # its previous sibling, so the temp must be the target's next sibling.
        target_node.addChildNode(temp, None)

        buffer = bytearray(w * h * _RGBA_U8_BPP)
        written = _write_points(buffer, w, h, left, top, points, rgba)
        if written == 0:
            raise PaintError("No pixel fell inside the image.")

        # setPixelData takes the raw bytes; bytearray is accepted by PyKrita.
        ok = temp.setPixelData(bytes(buffer), left, top, w, h)
        if not ok:
            raise PaintError(
                "Krita refused the pixel write. The layer may be locked or "
                "not a paint layer."
            )

        # mergeDown() is the undoable step. It returns the merged node.
        merged = temp.mergeDown()
        temp = None  # Krita consumed it; do not touch the wrapper afterwards

        if merged is None:
            raise PaintError(
                "Could not merge the stroke into the layer. The layer may be "
                "a clone, file or group layer."
            )

    finally:
        # Only remove the temp layer if the merge never happened, otherwise
        # we would delete a node Krita already owns.
        if temp is not None:
            try:
                temp.remove()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass

    return written
