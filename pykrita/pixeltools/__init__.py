# SPDX-License-Identifier: GPL-2.0-or-later
"""
pixeltools - geometrically correct pixel-art tools for Krita.

Entry point: Krita imports this package and calls its module-level code,
which registers the docker.

Why this file is defensive
--------------------------
A plugin that raises during import cannot register its docker, and Krita's
Python Plugins Manager can still show it as ticked, because the "enabled"
flag lives in Krita's config rather than in the plugin's health. The visible
result is exactly "says it is active, nothing appears in Settings > Dockers".

That failure mode is invisible unless the plugin reports on itself, so this
module:

  1. logs every load attempt, successful or not, with a full traceback;
  2. falls back to a minimal docker that DISPLAYS the error, so the plugin
     is never silently absent;
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

__version__ = "1.1.0"

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


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

_instance = None
_error = None


def _make_error_docker(exc):
    """Builds a minimal docker that displays a load failure.

    Uses only the oldest, most stable slice of the Qt API (a label inside a
    widget) so it has the best chance of being constructible even when the
    main docker was not. The Qt imports are repeated here on purpose: the
    failure being reported may well be that very import.
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
            try:
                label.setTextInteractionFlags(0x1)  # TextSelectableByMouse
            except Exception:  # noqa: BLE001
                pass
            layout.addWidget(label)
            layout.addStretch(1)
            self.setWidget(root)
            self._detail = detail

        def canvasChanged(self, canvas):
            return

    return ErrorDocker()


try:
    from krita import Krita

    from .pixeltools_docker import PixelToolsDocker

    _krita = Krita.instance()
    if _krita is None:
        raise RuntimeError("Krita.instance() returned None during plugin load")

    _instance = PixelToolsDocker()
    _krita.addDockWidget(_instance)
    _log("docker registered OK")

except Exception as _exc:  # noqa: BLE001 - never break Krita startup
    _error = _exc
    _log("DOCKER REGISTRATION FAILED", _exc)
    print(f"pixeltools: failed to register docker: {_exc}", file=sys.stderr)
    traceback.print_exc()

    # Last resort: register a docker that SHOWS the failure. A plugin that
    # reports nothing is indistinguishable from a plugin that was never
    # loaded, which is the worst possible outcome for the user.
    try:
        from krita import Krita as _K2

        _instance = _make_error_docker(_exc)
        _K2.instance().addDockWidget(_instance)
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
        "  dir      : " + PLUGIN_DIR,
        "  docker   : " + (type(_instance).__name__ if _instance else "NOT REGISTERED"),
        "  error    : " + (repr(_error) if _error else "none"),
        "  log file : " + str(_write_log("") or "unwritable"),
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
    # PEP 562: `pixeltools.docker` resolves to the live widget. This ONLY
    # works while no module-level name 'docker' exists - a def docker()
    # would shadow it, because __getattr__ runs only after normal lookup
    # fails.
    if name == "docker":
        return _instance
    raise AttributeError(name)
