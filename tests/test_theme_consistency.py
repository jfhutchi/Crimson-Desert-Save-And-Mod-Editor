"""No page may keep hardcoded button colours.

Several tabs shipped with their own green/blue/red palettes. The shell folds
those into the shared action hierarchy when it installs, but the mod editor
builds its tabs lazily, so pages created later - the Stacker Tool most
visibly - kept looking like a different application.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "crimson" / "game_mods"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton, QApplication, QDialog, QMessageBox,
)

HARDCODED = re.compile(
    r"background(?:-color)?\s*:\s*(?:#[0-9a-f]{3,8}|rgba?\s*\()", re.IGNORECASE
)


def test_every_route_uses_the_shared_button_styling() -> None:
    app = QApplication.instance() or QApplication([])
    for name in ("information", "question", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    QDialog.exec = lambda self: 0

    from crimson.app import CrimsonWindow

    window = CrimsonWindow()
    window.show()
    app.processEvents()

    offenders: dict[str, list[str]] = {}
    visited = 0
    for destination in window._shell._destinations:
        for route in destination.routes:
            window._router.setCurrentIndex(route.primary_index)
            if route.section_tabs is not None:
                route.section_tabs.setCurrentIndex(route.section_index)
            app.processEvents()
            visited += 1
            page = window._router.widget(route.primary_index)
            bad = [
                button.text() or "<icon>"
                for button in page.findChildren(QAbstractButton)
                if HARDCODED.search(button.styleSheet())
            ]
            if bad:
                offenders[f"{destination.label}/{route.label}"] = bad

    window.close()
    app.processEvents()

    assert visited >= 30, f"only {visited} routes were reachable"
    assert not offenders, "pages keep hardcoded button colours: " + "; ".join(
        f"{where}: {labels[:4]}" for where, labels in offenders.items()
    )
