"""No page may keep hardcoded button colours.

Several tabs shipped with their own green/blue/red palettes. The shell folds
those into the shared action hierarchy when it installs, but the mod editor
builds its tabs lazily, so pages created later - the Stacker Tool most
visibly - kept looking like a different application.
"""
from __future__ import annotations

import re

HARDCODED = re.compile(
    r"background(?:-color)?\s*:\s*(?:#[0-9a-f]{3,8}|rgba?\s*\()", re.IGNORECASE
)


def test_every_route_uses_the_shared_button_styling(qt_app, window) -> None:
    from PySide6.QtWidgets import QAbstractButton

    offenders: dict[str, list[str]] = {}
    visited = 0
    for destination in window._shell._destinations:
        for route in destination.routes:
            window._router.setCurrentIndex(route.primary_index)
            if route.section_tabs is not None:
                route.section_tabs.setCurrentIndex(route.section_index)
            qt_app.processEvents()
            visited += 1
            page = window._router.widget(route.primary_index)
            bad = [
                button.text() or "<icon>"
                for button in page.findChildren(QAbstractButton)
                if HARDCODED.search(button.styleSheet())
            ]
            if bad:
                offenders[f"{destination.label}/{route.label}"] = bad

    assert visited >= 30, f"only {visited} routes were reachable"
    assert not offenders, "pages keep hardcoded button colours: " + "; ".join(
        f"{where}: {labels[:4]}" for where, labels in offenders.items()
    )
