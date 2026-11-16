# SPDX-License-Identifier: GPL-2.0-or-later
"""
ksprite_tab.py - the K-Sprite editor as a tab inside the Pixel Tools docker.

Chosen over a separate floating window on request: it reuses the docker that
is already installed and working, so there is less surface for things to break.

What it does
------------
Wraps KCanvas and adds the controls around it:

    [Carregar] [Pincel][Linha][Bezier][Quadrado][Circulo]
    [ ] Preenchido  [x] Pixel perfect  [ ] Borracha   Zoom - +
    +-----------------------------------------------------+
    |                    KCanvas                          |
    +-----------------------------------------------------+
    status line

Layer access stays here, not in the canvas: the canvas receives callables for
reading and writing, so it never touches the Krita API. Writing happens per
stroke and covers ONLY the dirty rectangle.
"""

from __future__ import annotations

try:  # Krita 6
    from PyQt6.QtWidgets import (
        QCheckBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # Krita 5
    from PyQt5.QtWidgets import (
        QCheckBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

from krita import Krita

from .ksprite.canvas import KCanvas
from .ksprite import tools as ks_tools


TOOL_LABELS = [
    (ks_tools.TOOL_BRUSH, "Pincel"),
    (ks_tools.TOOL_LINE, "Linha"),
    (ks_tools.TOOL_BEZIER, "Bezier"),
    (ks_tools.TOOL_SQUARE, "Quadrado"),
    (ks_tools.TOOL_CIRCLE, "Circulo"),
]


class KSpriteTab(QWidget):
    """The whole K-Sprite editor, ready to be dropped into a QTabWidget."""

    def __init__(self, engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self._node = None          # the Layer we are editing
        self._doc = None
        self._tool_buttons = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # --- top row: load + tools ---------------------------------
        top = QHBoxLayout()
        top.setSpacing(2)

        self.btn_load = QPushButton("Carregar")
        self.btn_load.setToolTip(
            "Le a layer ativa para o editor.\n"
            "Clique de novo para recarregar depois de mexer no Krita."
        )
        self.btn_load.clicked.connect(self.load_layer)
        top.addWidget(self.btn_load)

        for name, label in TOOL_LABELS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(f"Ferramenta: {label}")
            btn.clicked.connect(lambda _c=False, n=name: self.select_tool(n))
            top.addWidget(btn)
            self._tool_buttons[name] = btn

        top.addStretch(1)
        layout.addLayout(top)

        # --- second row: options -----------------------------------
        opts = QHBoxLayout()
        opts.setSpacing(3)

        self.chk_filled = QCheckBox("Preenchido")
        self.chk_filled.setToolTip(
            "Quadrado e Circulo: preenche o interior.\n"
            "Nao afeta Pincel, Linha nem Bezier."
        )
        self.chk_filled.toggled.connect(self._apply_options)
        opts.addWidget(self.chk_filled)

        self.chk_perfect = QCheckBox("Pixel perfect")
        self.chk_perfect.setChecked(True)
        self.chk_perfect.setToolTip(
            "Remove o canto dobrado do traco livre.\n"
            "So vale para Pincel e Linha: em formas geometricas\n"
            "os cantos sao estruturais e sumiriam."
        )
        self.chk_perfect.toggled.connect(self._apply_options)
        opts.addWidget(self.chk_perfect)

        self.chk_eraser = QCheckBox("Borracha")
        self.chk_eraser.setToolTip(
            "Pinta transparente em vez de cor (atalho: E no canvas)."
        )
        self.chk_eraser.toggled.connect(self._apply_eraser)
        opts.addWidget(self.chk_eraser)

        opts.addWidget(QLabel("Zoom"))
        self.btn_zoom_out = QPushButton("-")
        self.btn_zoom_out.setFixedWidth(26)
        self.btn_zoom_out.clicked.connect(lambda: self._nudge_zoom(-2))
        opts.addWidget(self.btn_zoom_out)

        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setFixedWidth(26)
        self.btn_zoom_in.clicked.connect(lambda: self._nudge_zoom(2))
        opts.addWidget(self.btn_zoom_in)

        opts.addStretch(1)
        layout.addLayout(opts)

        # --- canvas ------------------------------------------------
        self.canvas = KCanvas(self)
        # The canvas needs the engine from the start: selecting a tool builds
        # a tool instance immediately, before any layer is loaded.
        self.canvas.engine = engine
        self.canvas.strokeCommitted.connect(self._on_stroke)
        self.canvas.cursorMoved.connect(self._on_cursor)
        layout.addWidget(self.canvas, 1)

        # --- status ------------------------------------------------
        self.status = QLabel("Selecione uma layer de pixel e clique em Carregar.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.select_tool(ks_tools.TOOL_BRUSH)

    # -- tools ---------------------------------------------------------

    def select_tool(self, name: str) -> None:
        for key, btn in self._tool_buttons.items():
            btn.setChecked(key == name)
        self.canvas.set_tool(name, **self._options())
        self._update_filled_state(name)
        self.status.setText(f"Ferramenta: {name}")

    def _options(self):
        return {
            "filled": self.chk_filled.isChecked(),
            "perfect": self.chk_perfect.isChecked(),
        }

    def _apply_options(self) -> None:
        self.canvas.set_tool(self.canvas.tool_name, **self._options())
        self._update_filled_state(self.canvas.tool_name)

    def _update_filled_state(self, name: str) -> None:
        """Only square and circle have a filled variant."""
        applies = name in (ks_tools.TOOL_SQUARE, ks_tools.TOOL_CIRCLE)
        self.chk_filled.setEnabled(applies)

    def _apply_eraser(self) -> None:
        self.canvas.eraser = self.chk_eraser.isChecked()

    def _nudge_zoom(self, delta: int) -> None:
        self.canvas.set_zoom(self.canvas.zoom + delta)

    # -- layer io -------------------------------------------------------

    def load_layer(self) -> None:
        """Binds to the active paint layer and pulls its pixels in."""
        doc = Krita.instance().activeDocument()
        if doc is None:
            self.status.setText("Nenhum documento aberto.")
            return

        node = doc.activeNode()
        if node is None:
            self.status.setText("Nenhuma layer ativa.")
            return

        if node.type() != "paintlayer":
            self.status.setText(
                f"Essa layer e do tipo '{node.type()}'. "
                "O editor precisa de uma layer de pintura."
            )
            return

        if not (node.colorModel() == "RGBA" and node.colorDepth() == "U8"):
            self.status.setText(
                f"Layer em {node.colorModel()}/{node.colorDepth()}. "
                "O editor trabalha em 8 bits RGBA. "
                "Converta com Imagem > Converter espaco de cor."
            )
            return

        self._doc = doc
        self._node = node

        self.canvas.attach(
            self.engine,
            read_layer=self._read_node,
            write_rect=self._write_node_rect,
            doc_size=lambda: (doc.width(), doc.height()),
        )

        # Sync the two colour swatches with Krita's.
        fg = self._krita_colour(foreground=True)
        if fg is not None:
            self.canvas.set_foreground(fg)
        bg = self._krita_colour(foreground=False)
        if bg is not None:
            self.canvas.set_background(bg)

        self.status.setText(
            f"Editando '{node.name()}' "
            f"({doc.width()}x{doc.height()}, zoom {self.canvas.zoom}x)."
        )

    def _read_node(self) -> bytes:
        if self._node is None:
            return b""
        doc = self._doc
        return bytes(self._node.pixelData(0, 0, doc.width(), doc.height()))

    def _write_node_rect(self, x: int, y: int, w: int, h: int, raw: bytes) -> bool:
        """Writes only the dirty rectangle back to the layer."""
        if self._node is None or w <= 0 or h <= 0:
            return False
        try:
            ok = self._node.setPixelData(raw, x, y, w, h)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Falha ao gravar: {exc}")
            return False
        if not ok:
            return False
        if self._doc is not None:
            self._doc.refreshProjection()
        return True

    def _krita_colour(self, foreground: bool):
        """Reads Krita's colour through the canvas, so it is sRGB."""
        try:
            from PyQt5.QtGui import QColor as _Q  # Krita 5
        except ImportError:
            from PyQt6.QtGui import QColor as _Q  # Krita 6

        window = Krita.instance().activeWindow()
        if window is None:
            return None
        view = window.activeView()
        if view is None:
            return None
        managed = (view.foregroundColor() if foreground
                   else view.backgroundColor())
        if managed is None:
            return None
        try:
            qc = managed.colorForCanvas(view.canvas())
            if isinstance(qc, _Q):
                return (qc.red(), qc.green(), qc.blue(), qc.alpha())
        except Exception:  # noqa: BLE001
            pass
        return None

    # -- feedback ------------------------------------------------------

    def _on_stroke(self, message: str) -> None:
        self.status.setText(message)

    def _on_cursor(self, x: int, y: int) -> None:
        if self._node is not None:
            self.status.setText(
                f"'{self._node.name()}'  ·  ({x},{y})  ·  zoom {self.canvas.zoom}x"
            )
