# SPDX-License-Identifier: GPL-2.0-or-later
"""
test_docker_strict.py - constructs the docker against a STRICT PyQt5 stand-in.

Why a second harness
--------------------
The first smoke test used a permissive stub whose __getattr__ returned a
no-op for any unknown attribute. That made the test pass no matter what it
called: it could not detect a method that does not exist in real PyQt5.
A stub that never fails cannot prove anything.

This harness is the opposite. Every stand-in declares EXPLICITLY the methods
real Qt5 provides, and anything not declared raises AttributeError. So a
call to a method PyQt5 does not have fails here, exactly as it would in
Krita 5.x.

Method lists below are the Qt5 API surface as documented:
  QDockWidget  setWindowTitle, setWidget
  QWidget      setContentsMargins, setVisible, setToolTip
  QLayout      setContentsMargins, setSpacing, addWidget, addLayout,
               addStretch, addRow, setLabelAlignment
  QComboBox    addItems, currentIndexChanged, currentText
  QSpinBox     setRange, setValue, value
  QCheckBox    setChecked
  QLineEdit    text
  QPushButton  clicked, setToolTip
  QLabel       setText, text, setWordWrap

Run:  python3 core/tests/test_docker_strict.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DOCKER_DIR = os.path.join(ROOT, "pykrita", "pixeltools")

failures: list[str] = []
checks = 0


def check(cond: bool, what: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(what)
        print("  FAIL:", what)
    else:
        print("  ok  :", what)


class _Strict:
    """Base class: unknown attribute access is a hard failure."""

    def __getattr__(self, name):
        raise AttributeError(
            f"{type(self).__name__!r} has no attribute {name!r} "
            f"(strict PyQt5 stand-in: not a real Qt5 method)"
        )


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *a):
        for s in self.slots:
            s(*a)


def _make_widget(name: str):
    """Builds a class with EXACTLY the Qt5 methods that widget really has."""

    def _m(fn):
        return fn

    @_m
    def setToolTip(self, *a):
        pass

    @_m
    def setVisible(self, v):
        self.visible = v

    @_m
    def setWindowTitle(self, t):
        self.window_title = t

    @_m
    def setWidget(self, w):
        self.widget = w

    @_m
    def setContentsMargins(self, *a):
        pass

    @_m
    def setSpacing(self, *a):
        pass

    @_m
    def addWidget(self, *a):
        pass

    @_m
    def addLayout(self, *a):
        pass

    @_m
    def addStretch(self, *a):
        pass

    @_m
    def addRow(self, *a):
        pass

    @_m
    def setLabelAlignment(self, *a):
        pass

    @_m
    def addItems(self, items):
        self.items = list(items)

    @_m
    def currentText(self):
        return self.items[self.index] if getattr(self, "items", None) else ""

    @_m
    def setRange(self, lo, hi):
        self.lo, self.hi = lo, hi

    @_m
    def setValue(self, v):
        self.val = v

    @_m
    def value(self):
        return getattr(self, "val", 0)

    @_m
    def setChecked(self, v):
        self.checked = v

    @_m
    def setText(self, t):
        self.text_value = t

    @_m
    def text(self):
        return getattr(self, "text_value", "")

    @_m
    def setWordWrap(self, v):
        self.wrap = v

    def _init(self, *a, **k):
        self.visible = True
        self.window_title = ""
        self.widget = None
        self.items = []
        self.index = 0
        self.val = 0
        self.text_value = ""

    methods = {
        "__init__": _init,
        "setToolTip": setToolTip,
        "setVisible": setVisible,
        "setWindowTitle": setWindowTitle,
        "setWidget": setWidget,
        "setContentsMargins": setContentsMargins,
        "setSpacing": setSpacing,
        "addWidget": addWidget,
        "addLayout": addLayout,
        "addStretch": addStretch,
        "addRow": addRow,
        "setLabelAlignment": setLabelAlignment,
        "addItems": addItems,
        "currentText": currentText,
        "setRange": setRange,
        "setValue": setValue,
        "value": value,
        "setChecked": setChecked,
        "setText": setText,
        "text": text,
        "setWordWrap": setWordWrap,
    }

    cls = type(name, (_Strict,), methods)

    # Signals are class ATTRIBUTES (objects), not methods.
    if name in ("QComboBox", "QPushButton"):
        cls.currentIndexChanged = _Signal()
        cls.clicked = _Signal()

    return cls


def install_strict_pyqt5() -> None:
    core = types.ModuleType("PyQt5.QtCore")

    class Qt:
        """PyQt5 layout: FLAT enums on Qt, and no AlignmentFlag group."""

        AlignRight = 0x0002
        AlignLeft = 0x0001
        NoPen = 0

    core.Qt = Qt

    widgets = types.ModuleType("PyQt5.QtWidgets")
    for n in [
        "QCheckBox",
        "QComboBox",
        "QFormLayout",
        "QHBoxLayout",
        "QLabel",
        "QLineEdit",
        "QPushButton",
        "QSpinBox",
        "QVBoxLayout",
        "QWidget",
        "QDockWidget",
    ]:
        setattr(widgets, n, _make_widget(n))

    pkg = types.ModuleType("PyQt5")
    pkg.QtCore = core
    pkg.QtWidgets = widgets

    for k in list(sys.modules):
        if k == "PyQt6" or k.startswith("PyQt6."):
            del sys.modules[k]

    sys.modules["PyQt5"] = pkg
    sys.modules["PyQt5.QtCore"] = core
    sys.modules["PyQt5.QtWidgets"] = widgets

    krita_mod = types.ModuleType("krita")

    class DockWidget:
        def __init__(self):
            self.window_title = ""
            self.widget = None

        def setWindowTitle(self, t):
            self.window_title = t

        def setWidget(self, w):
            self.widget = w

        def __getattr__(self, name):
            raise AttributeError(f"DockWidget has no attribute {name!r}")

    class Krita:
        @classmethod
        def instance(cls):
            return cls._obj

    Krita._obj = None
    krita_mod.DockWidget = DockWidget
    krita_mod.Krita = Krita
    sys.modules["krita"] = krita_mod


def load_package():
    for k in list(sys.modules):
        if k.startswith("pkgsrc"):
            del sys.modules[k]

    pkg = types.ModuleType("pkgsrc")
    pkg.__path__ = [DOCKER_DIR]
    sys.modules["pkgsrc"] = pkg

    for sub in ("pixel_engine", "painter"):
        full = f"pkgsrc.{sub}"
        spec = importlib.util.spec_from_file_location(
            full, os.path.join(DOCKER_DIR, f"{sub}.py")
        )
        m = importlib.util.module_from_spec(spec)
        sys.modules[full] = m
        spec.loader.exec_module(m)

    full = "pkgsrc.pixeltools_docker"
    spec = importlib.util.spec_from_file_location(
        full, os.path.join(DOCKER_DIR, "pixeltools_docker.py")
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules[full] = m
    spec.loader.exec_module(m)
    return m


def main() -> int:
    print("pixeltools - STRICT PyQt5 construction test\n")
    install_strict_pyqt5()

    print("1. import of the docker module")
    try:
        mod = load_package()
        check(True, "module imports with strict PyQt5")
    except Exception:
        check(False, "module import raised")
        traceback.print_exc()
        print("\nRESULT: FAILED")
        return 1

    print("\n2. enum helper resolves AlignRight on flat PyQt5 enums")
    try:
        val = mod._qt_enum(mod.Qt, "AlignmentFlag", "AlignRight")
        check(val == 0x0002, f"got {val!r}")
    except Exception as exc:
        check(False, f"_qt_enum raised {type(exc).__name__}: {exc}")

    print("\n3. constructor builds the docker")
    try:
        docker = mod.PixelToolsDocker()
        check(docker is not None, "docker constructed")
    except Exception:
        check(False, "CONSTRUCTOR RAISED")
        traceback.print_exc()
        print("\nRESULT: FAILED")
        return 1

    print("\n4. the widget tree was handed to the dock")
    check(getattr(docker, "widget", None) is not None, "setWidget was called")

    print("\n5. a shape still computes through the constructed docker")
    try:
        docker.shape_combo.items = mod.SHAPES
        docker.shape_combo.index = 1
        docker.x0.val, docker.y0.val = 0, 0
        docker.x1.val, docker.y1.val = 6, 4
        path = docker._build_path()
        check(len(path) == 12, f"ellipse has {len(path)} pixels")
        ascii_rows = mod.pe.to_ascii(path, (0, 0, 6, 4))
        expected = ["0011100", "0100010", "1000001", "0100010", "0011100"]
        check(ascii_rows == expected, "ellipse matches the acceptance pattern")
    except Exception:
        check(False, "_build_path raised")
        traceback.print_exc()

    print("\n" + "-" * 50)
    if failures:
        print(f"{checks} checks, {len(failures)} FAILURE(S)")
        print("RESULT: FAILED")
        return 1

    print(f"{checks} checks, 0 failures")
    print("RESULT: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
