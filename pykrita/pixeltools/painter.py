# SPDX-License-Identifier: GPL-2.0-or-later
"""
painter.py - writes exact pixels into a Krita document, undoably.

Why this module exists
----------------------

The obvious approach, Node.setPixelData(), cannot be undone: Krita does not
expose its undo stack to Python scripting. A plugin that writes pixels that
way leaves the user with a change they cannot Ctrl+Z. For a drawing tool that
is disqualifying.

The approach used here relies on the fact that C++-implemented Krita
*actions* do register undo steps. Node.mergeDown() calls
KisImage::mergeDown -> KisLayerUtils::mergeDown, which runs inside a
KisProcessingApplicator with the "Merge Down" undo command. So:

    1. create a temporary paint layer directly above the target
    2. write our exact pixels into the temporary layer
    3. merge the temporary layer down into the target

Step 3 is a single undoable command, so one Ctrl+Z removes the whole stroke.

Three API details that are easy to get wrong (all three were wrong once):

  * mergeDown() returns NOTHING. The reference documents it as void, so
    "if merged is None: raise" always raised. Success must be verified by
    reading the target's pixels back, not by a return value.

  * addChildNode() must be called on the PARENT (the image root or the
    enclosing group). Calling it on the target layer puts the temp layer
    inside that layer as if it were a mask.

  * mergeDown() merges with the first VISIBLE node underneath, not
    necessarily with the target. If the layer below the target is visible,
    the pixels land in the wrong layer. So the temp layer is written and
    merged only when the target is the topmost visible paint layer;
    otherwise the pixels are written straight into the target and the
    undo caveat is reported.

Channel order
-------------

Node.pixelData()/setPixelData() use the paint device's native byte order,
which for 8-bit integer RGBA is **BGRA** (see the API docs: "Integer RGBA:
Blue, Green, Red, Alpha"). Getting this wrong swaps red and blue, which is
invisible in a grayscale test and very visible in real art.

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


def _paintable_type(node) -> bool:
    """True for node types whose pixels setPixelData() can overwrite.

    The API docs list paint layers and masks as writable; file, group and
    clone layers silently ignore setPixelData, so we refuse them up front
    instead of claiming success.
    """
    try:
        return node.type() == "paintlayer"
    except Exception:  # noqa: BLE001
        return False


def _read_back(node, x: int, y: int, w: int, h: int) -> bytes:
    """Reads a rectangle back as raw bytes, for verification."""
    try:
        return bytes(node.pixelData(x, y, w, h))
    except Exception:  # noqa: BLE001
        return b""


def _has_ink(node, x: int, y: int, w: int, h: int, rgba) -> bool:
    """True when any pixel in the rect matches the colour we tried to write.

    Used to confirm that a write actually landed. Compares only the RGB (not
    alpha) because Krita may composite the write over existing pixels.
    """
    data = _read_back(node, x, y, w, h)
    if not data:
        return False
    r, g, b, _a = rgba
    want = bytes((b, g, r))  # BGRA
    for i in range(0, len(data) - 3, _RGBA_U8_BPP):
        if data[i:i + 3] == want:
            return True
    return False


def _siblings(node):
    """The node's siblings, bottom-up, or [] when it has no parent."""
    parent = node.parentNode()
    if parent is None:
        return []
    try:
        return list(parent.childNodes())
    except Exception:  # noqa: BLE001
        return []


def _is_topmost_visible(node) -> bool:
    """True when nothing visible sits above `node` among its siblings.

    This matters because mergeDown() merges with the first VISIBLE node
    underneath the merged layer. Our temp layer is inserted immediately
    above the target, so merging is only correct when the target is the
    topmost visible sibling.
    """
    sibs = _siblings(node)
    if not sibs:
        return False
    try:
        idx = next(i for i, n in enumerate(sibs) if n.uniqueId() == node.uniqueId())
    except (StopIteration, AttributeError):
        return False
    for above in sibs[idx + 1:]:
        try:
            if above.visible():
                return False
        except Exception:  # noqa: BLE001
            return False
    return True


def stamp(doc, target_node, points: Sequence[Point],
          rgba: Tuple[int, int, int, int]) -> Tuple[int, str]:
    """Paints `points` onto `target_node`, preferably undoably.

    Returns (pixels_written, note) where note is "" or a user-facing remark
    explaining a reduced guarantee (e.g. no undo on this layer).
    Raises PaintError with a message fit for the user when it cannot proceed.
    """
    if not points:
        raise PaintError("Nothing to draw.")

    if not _paintable_type(target_node):
        raise PaintError(
            f"Can't draw on this layer type ({target_node.type()}). "
            "Select a normal paint layer."
        )

    if not _supports_direct_write(target_node):
        raise PaintError(
            f"This layer is {target_node.colorModel()}/"
            f"{target_node.colorDepth()}. Pixel Tools writes 8-bit RGBA only. "
            "Convert with Image > Convert Image Colour Space."
        )

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    w = right - left + 1
    h = bottom - top + 1

    buffer = bytearray(w * h * _RGBA_U8_BPP)
    written = _write_points(buffer, w, h, left, top, points, rgba)
    if written == 0:
        raise PaintError("No pixel fell inside the image.")
    raw = bytes(buffer)

    # --- Path A: the layer is topmost visible, so mergeDown targets it ----
    # This is the only path that is genuinely undoable, because the merge is
    # Krita's own undoable command.
    if _is_topmost_visible(target_node):
        note = _stamp_via_merge(doc, target_node, raw, left, top, w, h, rgba)
        # NOTE: _stamp_via_merge returns None on SUCCESS and a string on
        # failure. Testing "if note is not None: return" here would invert
        # the logic and silently fall through to the direct write on every
        # successful merge.
        if note is None:
            return written, ""
        # merge failed structurally; fall through to the direct write

    # --- Path B: direct write -------------------------------------------
    # Not undoable through Krita's stack, but it never touches another layer.
    if not target_node.setPixelData(raw, left, top, w, h):
        raise PaintError(
            "Krita refused the pixel write. The layer may be locked."
        )

    if not _has_ink(target_node, left, top, w, h, rgba):
        raise PaintError(
            "The write did not land. The layer may be locked, or its content "
            "is not where this docker expects."
        )

    return written, (
        "Drawn directly (Ctrl+Z will not undo this). Put the layer on top "
        "of the stack to get undoable strokes."
    )


def _stamp_via_merge(doc, target, raw, left, top, w, h, rgba):
    """Temp layer above target -> write -> mergeDown.

    Returns None on success, or a note string if the merge was not usable
    and the caller should fall back to a direct write.
    """
    temp = None
    try:
        temp = doc.createNode("pixeltools stroke", "paintlayer")
        if temp is None:
            return "Could not create the temporary layer."

        # Insert into the PARENT, immediately above the target. Calling this
        # on the target itself makes the temp layer a mask inside it.
        parent = target.parentNode()
        if parent is None:
            return "The layer has no parent."

        if not parent.addChildNode(temp, target):
            return "Krita refused to insert the temporary layer."

        if not temp.setPixelData(raw, left, top, w, h):
            return "The temporary layer refused the pixel write."

        # mergeDown() is documented as returning nothing. Do NOT test its
        # return value: treat the call as a command and verify by reading
        # the result back from the target.
        temp.mergeDown()

        # Krita consumed the node on success.
        temp = None

        if not _has_ink(target, left, top, w, h, rgba):
            return "The merge produced no visible pixels."

        return None

    except Exception as exc:  # noqa: BLE001 - fall back, never crash
        return f"Merge path failed ({exc})."
    finally:
        if temp is not None:
            try:
                temp.remove()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
