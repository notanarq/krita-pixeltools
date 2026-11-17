# SPDX-License-Identifier: GPL-2.0-or-later
"""
canvas_tool.py - draw on Krita's OWN canvas, like the Brush tool.

The problem
-----------
A docker with its own widget is a separate drawing surface: you load a layer,
you draw inside the docker, and the result is pushed back. That is not what
"a tool like the Brush" means. The Brush draws where you click on the artwork,
with no loading step.

PyKrita gives plugins no canvas mouse events. What it does give is Qt: the
canvas is a QOpenGLWidget inside Krita's window, and Qt lets any object
install an event filter on any widget. So this module:

  1. finds the canvas widget of the active view
  2. installs an event filter on it
  3. converts each pointer position to an image pixel
  4. drives the same tools the docker uses
  5. writes the stroke to the ACTIVE LAYER, one undo step

Coordinate conversion (the part that is easy to get wrong)
----------------------------------------------------------
Krita applies zoom, pan, rotation and mirroring between the image and the
widget. The conversion is two Qt transforms composed in a specific order:

    widget -> flake   = view.flakeToCanvasTransform().inverted()[0]
    flake  -> image   = view.flakeToImageTransform()
    widget -> image   = widget->flake  *  flake->image

Both were verified against Krita's own coordinate converter, which exposes
widgetToImage()/imageToWidget() as the inverse pair. The mapping floors
instead of rounding: a pixel covers [n, n+1), so the floor is the pixel
actually under the cursor, and rounding would shift every stroke half a pixel
up and left at high zoom.

Why the filter must CONSUME its events while drawing
----------------------------------------------------
If the filter passes the press through, Krita's active tool also starts its
own stroke and the user gets two strokes: ours and the brush's. So while a
stroke of ours is running the events are consumed, and while the tool is
inactive nothing is touched at all.

Why this is separate from the docker
------------------------------------
The docker answers "draw in a zoomed view of one layer". This answers "draw
where I click". Keeping them apart means the docker keeps working for precise
work and this covers the natural gesture.
"""

from __future__ import annotations

from typing import Optional, Tuple

try:  # Krita 6
    from PyQt6.QtCore import QEvent, QObject, QPointF, Qt
    from PyQt6.QtWidgets import QOpenGLWidget
except ImportError:  # Krita 5
    from PyQt5.QtCore import QEvent, QObject, QPointF, Qt
    from PyQt5.QtWidgets import QOpenGLWidget

from krita import Krita

from . import painter
from . import pixel_engine as pe
from .stroke import StrokeSession
from .ksprite import tools as ks_tools


# ---------------------------------------------------------------------------
# Finding the canvas widget
# ---------------------------------------------------------------------------


def _parent_class_name(widget, levels: int = 3) -> str:
    """The metaObject class name `levels` parents up, or ""."""
    w = widget
    for _ in range(levels):
        if w is None:
            return ""
        try:
            w = w.parent()
        except Exception:  # noqa: BLE001
            return ""
    if w is None:
        return ""
    try:
        return w.metaObject().className()
    except Exception:  # noqa: BLE001
        return ""


def find_canvas_widget():
    """Finds the QOpenGLWidget that paints the artwork.

    Krita nests it as QOpenGLWidget -> KisCanvasController viewport ->
    KisCanvasController, which is the documented shape third-party plugins
    rely on to reach the canvas. Returns None when no document is open.
    """
    try:
        window = Krita.instance().activeWindow()
        if window is None:
            return None
        qwin = window.qwindow()
        if qwin is None:
            return None
    except Exception:  # noqa: BLE001
        return None

    try:
        candidates = qwin.findChildren(QOpenGLWidget)
    except Exception:  # noqa: BLE001
        return None

    for widget in candidates:
        if _parent_class_name(widget, 2) == "KisCanvasController":
            return widget

    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------


def widget_to_image_transform(view):
    """The composed transformation from widget pixels to image pixels.

    Returns None when the view exposes no usable transform, rather than
    falling back to a guess: a wrong transform draws in the wrong place,
    which is worse than refusing.
    """
    try:
        flake_to_image = view.flakeToImageTransform()
        widget_to_flake = view.flakeToCanvasTransform().inverted()[0]
    except Exception:  # noqa: BLE001
        return None
    if flake_to_image is None or widget_to_flake is None:
        return None
    return widget_to_flake * flake_to_image


def apply_transform(transform, x: float, y: float) -> Tuple[int, int]:
    """Maps a widget position through a transform to an integer image pixel.

    Floor, not round: a pixel covers the half-open interval [n, n+1), so the
    floor is the pixel actually under the cursor. Rounding would shift every
    stroke half a pixel up and left at high zoom.
    """
    import math

    point = transform.map(QPointF(x, y))
    return (int(math.floor(point.x())), int(math.floor(point.y())))


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------


class CanvasToolController(QObject):
    """Installs the event filter and turns pointer events into strokes.

    Deliberately dumb about drawing: it owns the mouse cycle and delegates
    everything else to the tools and the stroke session, both of which are
    tested on their own.
    """

    def __init__(self, engine, on_status=None) -> None:
        super().__init__()
        self.pe = engine
        self.on_status = on_status or (lambda _msg: None)

        self.active = False
        self.tool_name = ks_tools.TOOL_BRUSH
        self.tool_options = {"filled": False, "perfect": True}
        self.tool = ks_tools.make_tool(self.tool_name, engine, **self.tool_options)

        self._widget = None
        self._strokes = 0
        self._last_error = ""

        # per-stroke state
        self._session: Optional[StrokeSession] = None
        self._node = None
        self._doc = None
        self._transforms = None

    # -- lifecycle ------------------------------------------------------

    def set_tool(self, name: str, **options) -> None:
        self.tool_name = name
        self.tool_options.update(options)
        self.tool = ks_tools.make_tool(name, self.pe, **self.tool_options)

    def activate(self) -> bool:
        """Starts intercepting the canvas. Returns False if it was not found."""
        widget = find_canvas_widget()
        if widget is None:
            self._last_error = (
                "Nao achei a canvas do Krita. Abra um documento e tente de novo."
            )
            self.on_status(self._last_error)
            return False

        if self._widget is not None and self._widget is not widget:
            self.deactivate()

        self._widget = widget
        try:
            widget.installEventFilter(self)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"installEventFilter falhou: {exc}"
            self.on_status(self._last_error)
            return False

        self.active = True
        self.on_status(
            "Ativo: desenhe direto no canvas do Krita. "
            "A ferramenta que voce escolher aqui e a que desenha."
        )
        return True

    def deactivate(self) -> None:
        if self._widget is not None:
            try:
                self._widget.removeEventFilter(self)
            except Exception:  # noqa: BLE001
                pass
        self._widget = None
        self.active = False
        self._abort()

    @property
    def strokes_drawn(self) -> int:
        return self._strokes

    @property
    def last_error(self) -> str:
        return self._last_error

    # -- event filter ---------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        """Routes pointer events to a stroke. Returns True to consume.

        Consuming matters: an unconsumed press lets Krita's own tool start a
        second stroke underneath ours.
        """
        if not self.active or self._widget is None:
            return False

        try:
            etype = event.type()
        except Exception:  # noqa: BLE001
            return False

        press = self._event_types("MouseButtonPress", "TabletPress")
        move = self._event_types("MouseMove", "TabletMove")
        release = self._event_types("MouseButtonRelease", "TabletRelease")

        if etype in press:
            if not self._is_left_press(event):
                return False
            return self._on_press(event)
        if etype in move:
            return self._on_move(event)
        if etype in release:
            return self._on_release(event)
        return False

    @staticmethod
    def _event_types(*names):
        """Resolves QEvent type constants across PyQt5/PyQt6 scoping."""
        out = set()
        for name in names:
            value = getattr(QEvent, name, None)
            if value is None:
                enum = getattr(QEvent, "Type", None)
                value = getattr(enum, name, None) if enum is not None else None
            if value is not None:
                out.add(value)
        return out

    def _is_left_press(self, event) -> bool:
        try:
            button = event.button()
        except Exception:  # noqa: BLE001
            return True
        left = getattr(Qt, "LeftButton", None)
        if left is None:
            left = getattr(getattr(Qt, "MouseButton", None), "LeftButton", None)
        return button == left if left is not None else True

    @staticmethod
    def _event_xy(event) -> Optional[Tuple[float, float]]:
        """Pointer position in the widget, whatever the event flavour."""
        for attr in ("posF", "position"):
            fn = getattr(event, attr, None)
            if fn is None:
                continue
            try:
                p = fn()
            except Exception:  # noqa: BLE001
                continue
            if hasattr(p, "x") and hasattr(p, "y"):
                return (p.x(), p.y())
        fn = getattr(event, "pos", None)
        if fn is not None:
            try:
                p = fn()
                if hasattr(p, "x") and hasattr(p, "y"):
                    return (float(p.x()), float(p.y()))
            except Exception:  # noqa: BLE001
                pass
        return None

    # -- stroke ---------------------------------------------------------

    def _target(self):
        """(doc, node) for the stroke, or (None, None) when not drawable."""
        try:
            doc = Krita.instance().activeDocument()
        except Exception:  # noqa: BLE001
            return (None, None)
        if doc is None:
            self.on_status("Nenhum documento aberto.")
            return (None, None)

        try:
            window = Krita.instance().activeWindow()
            view = window.activeView() if window else None
        except Exception:  # noqa: BLE001
            view = None
        if view is None:
            self.on_status("Nenhuma view ativa.")
            return (None, None)

        try:
            nodes = view.selectedNodes()
        except Exception:  # noqa: BLE001
            nodes = []

        node = nodes[0] if nodes else doc.activeNode()
        if node is None:
            self.on_status("Nenhuma layer ativa.")
            return (None, None)

        if node.type() != "paintlayer":
            self.on_status(
                f"A layer ativa e '{node.type()}'. Escolha uma layer de pintura."
            )
            return (None, None)

        if not (node.colorModel() == "RGBA" and node.colorDepth() == "U8"):
            self.on_status(
                f"Layer em {node.colorModel()}/{node.colorDepth()}. "
                "O plugin desenha em 8 bits RGBA."
            )
            return (None, None)

        return (doc, node)

    def _on_press(self, event) -> bool:
        xy = self._event_xy(event)
        if xy is None:
            return False

        doc, node = self._target()
        if doc is None or node is None:
            return False

        try:
            window = Krita.instance().activeWindow()
            view = window.activeView()
            transform = widget_to_image_transform(view)
        except Exception:  # noqa: BLE001
            transform = None

        if transform is None:
            self.on_status("Nao consegui converter a posicao do cursor.")
            return False

        px, py = apply_transform(transform, *xy)

        try:
            base = bytes(node.pixelData(0, 0, doc.width(), doc.height()))
        except Exception as exc:  # noqa: BLE001
            self.on_status(f"Nao consegui ler a layer: {exc}")
            return False

        self._doc = doc
        self._node = node
        self._transforms = transform

        try:
            self._session = StrokeSession()
            self._session.begin(base, doc.width(), doc.height())
        except Exception as exc:  # noqa: BLE001
            self._session = None
            self.on_status(f"Nao consegui iniciar o traco: {exc}")
            return False

        self.tool.begin(px, py)
        self._apply_preview()
        self._refresh()
        return True

    def _on_move(self, event) -> bool:
        if self._session is None:
            return False
        xy = self._event_xy(event)
        if xy is None:
            return False

        try:
            px, py = apply_transform(self._transforms, *xy)
        except Exception:  # noqa: BLE001
            return False

        self.tool.drag(px, py)
        self._apply_preview()
        self._refresh()
        return True

    def _on_release(self, event) -> bool:
        if self._session is None:
            return False

        xy = self._event_xy(event)
        if xy is not None:
            try:
                self.tool.end(*apply_transform(self._transforms, *xy))
            except Exception:  # noqa: BLE001
                self.tool.end()

        session = self._session
        node = self._node
        doc = self._doc

        points = self.tool.commit_points()
        self.tool.reset()
        self._session = None

        if not points:
            session.cancel()
            self._clear_preview()
            return True

        session.reset_paint()
        session.update(points, self._foreground(), erase=self._eraser)

        try:
            ok, note, pixels = session.finish(doc, node)
        except Exception as exc:  # noqa: BLE001
            self._clear_preview()
            self.on_status(f"Falha ao gravar: {exc}")
            return True

        self._clear_preview()
        self._strokes += 1

        if not ok:
            self.on_status(f"Falha ao gravar: {note}")
        elif note:
            self.on_status(f"{pixels} px gravados SEM undo ({note})")
        else:
            self.on_status(f"{pixels} px gravados na layer (1 passo de undo)")

        self._refresh()
        return True

    # -- preview --------------------------------------------------------

    def _apply_preview(self) -> None:
        """Paints the in-progress stroke so the user sees it while dragging.

        The preview is the stroke's own buffer, applied to the layer through
        the same temporary-layer path used for the final write. Nothing is
        committed to the undo stack until release.
        """
        if self._session is None:
            return
        points = self.tool.points()
        if not points:
            return
        if self._session.paint_count:
            self._session.reset_paint()
        self._session.update(points, self._foreground(), erase=self._eraser)

    def _refresh(self) -> None:
        if self._doc is None:
            return
        try:
            self._doc.refreshProjection()
        except Exception:  # noqa: BLE001
            pass

    def _clear_preview(self) -> None:
        self._session = None
        self._node = None
        self._doc = None

    def _abort(self) -> None:
        if self._session is not None:
            self._session.cancel()
        self._clear_preview()

    # -- colours --------------------------------------------------------

    def _foreground(self):
        try:
            from PyQt5.QtGui import QColor as _Q  # Krita 5
        except ImportError:
            from PyQt6.QtGui import QColor as _Q  # Krita 6

        try:
            window = Krita.instance().activeWindow()
            view = window.activeView()
            managed = view.foregroundColor()
            qc = managed.colorForCanvas(view.canvas())
            if isinstance(qc, _Q):
                return (qc.red(), qc.green(), qc.blue(), qc.alpha())
        except Exception:  # noqa: BLE001
            pass
        return (0, 0, 0, 255)

    @property
    def _eraser(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Self-test, runnable from Krita's Scripter
# ---------------------------------------------------------------------------


def probe() -> str:
    """Reports whether the canvas can be reached. Run in the Scripter:

        import pixeltools.canvas_tool as ct; print(ct.probe())
    """
    lines = ["pixeltools canvas probe"]

    widget = find_canvas_widget()
    lines.append(f"  canvas widget : {widget!r}")
    if widget is not None:
        try:
            lines.append(f"  size          : {widget.width()}x{widget.height()}")
            lines.append(f"  parent2 class : {_parent_class_name(widget, 2)}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  size error    : {exc}")

    try:
        window = Krita.instance().activeWindow()
        view = window.activeView() if window else None
    except Exception as exc:  # noqa: BLE001
        view = None
        lines.append(f"  view error    : {exc}")

    if view is None:
        lines.append("  view          : None (abra um documento)")
        return "\n".join(lines)

    transform = widget_to_image_transform(view)
    lines.append(f"  transform     : {transform!r}")
    if transform is not None and widget is not None:
        cx, cy = widget.width() / 2.0, widget.height() / 2.0
        lines.append(
            f"  centre widget->image : {apply_transform(transform, cx, cy)}"
        )

    try:
        doc = Krita.instance().activeDocument()
        if doc is not None:
            lines.append(f"  document      : {doc.width()}x{doc.height()}")
            node = doc.activeNode()
            lines.append(f"  active node   : {node.name() if node else None}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  document error: {exc}")

    return "\n".join(lines)
