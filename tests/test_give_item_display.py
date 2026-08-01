"""What the Warehouse tab actually shows after Give Item.

The cached-object tests check `SaveItem.item_key`. The user reads the Name
column. These check the rendered cell.
"""
from __future__ import annotations

import pytest

import conftest
from conftest import requires_fixture

pytestmark = requires_fixture

WAREHOUSE_TAB = 5  # gui.py:4430 _inv_subtab_filters


def row_of(table, item):
    for row in range(table.rowCount()):
        cell = table.item(row, 1)
        if cell is not None and cell.data(0x0100) == id(item):  # Qt.UserRole
            return row
    return None


def run_give(qt_app, editor, donor, target_key, target_count):
    from PySide6.QtWidgets import QDialog
    from crimson.save_editor import gui as gui_mod

    class Stub:
        def __init__(self, *a, **k) -> None:
            pass

        def exec(self):
            return QDialog.Accepted

    Stub.target_key = target_key
    Stub.target_count = target_count
    Stub.donor_item = donor

    saved = gui_mod.GiveItemDialog
    gui_mod.GiveItemDialog = Stub
    try:
        editor._give_item()
        qt_app.processEvents()
    finally:
        gui_mod.GiveItemDialog = saved


def pick_donor_and_target(editor):
    """A warehouse donor whose target has a different name AND category."""
    donor = next(
        i for i in editor._items
        if i.bag == "Warehouse" and i.parc_parsed and i.stack_count >= 1
    )
    target = next(
        i for i in editor._items
        if i.name and i.name != donor.name and i.category != donor.category
    )
    return donor, target


def test_warehouse_row_shows_the_item_the_user_asked_for(qt_app, editor) -> None:
    editor._inv_subtabs.setCurrentIndex(WAREHOUSE_TAB)
    qt_app.processEvents()
    donor, target = pick_donor_and_target(editor)
    old_name, old_category = donor.name, donor.category

    run_give(qt_app, editor, donor, target.item_key, donor.stack_count + 7)

    table = editor._inv_table
    row = row_of(table, donor)
    assert row is not None, (
        f"the donor row left the Warehouse tab; dialogs: {conftest.DIALOGS[-3:]}"
    )
    shown_name = table.item(row, 1).text()
    shown_key = table.item(row, 5).text()
    shown_category = table.item(row, 4).text()

    assert shown_key == str(target.item_key), (
        f"the key column did not follow the swap: {shown_key}"
    )
    assert shown_name != old_name, (
        f"the Warehouse row still reads '{shown_name}' after being turned into "
        f"'{target.name}' (key column says {shown_key}). The swap looks like it "
        f"did nothing, which is the exact bug 6c94dd4 claims to fix."
    )
    assert shown_name == target.name, (
        f"name column shows '{shown_name}', expected '{target.name}'"
    )
    assert shown_category == target.category, (
        f"category column still reads '{shown_category}' (was '{old_category}'), "
        f"expected '{target.category}' - the row keeps the old category colour"
    )


def test_search_finds_the_new_item_not_the_old_one(qt_app, editor) -> None:
    """Warehouse tab, then type the new item's name in Search."""
    editor._inv_subtabs.setCurrentIndex(WAREHOUSE_TAB)
    qt_app.processEvents()
    donor, target = pick_donor_and_target(editor)
    old_name = donor.name

    run_give(qt_app, editor, donor, target.item_key, donor.stack_count + 7)

    editor._inv_search.setText(target.name)
    editor._populate_inventory()
    qt_app.processEvents()
    assert row_of(editor._inv_table, donor) is not None, (
        f"searching the Warehouse tab for '{target.name}' does not find the item "
        f"the user just created; the row is still indexed under '{old_name}'"
    )
