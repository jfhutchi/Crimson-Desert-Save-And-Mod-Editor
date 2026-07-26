"""Loading a real save must work inside the unified window.

The save editor runs hidden inside the shared window, so its pages are
reparented and its handlers fire from a different owner than before. This
drives the real load path end to end against a current-patch save.
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

EXPECTED_TABS = {
    "All": 1662, "Equipment": 19, "Inventory": 204, "Quest": 122,
    "Camp Warehouse": 253, "Warehouse": 23, "Bank": 50, "Kuku": 165,
    "Money": 15, "Vendor": 246, "Mercenary": 259,
}

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

pytestmark = pytest.mark.skipif(
    not FIXTURE.is_file(), reason="current-patch save fixture is not available"
)


@pytest.fixture(scope="module")
def loaded_editor(tmp_path_factory):
    app = QApplication.instance() or QApplication([])
    for name in ("information", "question", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    QDialog.exec = lambda self: 0

    from crimson.app import CrimsonWindow

    work = tmp_path_factory.mktemp("unified-load")
    slot = work / "slot104"
    slot.mkdir()
    shutil.copy2(FIXTURE, slot / "save.save")

    window = CrimsonWindow()
    window.show()
    app.processEvents()
    editor = window._sources[0]
    assert editor._save_data is None, "the app must not reopen a stale save"

    editor._get_config_path = lambda: str(work / "cfg.json")
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
    else:
        pytest.fail("the save never finished loading")
    app.processEvents()

    yield editor
    window.close()
    app.processEvents()


def test_every_item_loads_in_the_unified_window(loaded_editor) -> None:
    assert len(loaded_editor._items) == 1662
    assert loaded_editor._inv_table.rowCount() == 1662


def test_every_bag_tab_reports_its_count(loaded_editor) -> None:
    labels = [
        loaded_editor._inv_subtabs.tabText(i)
        for i in range(loaded_editor._inv_subtabs.count())
    ]
    for name, count in EXPECTED_TABS.items():
        assert f"{name} ({count})" in labels, f"{name} should report {count}: {labels}"


def test_the_save_stays_writable(loaded_editor) -> None:
    assert loaded_editor._save_data.is_schema_supported
    assert loaded_editor._save_data.compatibility_profile_id == "community-2026-07-patch"
