# SPDX-License-Identifier: GPL-2.0-or-later
"""
pixeltools - geometrically correct pixel-art tools for Krita.

Entry point: Krita imports this package and calls its module-level code,
which registers the docker.
"""

from krita import Krita

from .pixeltools_docker import PixelToolsDocker

_instance = None

try:
    _krita = Krita.instance()
    if _krita is not None:
        _instance = PixelToolsDocker()
        _krita.addDockWidget(_instance)
except Exception as exc:  # noqa: BLE001 - never break Krita startup
    # A plugin that raises on import can prevent Krita from starting cleanly,
    # so failure is reported rather than propagated.
    print(f"pixeltools: failed to register docker: {exc}")


def __getattr__(name):
    # Lets the Scripter reach the live docker for debugging:
    #   from pixeltools import docker; docker.status.text()
    if name == "docker":
        return _instance
    raise AttributeError(name)
