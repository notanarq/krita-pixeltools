# SPDX-License-Identifier: GPL-2.0-or-later
"""
pixeltools - geometrically correct pixel-art tools for Krita.

Entry point: Krita imports this package and runs the module-level code, which
registers a docker FACTORY with the application.

Why a factory and not an instance
---------------------------------
``Krita.addDockWidget`` does not exist. The Krita scripting API only offers
``addDockWidgetFactory`` (verified against the API reference for the Krita
class, which lists addDockWidgetFactory, addExtension, dockers(), documents()
- and no addDockWidget at all) and every working plugin, including the
blank template shipped by the Krita Scripting School, registers this way:

    Krita.instance().addDockWidgetFactory(
        DockWidgetFactory("someId", DockWidgetFactoryBase.DockRight, MyDocker))

Krita then instantiates the docker itself, once per window, and lists it in
Settings > Dockers. Handing it a pre-built instance silently does nothing,
which produced the reported symptom: the plugin showed as enabled in the
Python Plugins Manager while no docker ever appeared.

Why this file is defensive
--------------------------
A failure during import leaves the plugin enabled in the manager (that flag
lives in Krita's config, not in the plugin's health) with no docker, which
is invisible and impossible to diagnose from the UI. So this module:

  1. logs every load attempt, successful or not, with a full traceback;
  2. falls back to a minimal docker that DISPLAYS the error;
  3. exposes diagnostics() for Krita's Scripter.

Nothing here may raise: the worst case is a docker that did not register,
never a Krita that will not start.

Log locations, first writable wins:
    <plugin dir>/pixeltools.log
    ~/.local/share/krita/pixeltools.log
    /tmp/pixeltools.log

Live report, in Krita's Scripter (Tools > Scripts > Scripter):
    import pixeltools; print(pixeltools.diagnostics())
"""

import os
import sys
import traceback

__version__ = "2.0.0"

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# Unique id Krita uses to remember this docker. Keep it stable: changing it
# makes Krita forget the user's docked position and visibility settings.
DOCKER_ID = "pixeltools"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log_paths():
    """Candidate log files, in the order we want them."""
    home = os.path.expanduser("~")
    return [
        os.path.join(PLUGIN_DIR, "pixeltools.log"),
        os.path.join(home, ".local", "share", "krita", "pixeltools.log"),
        os.path.join("/tmp", "pixeltools.log"),
    ]


def _write_log(text):
    """Writes to the first writable candidate. Returns the path or None."""
    for path in _log_paths():
        try:
            parent = os.path.dirname(path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text)
            return path
        except Exception:  # noqa: BLE001 - logging must never break the plugin
            continue
    return None


def _log(header, exc=None):
    import datetime

    lines = [
        "",
        "=" * 68,
        f"{datetime.datetime.now().isoformat(timespec='seconds')}  {header}",
        f"  plugin dir : {PLUGIN_DIR}",
        f"  version    : {__version__}",
        f"  python     : {sys.version.split()[0]}",
        f"  executable : {sys.executable}",
    ]
    try:
        import PyQt5  # noqa: F401

        lines.append("  binding    : PyQt5 (Krita 5.x)")
    except Exception:  # noqa: BLE001
        try:
            import PyQt6  # noqa: F401

            lines.append("  binding    : PyQt6 (Krita 6.x)")
        except Exception:  # noqa: BLE001
            lines.append("  binding    : NONE FOUND")
    if exc is not None:
        lines.append(
            "  ERROR      : "
            + "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip()
        )
    lines.append("=" * 68)
    _write_log("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_registered = False
_error = None


def _make_error_docker(exc):
    """Builds a minimal docker that displays a load failure.

    Uses only the oldest, most stable slice of the Qt API (a label inside a
    widget) so it has the best chance of being constructible even when the
    real docker was not.
    """
    from krita import DockWidget

    try:
        from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget  # Krita 5.x
    except ImportError:
        from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget  # Krita 6.x

    detail = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    ).rstrip()

    class ErrorDocker(DockWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Pixel Tools (erro)")
            root = QWidget()
            layout = QVBoxLayout(root)
            label = QLabel(
                "Pixel Tools nao conseguiu carregar.\n\n"
                + repr(exc)
                + "\n\nDetalhes completos em:\n"
                + str(_write_log("") or "(log nao gravavel)")
                + "\n\nRelatorio: abra o Scripter e rode\n"
                "  import pixeltools; print(pixeltools.diagnostics())"
            )
            label.setWordWrap(True)
            layout.addWidget(label)
            layout.addStretch(1)
            self.setWidget(root)
            self._detail = detail

        def canvasChanged(self, canvas):
            return

    return ErrorDocker


def _register(docker_class, docker_id):
    """Registers a docker class with Krita and returns True on success.

    This is the ONLY supported way to add a docker: a factory, not an
    instance. Krita builds the widget for each window and exposes it under
    Settings > Dockers.
    """
    from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita

    factory = DockWidgetFactory(
        docker_id,
        DockWidgetFactoryBase.DockPosition.DockRight,
        docker_class,
    )
    Krita.instance().addDockWidgetFactory(factory)
    return True


try:
    from .pixeltools_docker import PixelToolsDocker

    _register(PixelToolsDocker, DOCKER_ID)
    _registered = True
    _log("docker factory registered OK (id=%s)" % DOCKER_ID)

except Exception as _exc:  # noqa: BLE001 - never break Krita startup
    _error = _exc
    _log("DOCKER REGISTRATION FAILED", _exc)
    print(f"pixeltools: failed to register docker: {_exc}", file=sys.stderr)
    traceback.print_exc()

    # Last resort: register a docker that SHOWS the failure, so the plugin is
    # never silently absent.
    try:
        fallback = _make_error_docker(_exc)
        _register(fallback, DOCKER_ID + "error")
        _log("registered fallback ERROR docker")
    except Exception as _exc2:  # noqa: BLE001
        _log("fallback error docker also failed", _exc2)


# ---------------------------------------------------------------------------
# Scripter helpers
# ---------------------------------------------------------------------------


def diagnostics():
    """Human-readable state report. Run in Krita's Scripter:

        import pixeltools; print(pixeltools.diagnostics())
    """
    out = [
        "pixeltools " + __version__,
        "  dir        : " + PLUGIN_DIR,
        "  registered : " + ("yes" if _registered else "NO"),
        "  error      : " + (repr(_error) if _error else "none"),
        "  log file   : " + str(_write_log("") or "unwritable"),
    ]
    if _error is not None:
        out.append("  traceback:")
        out.extend(
            "    " + line
            for line in "".join(
                traceback.format_exception(type(_error), _error, _error.__traceback__)
            ).rstrip().splitlines()
        )
    return "\n".join(out)


def __getattr__(name):
    # `pixeltools.docker` was a live widget in 1.1.0. With a factory Krita
    # owns the instance, so we surface the list Krita reports instead of
    # pretending to hold one.
    if name == "docker":
        try:
            from krita import Krita

            return [d for d in Krita.instance().dockers()
                    if d.objectName() == DOCKER_ID]
        except Exception:  # noqa: BLE001
            return []
    raise AttributeError(name)
