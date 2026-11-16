# SPDX-License-Identifier: GPL-2.0-or-later
"""
ksprite - a pixel art editor inside Krita.

A Qt widget embedded in the Pixel Tools docker that renders one layer of the
active document, handles the mouse itself, and writes the result back. This
is the only way to get "drag to draw with a live preview" from a Python
plugin: PyKrita exposes no canvas mouse events and no canvas-to-image
conversion, but a Qt widget receives mouse events normally.

Modules:
    buffer   - the writable pixel copy of a layer (no Qt, fully testable)
    tools    - the drawing tools as pure functions and a small state machine
    canvas   - the QWidget that renders and handles the mouse
"""
