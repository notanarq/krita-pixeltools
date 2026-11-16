# SPDX-License-Identifier: GPL-2.0-or-later
"""
canvas.py - the drawing widget, embedded in the Pixel Tools docker.

Why a widget and not Krita's canvas
-----------------------------------
PyKrita exposes no mouse events on the Krita canvas, so a plugin cannot
intercept a drag over the artwork. A Qt widget DOES receive mouse events, so
the editor renders its own view of one layer and writes the result back.

This is the approach the original K-Sprite takes, and it is the only one that
works from Python.

Responsibilities, kept deliberately narrow
------------------------------------------
  * keep a LayerBuffer of the active layer, for rendering
  * render it, zoomed by an integer factor
  * turn mouse events into pixel coordinates and drive a Tool
  * draw the live preview of the tool on top of the buffer
  * open and close a StrokeSession, so a drag is ONE layer write

It knows nothing about Krita's API and nothing about undo: the host supplies
callables. Keeping the undo decision out of the widget is what lets the tests
run the whole drag with a fake layer that merely counts writes.

Two write modes
---------------
With a host (begin_stroke set), each drag is a session: the pixels accumulate
locally and reach the layer once, on release, as a single undo step.

Without a host, the widget paints its own buffer only. That mode is what the
coordinate tests use; it must keep working so a bare canvas never raises.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

try:  # Krita 6
    from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
    from PyQt6.QtGui import QColor, QImage, QPainter, QPen
    from PyQt6.QtWidgets import QWidget
except ImportError:  # Krita 5
    from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
    from PyQt5.QtGui import QColor, QImage, QPainter, QPen
    from PyQt5.QtWidgets import QWidget

from .buffer import BPP, LayerBuffer, BufferError
from ..stroke import StrokeSession, StrokeError
from . import tools as ksprite_tools

Point = Tuple[int, int]
Rgba = Tuple[int, int, int, int]

MIN_ZOOM = 1
MAX_ZOOM = 32


def _qt(scope, group, name):
    """Qt enum on both PyQt5 (flat) and PyQt6 (scoped)."""
    flat = getattr(scope, name, None)
    if flat is not None:
        return flat
    grp = getattr(scope, group, None)
    if grp is not None:
        return getattr(grp, name)
    raise AttributeError(f"Qt has neither {name} nor {group}.{name}")


def _image_format_argb32():
    """The QImage format whose memory order is BGRA on little-endian.

    Krita stores 8-bit RGBA as B,G,R,A. Format_ARGB32 reads a 32-bit word as
    0xAARRGGBB, which is exactly those bytes in memory. Format_RGBA8888 would
    swap red and blue - the mistake that is invisible on a gray test image.
    """
    fmt = getattr(QImage, "Format", None)
    if fmt is not None:  # PyQt6
        return fmt.Format_ARGB32
    return QImage.Format_ARGB32  # PyQt5


class KCanvas(QWidget):
    """The drawing surface: one layer, zoomed, mouse-driven."""

    strokeCommitted = pyqtSignal(str)
    cursorMoved = pyqtSignal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setMouseTracking(True)
        self.setFocusPolicy(_qt(Qt, "FocusPolicy", "StrongFocus"))
        self.setMinimumSize(160, 120)

        # --- document state ------------------------------------------
        self.buffer: Optional[LayerBuffer] = None
        self._image = None
        self.zoom = 8
        self.offset = QPoint(0, 0)

        # --- tool state ----------------------------------------------
        self.engine = None
        self.tool = None
        self.tool_name = ksprite_tools.TOOL_BRUSH
        self.tool_options = {"filled": False, "perfect": True}
        self.foreground: Rgba = (0, 0, 0, 255)
        self.background: Rgba = (255, 255, 255, 255)
        self.eraser = False

        # --- io hooks, set by the host --------------------------------
        self.read_layer: Optional[Callable[[], bytes]] = None
        self.write_rect = None
        self.doc_size = None

        # Stroke IO, supplied by the host. When a stroke begins the canvas
        # hands these to a StrokeSession so the whole drag becomes ONE write
        # - and therefore one undo step - instead of one per mouse move.
        self.begin_stroke: Optional[Callable[[], object]] = None
        self.commit_stroke = None
        self.stroke: Optional[StrokeSession] = None

        # --- interaction ----------------------------------------------
        self._panning = False
        self._pan_origin = QPoint(0, 0)
        self._drawing = False
        self._last_pixel: Optional[Point] = None
        self._shift = False
        self._right_button = False

    # -- document -----------------------------------------------------

    def attach(self, engine, read_layer, write_rect, doc_size) -> None:
        """Connects the widget to a document; called by the host on load."""
        self.engine = engine
        self.read_layer = read_layer
        self.write_rect = write_rect
        self.doc_size = doc_size
        self.reload()

    def reload(self) -> None:
        """Re-reads the active layer into the buffer."""
        if self.doc_size is None or self.read_layer is None:
            return
        try:
            w, h = self.doc_size()
        except Exception:  # noqa: BLE001 - no document open
            return
        if not w or not h:
            return

        try:
            data = self.read_layer()
        except Exception:  # noqa: BLE001
            data = b""

        try:
            self.buffer = LayerBuffer(w, h, data)
        except BufferError:
            self.buffer = None
            return

        self._rebuild_image()
        self._fit_zoom()
        self.update()

    def _rebuild_image(self) -> None:
        """Builds a QImage that VIEWS the buffer's bytes (no copy)."""
        if self.buffer is None:
            self._image = None
            return
        self._image = QImage(
            self.buffer.raw(),
            self.buffer.width,
            self.buffer.height,
            self.buffer.width * BPP,
            _image_format_argb32(),
        )

    def _fit_zoom(self) -> None:
        """Picks a zoom that shows the whole document, if it is small."""
        if self.buffer is None or self.width() <= 0:
            return
        zx = max(1, (self.width() - 8) // self.buffer.width)
        zy = max(1, (self.height() - 8) // self.buffer.height)
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, min(zx, zy)))
        self.offset = QPoint(4, 4)

    # -- tools --------------------------------------------------------

    def set_tool(self, name: str, **options) -> None:
        self.tool_name = name
        self.tool_options.update(options)
        self.tool = ksprite_tools.make_tool(name, self.engine, **self.tool_options)
        self.update()

    def set_foreground(self, rgba: Rgba) -> None:
        self.foreground = rgba

    def set_background(self, rgba: Rgba) -> None:
        self.background = rgba

    def set_zoom(self, value: int) -> None:
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, int(value)))
        self.update()

    # -- geometry -----------------------------------------------------

    def pixel_at(self, pos) -> Point:
        """Widget position -> image pixel. Floor division on purpose."""
        x = (pos.x() - self.offset.x()) // self.zoom
        y = (pos.y() - self.offset.y()) // self.zoom
        return (int(x), int(y))

    def widget_rect_for(self, x: int, y: int, w: int, h: int):
        return QRect(
            self.offset.x() + x * self.zoom,
            self.offset.y() + y * self.zoom,
            w * self.zoom,
            h * self.zoom,
        )

    # -- painting -----------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 44))

        if self.buffer is None or self._image is None:
            p.setPen(QColor(180, 180, 180))
            p.drawText(
                self.rect(),
                _qt(Qt, "AlignmentFlag", "AlignCenter"),
                "Nenhuma layer carregada.\nSelecione uma layer de pixel\ne clique em Carregar.",
            )
            p.end()
            return

        target = QRect(
            self.offset.x(),
            self.offset.y(),
            self.buffer.width * self.zoom,
            self.buffer.height * self.zoom,
        )
        # Nearest-neighbour scaling: pixels must stay crisp squares.
        p.setRenderHint(
            _qt(QPainter, "RenderHint", "SmoothPixmapTransform"), False
        )
        p.drawImage(target, self._image)

        self._paint_grid(p)
        self._paint_preview(p)
        p.end()

    def _paint_grid(self, p: QPainter) -> None:
        """A light 1px grid, only when zoomed enough to be useful."""
        if self.buffer is None or self.zoom < 6:
            return
        pen = QPen(QColor(255, 255, 255, 28))
        pen.setWidth(1)
        p.setPen(pen)
        w = self.buffer.width
        h = self.buffer.height
        for x in range(0, w + 1):
            wx = self.offset.x() + x * self.zoom
            p.drawLine(wx, self.offset.y(), wx, self.offset.y() + h * self.zoom)
        for y in range(0, h + 1):
            wy = self.offset.y() + y * self.zoom
            p.drawLine(self.offset.x(), wy, self.offset.x() + w * self.zoom, wy)

    def _paint_preview(self, p: QPainter) -> None:
        """Draws the tool's in-progress pixels on top of the buffer."""
        if self.tool is None or not self.tool.active:
            return
        pts = self.tool.points()
        if not pts:
            return

        z = self.zoom
        if self.eraser:
            # A hollow outline is visible over any artwork.
            pen = QPen(QColor(255, 80, 80))
            pen.setWidth(1)
            p.setPen(pen)
            p.setBrush(_qt(Qt, "BrushStyle", "NoBrush"))
            for x, y in pts:
                p.drawRect(QRect(self.offset.x() + x * z,
                                 self.offset.y() + y * z, z, z))
            return

        colour = self._stroke_colour()
        p.setPen(_qt(Qt, "PenStyle", "NoPen"))
        p.setBrush(QColor(colour[0], colour[1], colour[2], 255))
        for x, y in pts:
            p.drawRect(QRect(self.offset.x() + x * z,
                             self.offset.y() + y * z, z, z))

    def _stroke_colour(self) -> Rgba:
        return self.background if self._right_button else self.foreground

    # -- mouse ---------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if self.buffer is None:
            return

        button = event.button()
        self._right_button = button == _qt(Qt, "MouseButton", "RightButton")

        # Middle button pans - one of the K-Sprite controls.
        if button == _qt(Qt, "MouseButton", "MiddleButton"):
            self._panning = True
            self._pan_origin = event.pos()
            self.setCursor(_qt(Qt, "CursorShape", "ClosedHandCursor"))
            return

        if button not in (
            _qt(Qt, "MouseButton", "LeftButton"),
            _qt(Qt, "MouseButton", "RightButton"),
        ):
            return

        # Ctrl+click samples the colour under the cursor.
        if event.modifiers() & _qt(Qt, "KeyboardModifier", "ControlModifier"):
            px, py = self.pixel_at(event.pos())
            self.foreground = self.buffer.get(px, py)
            self.eraser = False
            self.update()
            return

        self._shift = bool(
            event.modifiers() & _qt(Qt, "KeyboardModifier", "ShiftModifier")
        )

        px, py = self.pixel_at(event.pos())
        self._drawing = True
        self._last_pixel = (px, py)

        if self.tool is None:
            self.set_tool(self.tool_name)

        # Open the stroke BEFORE the first pixel: the session needs the layer
        # as it was, and a stroke cannot be rewritten after the fact.
        self.stroke = None
        if self.begin_stroke is not None:
            opened = self.begin_stroke()
            if opened is None:
                self._drawing = False
                return
            self.stroke = StrokeSession()
            self.stroke.begin(*opened)

        self.tool.begin(px, py)
        self._paint_preview_to_stroke()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self.buffer is None:
            return

        px, py = self.pixel_at(event.pos())
        self.cursorMoved.emit(px, py)

        if self._panning:
            delta = event.pos() - self._pan_origin
            self.offset = self.offset + delta
            self._pan_origin = event.pos()
            self.update()
            return

        if not self._drawing or self.tool is None:
            return

        if self._shift and self.tool_name in (
            ksprite_tools.TOOL_SQUARE,
            ksprite_tools.TOOL_CIRCLE,
        ):
            px, py = self._constrain_square(px, py)

        self.tool.drag(px, py)
        self._paint_preview_to_stroke()
        self._last_pixel = (px, py)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._panning:
            self._panning = False
            self.setCursor(_qt(Qt, "CursorShape", "ArrowCursor"))
            return

        if not self._drawing or self.tool is None or self.buffer is None:
            return

        px, py = self.pixel_at(event.pos())
        self.tool.end(px, py)
        self._commit_stroke()
        self._drawing = False
        self._last_pixel = None
        self.update()

    def _constrain_square(self, px: int, py: int) -> Point:
        """With shift held, makes the box square in the drag direction."""
        if self.tool is None or self.tool.start is None:
            return (px, py)
        x0, y0 = self.tool.start
        dx = px - x0
        dy = py - y0
        side = max(abs(dx), abs(dy))
        return (x0 + (side if dx >= 0 else -side),
                y0 + (side if dy >= 0 else -side))

    def wheelEvent(self, event) -> None:
        """Ctrl+wheel zooms around the cursor."""
        if not (event.modifiers() & _qt(Qt, "KeyboardModifier", "ControlModifier")):
            event.ignore()
            return
        before = self.pixel_at(event.pos())
        step = 1 if event.angleDelta().y() > 0 else -1
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom + step))
        after = self.pixel_at(event.pos())
        # Keep the pixel under the cursor in place.
        self.offset = self.offset + QPoint(
            (after[0] - before[0]) * self.zoom,
            (after[1] - before[1]) * self.zoom,
        )
        self.update()

    # -- stroke --------------------------------------------------------

    def _paint_preview_to_stroke(self) -> None:
        """Copies what the tool has drawn so far into the local stroke buffer.

        The tool's points() is the LIVE preview: it can shrink as the stroke
        develops (the pixel-perfect pass drops a corner that became
        redundant), so a pixel already written to the buffer would stay
        behind. To keep the committed result identical to the preview, the
        stroke repaints from the layer copy on every update.
        """
        if self.stroke is None or self.tool is None:
            return

        pts = self.tool.points()
        if not pts:
            return

        # Re-apply from the base copy so removed preview pixels disappear.
        if self.stroke.paint_count:
            self.stroke.reset_paint()

        self.stroke.update(pts, self._stroke_colour(), erase=self.eraser)

    def _commit_stroke(self) -> None:
        """Closes the stroke: ONE write into the layer, ONE undo step."""
        if self.tool is None:
            return

        pts = self.tool.commit_points()
        self.tool.reset()

        session = self.stroke
        self.stroke = None

        # No session means the host did not offer one: fall back to painting
        # straight into the buffer, which is what a bare canvas can do.
        if session is None:
            if pts and self.buffer is not None:
                if self.eraser:
                    for x, y in pts:
                        self.buffer.erase(x, y)
                else:
                    self.buffer.put_generated(pts, self._stroke_colour())
                self._rebuild_image()
                self.strokeCommitted.emit(
                    f"{len(pts)} px · {self.tool_name} · buffer local"
                )
            self._drawing = False
            self.update()
            return

        if not pts or self.commit_stroke is None:
            session.cancel()
            self._drawing = False
            return

        # Apply the final point list, then hand the session to the host.
        session.reset_paint()
        session.update(pts, self._stroke_colour(), erase=self.eraser)

        ok, note, pixels = self.commit_stroke(session)

        self._rebuild_image()

        if not ok:
            state = f"FALHOU: {note}"
        elif note:
            state = f"gravado SEM undo: {note}"
        else:
            state = "gravado (1 passo de undo)"

        self.strokeCommitted.emit(
            f"{pixels} px · {self.tool_name} · {state}"
        )
        self.update()

    # -- keyboard ------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == _qt(Qt, "Key", "Key_X"):
            self.foreground, self.background = self.background, self.foreground
            self.update()
            return
        if key == _qt(Qt, "Key", "Key_E"):
            self.eraser = not self.eraser
            self.update()
            return
        if key in (_qt(Qt, "Key", "Key_Plus"), _qt(Qt, "Key", "Key_Equal")):
            self.set_zoom(self.zoom + 1)
            return
        if key == _qt(Qt, "Key", "Key_Minus"):
            self.set_zoom(self.zoom - 1)
            return
        super().keyPressEvent(event)
