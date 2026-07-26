"""Shared harness for tests that drive the real application window.

Everything here works on copies of a save fixture. No test may touch a real
save directory or the installed game.
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

SAVE_FIXTURE = Path(
    r"E:\Documents\GitHub\CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS"
    r"\tests\fixtures\slot104\save.save"
)

requires_fixture = pytest.mark.skipif(
    not SAVE_FIXTURE.is_file(), reason="current-patch save fixture is unavailable"
)


# Every dialog the app would have shown, as (severity, title, body).
DIALOGS: list[tuple[str, str, str]] = []


@pytest.fixture(scope="session")
def qt_app():
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    app = QApplication.instance() or QApplication([])

    def record(severity, answer):
        def stub(*args, **kwargs):
            title = str(args[1]) if len(args) > 1 else ""
            body = str(args[2]) if len(args) > 2 else ""
            DIALOGS.append((severity, title, body))
            return answer
        return staticmethod(stub)

    # Tests must never block on a dialog, but what would have been shown is
    # recorded so a sweep can assert no page reported an error.
    QMessageBox.information = record("information", QMessageBox.Ok)
    QMessageBox.warning = record("warning", QMessageBox.Yes)
    QMessageBox.critical = record("critical", QMessageBox.Ok)
    QMessageBox.question = record("question", QMessageBox.Yes)
    QDialog.exec = lambda self: 0
    return app


def settle(app, editor, timeout: float = 240.0, after_generation: int | None = None) -> None:
    """Wait until a save has finished loading and its tabs show real counts.

    Pass ``after_generation`` when a reload is expected: the editor is already
    holding a save, so waiting only for "not loading" can return before the
    new load has even started.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if after_generation is not None:
            current = getattr(editor._save_data, "document_generation", None)
            if editor._save_data is None or current == after_generation:
                app.processEvents()
                time.sleep(0.05)
                continue
        app.processEvents()
        labels = [
            editor._inv_subtabs.tabText(i)
            for i in range(editor._inv_subtabs.count())
        ]
        if editor._save_data is not None and not any(
            "loading" in label.lower() for label in labels
        ):
            for _ in range(20):
                app.processEvents()
                time.sleep(0.02)
            return
        time.sleep(0.05)
    raise AssertionError("the save never finished loading")


@pytest.fixture
def save_copy(tmp_path) -> Path:
    """An isolated slot directory holding a copy of the fixture save."""
    slot = tmp_path / "slot104"
    slot.mkdir()
    destination = slot / "save.save"
    shutil.copy2(SAVE_FIXTURE, destination)
    return destination


@pytest.fixture
def window(qt_app, tmp_path):
    from crimson.app import CrimsonWindow

    win = CrimsonWindow()
    win.show()
    qt_app.processEvents()
    # Keep every editor's settings inside the temp directory.
    for index, source in enumerate(win._sources):
        source._get_config_path = (
            lambda i=index: str(tmp_path / f"config_{i}.json")
        )
    yield win
    win.close()
    qt_app.processEvents()


@pytest.fixture
def editor(qt_app, window, save_copy):
    """The save editor workspace with the copied save loaded."""
    save_editor = window._sources[0]
    save_editor._load_save(str(save_copy))
    settle(qt_app, save_editor)
    return save_editor
