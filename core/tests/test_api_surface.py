# SPDX-License-Identifier: GPL-2.0-or-later
"""
test_api_surface.py - every Krita API call in the plugin must EXIST.

Why this exists
---------------
Three failures in a row were the same mistake in different clothes: calling a
method the Krita API does not have.

    _krita.addDockWidget(...)   # does not exist -> addDockWidgetFactory
    doc.refresh()               # does not exist -> refreshProjection()
    merged = node.mergeDown()   # exists, but returns None (void)

Each one only surfaced as a traceback in the user's Krita. Discovering
missing methods one traceback at a time is slow and disrespectful of the
user's time, so this test extracts every `object.method(...)` call from the
plugin sources, resolves the object to the API class it comes from, and
checks the method against that class's documented method list.

The lists below are transcribed from the Krita API reference
(apidoc.krita.maou-maou.fr). They are deliberately exhaustive for the
classes we touch: if a method is missing from a list here, that is a bug in
this file, to be fixed by re-reading the reference.

Run:  python3 core/tests/test_api_surface.py
"""

from __future__ import annotations

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "pykrita", "pixeltools")

failures = []
checks = 0


def check(cond, what):
    global checks
    checks += 1
    if cond:
        print("  ok  :", what)
    else:
        failures.append(what)
        print("  FAIL:", what)


# ---------------------------------------------------------------------------
# Documented API surface (transcribed from the reference)
# ---------------------------------------------------------------------------

KRITA = {  # class Krita
    "addDockWidgetFactory", "addExtension", "dockers", "documents",
    "extensions", "filters", "instance", "activeDocument", "activeWindow",
    "notifier", "version", "readSetting", "writeSetting", "createDocument",
    "krita_i18n", "krita_i18nc", "getAppDataLocation", "fromVariant",
}

DOCUMENT = {  # class Document
    "activeNode", "animationLength", "annotation", "annotationDescription",
    "annotationTypes", "audioLevel", "audioTracks", "autosave",
    "backgroundColor", "batchmode", "bounds", "clone", "close", "colorDepth",
    "colorModel", "colorProfile", "createCloneLayer", "createColorizeMask",
    "createFileLayer", "createFillLayer", "createFilterLayer",
    "createFilterMask", "createGroupLayer", "createNode",
    "createSelectionMask", "createTransformMask", "createTransparencyMask",
    "createVectorLayer", "crop", "currentTime", "documentInfo",
    "exportImage", "fileName", "flatten", "framesPerSecond",
    "fullClipRangeEndTime", "fullClipRangeStartTime", "gridConfig",
    "guidesConfig", "guidesLocked", "guidesVisible", "height",
    "horizontalGuides", "importAnimation", "isIdle", "lock", "modified",
    "name", "nodeByName", "nodeByUniqueID", "pixelData", "playBackEndTime",
    "playBackStartTime", "projection", "refreshProjection",
    "removeAnnotation", "resizeImage", "resolution", "rootNode",
    "rotateImage", "save", "saveAs", "scaleImage", "selection",
    "setActiveNode", "setAnnotation", "setAudioLevel", "setAudioTracks",
    "setAutosave", "setBackgroundColor", "setBatchmode", "setColorProfile",
    "setColorSpace", "setCurrentTime", "setDocumentInfo", "setFileName",
    "setFramesPerSecond", "setGridConfig", "setGuidesConfig",
    "setGuidesLocked", "setGuidesVisible", "setHeight",
    "setHorizontalGuides", "setModified", "setName", "setPlayBackRange",
    "setResolution", "setSelection", "setVerticalGuides", "setWidth",
    "setXOffset", "setXRes", "setYOffset", "setYRes", "shearImage",
    "thumbnail", "topLevelNodes", "tryBarrierLock", "unlock",
    "verticalGuides", "waitForDone", "width", "xOffset", "xRes", "yOffset",
    "yRes",
}

NODE = {  # class Node
    "addChildNode", "alphaLocked", "animated", "blendingMode", "bounds",
    "channels", "childNodes", "clone", "collapsed", "colorDepth",
    "colorLabel", "colorModel", "colorProfile", "cropNode", "duplicate",
    "enableAnimation", "findChildNodes", "hasExtents", "hasKeyframeAtTime",
    "icon", "index", "inheritAlpha", "isPinnedToTimeline", "layerStyleToAsl",
    "locked", "mergeDown", "move", "name", "opacity", "paintAbility",
    "paintEllipse", "paintLine", "paintPath", "paintPolygon",
    "paintRectangle", "parentNode", "pixelData", "pixelDataAtTime",
    "position", "projectionPixelData", "remove", "removeChildNode",
    "rotateNode", "save", "scaleNode", "setAlphaLocked", "setBlendingMode",
    "setChildNodes", "setCollapsed", "setColorLabel", "setColorProfile",
    "setColorSpace", "setInheritAlpha", "setLayerStyleFromAsl", "setLocked",
    "setName", "setOpacity", "setPinnedToTimeline", "setPixelData",
    "setShowInTimeline", "setVisible", "shearNode", "showInTimeline",
    "thumbnail", "type", "uniqueId", "visible",
}

SELECTION = {  # class Selection
    "add", "border", "clear", "contract", "copy", "cut", "dilate",
    "duplicate", "erode", "feather", "grow", "height", "intersect",
    "invert", "move", "paste", "pixelData", "replace", "resize", "select",
    "selectAll", "setPixelData", "shrink", "smooth", "subtract",
    "symmetricdifference", "width", "x", "y",
}

VIEW = {  # class View
    "HDRExposure", "HDRGamma", "activateResource", "backGroundColor",
    "backgroundColor", "brushFade", "brushPosition", "brushRotation",
    "brushSize", "canvas", "currentBlendingMode", "currentBrushPreset",
    "currentGradient", "currentPattern", "disablePressure", "document",
    "eraserMode", "flakeToCanvasTransform", "flakeToDocumentTransform",
    "flakeToImageTransform", "foreGroundColor", "foregroundColor",
    "globalAlphaLock", "paintingFlow", "paintingOpacity", "patternSize",
    "selectedNodes", "setBackGroundColor", "setBrushFade",
    "setBrushRotation", "setBrushSize", "setCurrentBlendingMode",
    "setCurrentBrushPreset", "setCurrentGradient", "setCurrentPattern",
    "setDisablePressure", "setDocument", "setEraserMode",
    "setForeGroundColor", "setGlobalAlphaLock", "setHDRExposure",
    "setHDRGamma", "setPaintingFlow", "setPaintingOpacity",
    "setPatternSize", "setVisible", "showFloatingMessage", "visible",
    "window",
}

MANAGED_COLOR = {  # class ManagedColor
    "colorDepth", "colorForCanvas", "colorModel", "colorProfile",
    "components", "componentsOrdered", "fromQColor", "fromXML",
    "setColorProfile", "setColorSpace", "setComponents", "toQString",
    "toXML",
}

CLASSES = {
    "Krita/Application": KRITA,
    "Document": DOCUMENT,
    "Node": NODE,
    "Selection": SELECTION,
    "View": VIEW,
    "ManagedColor": MANAGED_COLOR,
}

# Which class each conventional variable name refers to in this codebase.
# This is the mapping that makes the test possible: without it we would be
# guessing which API class `doc` is.
VAR_CLASS = {
    "doc": "Document",
    "document": "Document",
    "node": "Node",
    "target": "Node",
    "temp": "Node",
    "target_node": "Node",
    "parent": "Node",
    "sel": "Selection",
    "selection": "Selection",
    "view": "View",
    "managed": "ManagedColor",
}

# Methods that exist on the plugin's OWN classes, not on Krita objects.
OWN_ATTRS = {
    "combo", "box", "label", "layout", "widget", "status", "value", "text",
    "setValue", "setText", "setChecked", "setVisible", "setToolTip",
    "setWordWrap", "addItems", "currentText", "connect", "setRange",
    "setLabelAlignment", "addWidget", "addLayout", "addStretch", "addRow",
    "setContentsMargins", "setSpacing", "setWindowTitle", "setWidget",
    "canvasChanged", "stamp", "line", "ellipse", "ellipse_filled",
    "rectangle", "rectangle_filled", "bezier", "thicken", "bounds", "clip",
    "to_ascii", "get", "append", "split", "replace", "format",
}

# Exact source lines allowed to use these names (python/file objects).
IGNORE_VARS = {"path", "os", "sys", "datetime", "traceback", "name", "self"}


def collect_calls(path):
    """Yields (var, method, lineno) for every `var.method()` in the file."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            base = n.func.value
            if isinstance(base, ast.Name):
                yield base.id, n.func.attr, n.lineno


def main():
    print("pixeltools - Krita API surface check\n")

    bad = []
    checked_calls = 0

    for fname in sorted(os.listdir(SRC)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(SRC, fname)
        print(f"scanning {fname}")
        for var, method, lineno in collect_calls(path):
            if var in IGNORE_VARS:
                continue
            cls = VAR_CLASS.get(var)
            if cls is None:
                continue
            checked_calls += 1
            allowed = CLASSES[cls]
            if method not in allowed:
                bad.append((fname, lineno, cls, var, method))

    print()
    check(checked_calls > 20, f"scanned {checked_calls} API calls")

    if bad:
        for fname, lineno, cls, var, method in bad:
            print(f"  {fname}:{lineno}  {var}.{method}()  -> not in class {cls}")
        check(False, f"{len(bad)} call(s) to methods that do not exist")
    else:
        check(True, "every Krita API call matches a documented method")

    # The three historical mistakes, asserted explicitly so a regression
    # in this file's data cannot hide them.
    print("\nregression guards")
    check("addDockWidget" not in KRITA, "Krita has no addDockWidget")
    check("addDockWidgetFactory" in KRITA, "Krita has addDockWidgetFactory")
    check("refresh" not in DOCUMENT, "Document has no refresh()")
    check("refreshProjection" in DOCUMENT, "Document has refreshProjection()")
    check("mergeDown" in NODE, "Node has mergeDown()")

    # And the plugin must not contain them.
    print("\nsource guards")
    for fname in ("pixeltools_docker.py", "painter.py", "__init__.py"):
        with open(os.path.join(SRC, fname), encoding="utf-8") as fh:
            src = fh.read()
        check("addDockWidget(" not in src, f"{fname} does not call addDockWidget")
        check("doc.refresh()" not in src, f"{fname} does not call doc.refresh")

    print("\n" + "-" * 54)
    if failures:
        print(f"{checks} checks, {len(failures)} FAILURE(S)")
        for f in failures:
            print("  FAIL:", f)
        print("RESULT: FAILED")
        return 1
    print(f"{checks} checks, 0 failures")
    print("RESULT: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
