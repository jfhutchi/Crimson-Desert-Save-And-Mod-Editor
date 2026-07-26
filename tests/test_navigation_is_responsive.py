"""Switching sections must never block the GUI thread.

Both editors previously did real work on tab activation - the save editor
froze for 4.3s opening Items because it re-parsed the whole save. Every
section and route is exercised here with a save loaded, since an empty editor
would not reach the expensive paths.
"""
from __future__ import annotations

import time

from conftest import requires_fixture

pytestmark = requires_fixture

# A switch slower than this reads as a freeze rather than a transition.
STALL_SECONDS = 0.4


def test_no_section_or_route_stalls_the_gui(qt_app, window, editor) -> None:
    destinations = list(window._shell._destinations)
    stalls = []
    exercised = 0
    for index, destination in enumerate(destinations):
        start = time.time()
        window._shell.activate_destination(index)
        qt_app.processEvents()
        if time.time() - start > STALL_SECONDS:
            stalls.append((destination.label, time.time() - start))
        for route in destination.routes:
            start = time.time()
            window._router.setCurrentIndex(route.primary_index)
            qt_app.processEvents()
            elapsed = time.time() - start
            exercised += 1
            if elapsed > STALL_SECONDS:
                stalls.append((f"{destination.label}/{route.label}", elapsed))

    assert exercised >= 30, f"only {exercised} routes were reachable"
    assert not stalls, "sections froze the GUI: " + ", ".join(
        f"{name} {seconds * 1000:.0f}ms" for name, seconds in stalls
    )
