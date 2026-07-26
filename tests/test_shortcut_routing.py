"""Shared shortcuts must reach the workspace the user is looking at.

Both editors bind Ctrl+S on actions parented to their own hidden windows.
Left alone, the shortcut is ambiguous or fires the editor the user is not
using - "No file loaded." with a save plainly loaded. Each shared shortcut is
now one application-level action that dispatches by the visible page's owner.

The offscreen platform does not deliver shortcut keystrokes, so these tests
trigger the dispatch actions directly; a real-keypress check was done once on
the desktop platform.
"""
from __future__ import annotations

import conftest
from conftest import requires_fixture

pytestmark = requires_fixture


def _ctrl_s_actions(window):
    return [a for a in window.actions() if a.shortcut().toString() == "Ctrl+S"]


def test_exactly_one_ctrl_s_owner(window) -> None:
    assert len(_ctrl_s_actions(window)) == 1, (
        "Ctrl+S must have exactly one owner or Qt fires nobody"
    )


def test_ctrl_s_reaches_the_save_editor_on_a_save_page(qt_app, window, editor) -> None:
    save_routes = window._shell._destinations[0].routes
    window._router.setCurrentIndex(save_routes[0].primary_index)
    qt_app.processEvents()

    conftest.DIALOGS.clear()
    _ctrl_s_actions(window)[0].trigger()
    qt_app.processEvents()

    titles = [title for _sev, title, _body in conftest.DIALOGS]
    assert "Write Save with Verified Backup" in titles, (
        f"the save editor's write flow did not start: {conftest.DIALOGS}"
    )


def test_ctrl_s_reaches_the_mod_editor_on_a_mods_page(qt_app, window, editor) -> None:
    mods = next(d for d in window._shell._destinations if d.label == "MODS")
    window._router.setCurrentIndex(mods.routes[0].primary_index)
    qt_app.processEvents()

    conftest.DIALOGS.clear()
    _ctrl_s_actions(window)[0].trigger()
    qt_app.processEvents()

    # The mod editor has no file loaded here, and saying so is correct - the
    # bug was saying it while the user was on a save-editor page.
    assert conftest.DIALOGS, "nothing handled Ctrl+S on a mods page"
    severity, title, _body = conftest.DIALOGS[0]
    assert (severity, title) == ("warning", "Save")
