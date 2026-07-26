"""The unified window must host both editors in one process.

Both apps ship modules with identical names and different contents, so the
thing most likely to break silently is import resolution: one workspace ends
up using the other's `models` or `save_crypto`. These tests pin that the
namespacing holds and that every page from both editors is reachable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "crimson" / "game_mods"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def window():
    app = QApplication.instance() or QApplication([])
    for name in ("information", "question", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    QDialog.exec = lambda self: 0

    from crimson.app import CrimsonWindow

    win = CrimsonWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


def test_both_editors_keep_their_own_core_modules() -> None:
    from crimson.game_mods.models import SaveData as ModsSaveData
    from crimson.save_editor.models import SaveData as EditorSaveData

    assert EditorSaveData is not ModsSaveData, (
        "the apps' models collided; one workspace is using the other's classes"
    )
    assert "schema_identity" in EditorSaveData.__dataclass_fields__
    assert "parse_cache" in ModsSaveData.__dataclass_fields__


def test_save_writes_use_the_transactional_path() -> None:
    from crimson.save_editor import save_crypto

    assert hasattr(save_crypto, "transactional_write_save"), (
        "the save editor's crypto module carries the verified-backup write "
        "path; the mod editor's stub must not shadow it"
    )
    source = (ROOT / "crimson" / "save_editor" / "save_crypto.py").read_text(
        encoding="utf-8-sig"
    )
    assert "Refusing to overwrite an existing save without a verified backup" in source


def test_window_hosts_both_workspaces(window) -> None:
    labels = [d.label for d in window._shell._destinations]
    assert window._router.count() >= 6, "every page from both editors is hosted"

    # Save-side sections keep their original names; game-side sections are
    # renamed so the two domains are never confused for each other.
    assert {"SAVE", "INVENTORY", "WORLD"} <= set(labels)
    assert {"GAME ITEMS", "GAME WORLD", "MODS"} <= set(labels)
    assert len(labels) == len(set(labels)), f"duplicate section names: {labels}"

    routes = {d.label: [r.label for r in d.routes] for d in window._shell._destinations}
    assert "Inventory" in routes["INVENTORY"]
    assert "Blackstar" in routes["MOUNTS"]
    assert "Game Patches" in routes["MODS"]
    total = sum(len(v) for v in routes.values())
    assert total >= 35, f"navigation lost routes: only {total}"


def test_each_workspace_keeps_its_own_settings_file(window) -> None:
    # Both editors defaulted to editor_config.json beside the executable, so
    # in one process they would overwrite each other's settings.
    paths = {Path(source._get_config_path()).name for source in window._sources}
    assert len(paths) == len(window._sources), f"settings files collide: {paths}"
    assert all(name != "editor_config.json" for name in paths)


def test_menu_actions_survive_the_move(window) -> None:
    # Each editor built its menus on its own window, which is now hidden, so
    # without merging them File > Open Save File would be unreachable.
    menus = {a.text(): a.menu() for a in window.menuBar().actions() if a.menu()}
    assert {"File", "Edit", "Help"} <= set(menus)

    file_entries = [a.text() for a in menus["File"].actions() if a.text()]
    assert any("Open Save File" in e for e in file_entries)
    assert any(e == "Save" for e in file_entries)
    assert any("Save As" in e for e in file_entries)
    # Both workspaces contribute, each under its own heading.
    assert "SAVE" in file_entries and "GAME FILES" in file_entries

    assert any("Undo" in a.text() for a in menus["Edit"].actions())


def test_side_panels_and_menu_are_reachable(window) -> None:
    # The shell hides the menu bar behind a MENU button and the browsers behind
    # SAVES/PACKS. Those commands come from each editor, so without forwarding
    # them the save browser - the main way to open a save - would be gone.
    from PySide6.QtWidgets import QAbstractButton

    buttons = {
        b.text()
        for b in window._shell.findChildren(QAbstractButton)
        if b.objectName() == "shellUtilityButton"
    }
    assert {"MENU", "SAVES", "PACKS"} <= buttons, buttons

    dock = window._sources[0]._save_dock
    assert dock.parent() is window, "docks must belong to the visible window"

    saves = next(
        b for b in window._shell.findChildren(QAbstractButton) if b.text() == "SAVES"
    )
    saves.click()
    QApplication.instance().processEvents()
    assert dock.isVisible(), "SAVES did not open the save browser"
    saves.click()
    QApplication.instance().processEvents()


def test_no_workspace_silently_failed_to_load(window) -> None:
    # _absorb logs and skips a workspace that raises; an empty destination list
    # would still build a window, so assert both actually produced pages.
    assert len(window._sources) == 2, (
        "one of the editors failed to construct; check the log for the "
        "workspace that was skipped"
    )
