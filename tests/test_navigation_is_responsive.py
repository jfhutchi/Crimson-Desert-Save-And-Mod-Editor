"""Switching sections must never block the GUI thread.

Both editors previously did real work on tab activation - the save editor
froze for 4.3s opening Items because it re-parsed the whole save. Every
section and route is exercised here with a save loaded, since an empty editor
would not reach the expensive paths.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "crimson" / "game_mods"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

FIXTURE = Path(
    r"E:\Documents\GitHub\CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS"
    r"\tests\fixtures\slot104\save.save"
)

# A switch slower than this reads as a freeze rather than a transition.
STALL_SECONDS = 0.4

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

pytestmark = pytest.mark.skipif(
    not FIXTURE.is_file(), reason="current-patch save fixture is not available"
)


def test_no_section_or_route_stalls_the_gui(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    for name in ("information", "question", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    QDialog.exec = lambda self: 0

    from crimson.app import CrimsonWindow

    slot = tmp_path / "slot104"
    slot.mkdir()
    shutil.copy2(FIXTURE, slot / "save.save")

    window = CrimsonWindow()
    window.show()
    app.processEvents()

    editor = window._sources[0]
    editor._get_config_path = lambda: str(tmp_path / "cfg.json")
    editor._load_save(str(slot / "save.save"))
    deadline = time.time() + 240
    while time.time() < deadline:
        app.processEvents()
        labels = [
            editor._inv_subtabs.tabText(i)
            for i in range(editor._inv_subtabs.count())
        ]
        if editor._save_data is not None and not any(
            "loading" in label.lower() for label in labels
        ):
            break
        time.sleep(0.05)
    for _ in range(40):
        app.processEvents()
        time.sleep(0.03)

    destinations = list(window._shell._destinations)
    stalls = []
    exercised = 0
    for index, destination in enumerate(destinations):
        start = time.time()
        window._shell.activate_destination(index)
        app.processEvents()
        if time.time() - start > STALL_SECONDS:
            stalls.append((destination.label, time.time() - start))
        for route in destination.routes:
            start = time.time()
            window._router.setCurrentIndex(route.primary_index)
            app.processEvents()
            elapsed = time.time() - start
            exercised += 1
            if elapsed > STALL_SECONDS:
                stalls.append((f"{destination.label}/{route.label}", elapsed))

    window.close()
    app.processEvents()

    assert exercised >= 30, f"only {exercised} routes were reachable"
    assert not stalls, "sections froze the GUI: " + ", ".join(
        f"{name} {seconds * 1000:.0f}ms" for name, seconds in stalls
    )
