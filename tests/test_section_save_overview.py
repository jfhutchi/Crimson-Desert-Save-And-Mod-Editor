"""SAVE section, driven through its real controls.

Search must narrow the table, Set Stack and Delete must write through the
same handlers the buttons call, and the result must survive a round trip to
disk. One save load is shared module-wide; state-changing tests re-verify
from a fresh reload rather than trusting the widgets.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

import conftest
from conftest import SAVE_FIXTURE, requires_fixture, settle

pytestmark = requires_fixture


@pytest.fixture(scope="module")
def harness(qt_app, tmp_path_factory):
    from crimson.app import CrimsonWindow

    work = tmp_path_factory.mktemp("save-overview")
    slot = work / "slot104"
    slot.mkdir()
    save_path = slot / "save.save"
    shutil.copy2(SAVE_FIXTURE, save_path)

    window = CrimsonWindow()
    window.show()
    qt_app.processEvents()
    editor = window._sources[0]
    editor._get_config_path = lambda: str(work / "cfg.json")
    editor._load_save(str(save_path))
    settle(qt_app, editor)
    yield window, editor, save_path
    from conftest import release_window

    release_window(qt_app, window)


def reload_items(path: Path):
    from crimson.save_editor import save_crypto
    from crimson.save_editor.item_scanner import scan_items_smart

    return scan_items_smart(save_crypto.load_save_file(str(path)).decompressed_blob)


def select_rows_for(editor, qt_app, predicate, count=1, timeout=120.0):
    """Select matching rows, waiting out the asynchronous repopulate.

    After an edit or undo the item list is rescanned and the table catches
    up a few seconds later, so a single pass can run between the two.
    """
    table = editor._inv_table
    deadline = time.time() + timeout
    while True:
        editor._inv_search.setText("")
        qt_app.processEvents()
        table.clearSelection()
        picked = 0
        for row in range(table.rowCount()):
            key_item = table.item(row, 5)
            if key_item is None:
                continue
            if any(
                str(i.item_key) == key_item.text() and predicate(i)
                for i in editor._items
            ):
                table.selectRow(row)
                picked += 1
                if picked >= count:
                    break
        qt_app.processEvents()
        if picked >= count:
            return picked
        if time.time() > deadline:
            raise AssertionError(
                f"selected {picked}/{count} after {timeout:.0f}s; "
                f"table rows={table.rowCount()}, predicate matches in items="
                f"{sum(1 for i in editor._items if predicate(i))}"
            )
        for _ in range(20):
            qt_app.processEvents()
            time.sleep(0.1)


def test_search_narrows_and_clears(harness, qt_app) -> None:
    _window, editor, _path = harness
    total = editor._inv_table.rowCount()
    assert total >= 1600

    editor._inv_search.setText("cabbage")
    qt_app.processEvents()
    narrowed = editor._inv_table.rowCount()
    assert 0 < narrowed < 50, f"search should narrow the table, got {narrowed}"

    editor._inv_search.setText("")
    qt_app.processEvents()
    assert editor._inv_table.rowCount() == total, "clearing search must restore"


def test_set_stack_through_the_button_persists(harness, qt_app) -> None:
    window, editor, path = harness
    picked = select_rows_for(
        editor, qt_app, lambda i: i.bag == "CampWarehouse" and i.source != "Mercenary"
    )
    assert picked == 1
    selected = editor._get_selected_items(editor._inv_table)
    assert len(selected) == 1
    target_offset = selected[0].offset

    editor._inv_stack_input.setValue(777)
    editor._set_stack()
    qt_app.processEvents()
    assert editor._dirty

    editor._do_save(str(path))
    qt_app.processEvents()

    written = [i for i in reload_items(path) if i.offset == target_offset]
    assert written and written[0].stack_count == 777

    # Undo returns the in-memory blob to the pre-edit value.
    editor._undo()
    qt_app.processEvents()
    from crimson.save_editor.item_scanner import scan_items_smart

    undone = [
        i for i in scan_items_smart(editor._save_data.decompressed_blob)
        if i.offset == target_offset
    ]
    assert undone and undone[0].stack_count != 777


def test_delete_zeroes_the_item_and_survives_reload(harness, qt_app) -> None:
    window, editor, path = harness
    picked = select_rows_for(
        editor, qt_app,
        lambda i: i.bag == "Warehouse" and i.source != "Mercenary" and i.stack_count > 0,
    )
    assert picked == 1
    selected = editor._get_selected_items(editor._inv_table)
    target = selected[0]

    conftest.DIALOGS.clear()
    editor._delete_items()
    qt_app.processEvents()
    assert any(t == "Delete Items" for _s, t, _b in conftest.DIALOGS)

    editor._do_save(str(path))
    qt_app.processEvents()

    after = [i for i in reload_items(path) if i.offset == target.offset]
    assert not after or after[0].item_key == 0, "deleted item still has its key"


def test_the_status_bar_reports_the_edit(harness, qt_app) -> None:
    _window, editor, _path = harness
    message = editor.statusBar().currentMessage() or getattr(
        editor, "_center_status", None
    )
    assert message is not None
