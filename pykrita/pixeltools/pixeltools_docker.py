# SPDX-License-Identifier: GPL-2.0-or-later
"""
pixeltools_docker.py - the docker UI.

What this docker can and cannot do, stated up front because it shapes every
design decision below:

  CAN: read and write pixels of a paint layer; create/remove/merge layers;
       read the active foreground colour; compute exact integer shapes;
       wrap the whole operation in one undoable step.

  CANNOT: receive mouse events on the canvas, nor convert screen coordinates
       to image coordinates. PyKrita exposes neither. So "drag to draw with a
       live preview" is impossible from Python - that part is the C++ tool in
       native/ and is documented as such.
       Because of that, this docker takes the shape's geometry as NUMBERS.

Two ways to get geometry, both honest about the limitation:

  1) "Capture from selection" reads the CURRENT selection's bounding box and
     uses it as the shape box. Select with any Krita selection tool
     (rectangle, ellipse, polygon), then hit the button. This is the closest
     thing to "draw with the mouse" available without C++.

  2) Type the box, or the four Bezier control points, directly.

The stroke is applied as: temporary paint layer on top -> write exact pixels
-> merge down. Merging is wrapped by Krita's own "Merge Down" undo command, so
a single Ctrl+Z removes the whole shape.
"""

from __future__ import annotations

try:
    from PyQt6.QtWidgets import (  # Krita 6.x
        QCheckBox,
        QComboBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
    from PyQt6.QtCore import Qt
except ImportError:  # Krita 5.x
    from PyQt5.QtWidgets import (
        QCheckBox,
        QComboBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
    from PyQt5.QtCore import Qt

from krita import DockWidget, Krita  # noqa: E402

from . import painter  # noqa: E402
from . import pixel_engine as pe  # noqa: E402


def _qt_enum(scope, group: str, name: str):
    """Returns a Qt enum value on both PyQt5 (Krita 5) and PyQt6 (Krita 6).

    PyQt5 exposes flat enums: Qt.AlignRight.
    PyQt6 scopes them:        Qt.AlignmentFlag.AlignRight.
    Writing either form directly breaks on the other version - and because
    the docker is constructed inside a try/except at import time, the
    resulting AttributeError silently prevents registration, leaving the
    plugin enabled but with no docker in the menu. Hence this helper.
    """
    flat = getattr(scope, name, None)
    if flat is not None:
        return flat
    group_obj = getattr(scope, group, None)
    if group_obj is not None:
        return getattr(group_obj, name)
    raise AttributeError(f"Qt has neither {name} nor {group}.{name}")


SHAPES = [
    "Line",
    "Ellipse (outline)",
    "Ellipse (filled)",
    "Rectangle (outline)",
    "Rectangle (filled)",
    "Bezier curve",
]


class PixelToolsDocker(DockWidget):
    """Docker widget: pick a shape, give the geometry, stamp exact pixels."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pixel Tools")

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # --- shape selection ------------------------------------------------
        form = QFormLayout()
        form.setLabelAlignment(_qt_enum(Qt, "AlignmentFlag", "AlignRight"))

        self.shape_combo = QComboBox()
        self.shape_combo.addItems(SHAPES)
        self.shape_combo.currentIndexChanged.connect(self._on_shape_changed)
        form.addRow("Shape", self.shape_combo)

        # --- geometry -------------------------------------------------------
        self.x0 = self._spin(-9999, 9999, 0)
        self.y0 = self._spin(-9999, 9999, 0)
        self.x1 = self._spin(-9999, 9999, 63)
        self.y1 = self._spin(-9999, 9999, 63)

        row_a = QHBoxLayout()
        row_a.addWidget(QLabel("Start"))
        row_a.addWidget(self.x0)
        row_a.addWidget(self.y0)
        row_a.addWidget(QLabel("End"))
        row_a.addWidget(self.x1)
        row_a.addWidget(self.y1)
        layout.addLayout(form)
        layout.addLayout(row_a)

        # Bezier control points, hidden unless the Bezier shape is chosen.
        self.bezier_box = QWidget()
        bl = QFormLayout(self.bezier_box)
        bl.setContentsMargins(0, 0, 0, 0)
        self.ctrl1 = QLineEdit("20,0")
        self.ctrl2 = QLineEdit("44,64")
        bl.addRow("Control 1", self.ctrl1)
        bl.addRow("Control 2", self.ctrl2)
        self.bezier_box.setVisible(False)
        layout.addWidget(self.bezier_box)

        # --- brush ----------------------------------------------------------
        self.thickness = self._spin(0, 64, 0)
        layout.addWidget(self._row("Thickness", self.thickness,
                                   "0 = single pixel"))

        self.fill_gaps = QCheckBox("Bridge gaps in outline (brush side)")
        self.fill_gaps.setChecked(False)
        layout.addWidget(self.fill_gaps)

        # --- actions --------------------------------------------------------
        self.btn_capture = QPushButton("Capture from selection")
        self.btn_capture.setToolTip(
            "Use the bounding box of the current selection as the shape box.\n"
            "Select an area with any selection tool first."
        )
        self.btn_capture.clicked.connect(self._capture_selection)
        layout.addWidget(self.btn_capture)

        self.btn_apply = QPushButton("Draw")
        self.btn_apply.setToolTip("Stamp the shape on the active paint layer.")
        self.btn_apply.clicked.connect(self._apply)
        layout.addWidget(self.btn_apply)

        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        layout.addStretch(1)
        self.setWidget(root)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _spin(lo: int, hi: int, value: int) -> QSpinBox:
        box = QSpinBox()
        box.setRange(lo, hi)
        box.setValue(value)
        return box

    @staticmethod
    def _row(label: str, widget: QWidget, hint: str = "") -> QWidget:
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel(label))
        h.addWidget(widget)
        if hint:
            h.addWidget(QLabel(f"<small>{hint}</small>"))
        h.addStretch(1)
        return holder

    def _on_shape_changed(self, index: int) -> None:
        self.bezier_box.setVisible(SHAPES[index] == "Bezier curve")

    def _say(self, message: str) -> None:
        self.status.setText(message)

    # -- geometry sources ----------------------------------------------------

    def _capture_selection(self) -> None:
        """Uses the current selection's bounding box as the shape box."""
        doc = Krita.instance().activeDocument()
        if doc is None:
            self._say("No document open.")
            return

        sel = doc.selection()
        if sel is None:
            self._say("No selection. Use a selection tool first.")
            return

        # Krita 5.2+ exposes Selection.x()/y()/width()/height().
        x, y = sel.x(), sel.y()
        w, h = sel.width(), sel.height()
        if w <= 0 or h <= 0:
            self._say("Selection is empty.")
            return

        # A rectangle's box is (x,y)-(x+w-1,y+h-1): both endpoints are
        # inclusive pixel indices, so a 7-pixel-wide box is 0..6.
        self.x0.setValue(x)
        self.y0.setValue(y)
        self.x1.setValue(x + w - 1)
        self.y1.setValue(y + h - 1)
        self._say(f"Captured box ({x},{y})-({x + w - 1},{y + h - 1}).")

    # -- shape construction --------------------------------------------------

    def _build_path(self) -> list[tuple[int, int]]:
        a = (self.x0.value(), self.y0.value())
        b = (self.x1.value(), self.y1.value())
        shape = self.shape_combo.currentText()

        if shape == "Line":
            path = pe.line(a, b)
        elif shape == "Ellipse (outline)":
            path = pe.ellipse(a, b)
        elif shape == "Ellipse (filled)":
            path = pe.ellipse_filled(a, b)
        elif shape == "Rectangle (outline)":
            path = pe.rectangle(a, b)
        elif shape == "Rectangle (filled)":
            path = pe.rectangle_filled(a, b)
        elif shape == "Bezier curve":
            c1 = self._parse_point(self.ctrl1.text())
            c2 = self._parse_point(self.ctrl2.text())
            if c1 is None or c2 is None:
                raise ValueError("Control points must look like '12,34'.")
            path = pe.bezier(a, c1, c2, b)
        else:  # pragma: no cover - combo is fixed
            raise ValueError(f"Unknown shape {shape!r}")

        radius = self.thickness.value()
        if radius > 0:
            path = pe.thicken(path, radius)

        return path

    @staticmethod
    def _parse_point(text: str):
        try:
            x, y = text.replace(" ", "").split(",")
            return (int(x), int(y))
        except (ValueError, AttributeError):
            return None

    # -- applying ------------------------------------------------------------

    def _apply(self) -> None:
        doc = Krita.instance().activeDocument()
        if doc is None:
            self._say("No document open.")
            return

        node = doc.activeNode()
        if node is None or node.type() != "paintlayer":
            self._say("Select a paint layer first (not a group or mask).")
            return

        if node.locked():
            self._say("The active layer is locked.")
            return

        try:
            path = self._build_path()
        except ValueError as exc:
            self._say(str(exc))
            return

        if not path:
            self._say("The shape has no pixels.")
            return

        # Clamp to the image so a stray coordinate cannot allocate gigabytes.
        box = pe.bounds(path)
        clipped = pe.clip(path, (0, 0, doc.width() - 1, doc.height() - 1))
        if not clipped:
            self._say("The shape is entirely outside the image.")
            return

        missing = len(path) - len(clipped)
        if missing:
            self._say(f"{missing} pixels were outside the image and skipped.")

        color = self._foreground_rgba()
        if color is None:
            self._say("Could not read the foreground colour.")
            return

        try:
            painted, note = painter.stamp(doc, node, clipped, color)
        except painter.PaintError as exc:
            self._say(str(exc))
            return

        # Document has no refresh(); refreshProjection() is the documented
        # call that recomposes the projection so the canvas shows the stroke.
        doc.refreshProjection()
        message = (
            f"Drew {painted} px on '{node.name()}' "
            f"(box {box[0]},{box[1]}-{box[2]},{box[3]})."
        )
        if note:
            message += " " + note
        else:
            message += " Ctrl+Z to undo."
        self._say(message)

    def _foreground_rgba(self):
        """Reads the active foreground colour as 8-bit sRGB RGBA.

        ManagedColor.components() returns the colour in the DOCUMENT's colour
        space, whose channel order can differ (Krita's internal RGBA is often
        BGR). Converting through colorForCanvas() gives a QColor in sRGB,
        where red()/green()/blue()/alpha() are unambiguous 0..255 values -
        no guessing about channel order.
        """
        try:
            from PyQt5.QtGui import QColor  # Krita 5.x
        except ImportError:
            from PyQt6.QtGui import QColor  # Krita 6.x

        window = Krita.instance().activeWindow()
        if window is None:
            return None
        view = window.activeView()
        if view is None:
            return None

        managed = view.foregroundColor()
        if managed is None:
            return None

        # Preferred: go through the canvas so the result is sRGB.
        try:
            qcolor = managed.colorForCanvas(view.canvas())
            if isinstance(qcolor, QColor):
                return (qcolor.red(), qcolor.green(), qcolor.blue(), qcolor.alpha())
        except Exception:  # noqa: BLE001 - fall back to raw components
            pass

        # Fallback: raw components, assuming the conventional R,G,B,A order.
        try:
            components = list(managed.components())
        except Exception:  # noqa: BLE001
            return None

        if not components:
            return None

        vals = [c * 255.0 if c <= 1.0 else c for c in components]
        vals = [max(0, min(255, int(round(v)))) for v in vals]
        while len(vals) < 3:
            vals.append(0)
        alpha = vals[3] if len(vals) > 3 else 255
        return (vals[0], vals[1], vals[2], alpha)

    # -- Krita DockWidget API ------------------------------------------------

    def canvasChanged(self, canvas) -> None:
        """Called by Krita when the active canvas changes. Nothing to do."""
        return


# ---------------------------------------------------------------------------
# Geometry-only helpers, importable without Krita for testing
# ---------------------------------------------------------------------------


def parse_points(text: str) -> list[tuple[int, int]]:
    """Parses 'x,y x,y ...' into a list of points."""
    out = []
    for chunk in text.replace(";", " ").split():
        x, y = chunk.split(",")
        out.append((int(x), int(y)))
    return out
