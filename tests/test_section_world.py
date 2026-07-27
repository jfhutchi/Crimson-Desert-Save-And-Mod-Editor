"""WORLD section: knowledge scan and the learn/unlearn write path.

Knowledge injection was an early freeze complaint, and it writes into the
save, so the whole cycle runs here: scan the loaded save, learn one
unlearned entry, see its status flip, and unlearn it again. Selection uses
the real table and the real buttons' handlers.
"""
from __future__ import annotations

import time

import conftest
from conftest import requires_fixture

pytestmark = requires_fixture


def open_knowledge(window, qt_app):
    world = next(d for d in window._shell._destinations if d.label == "WORLD")
    route = next(r for r in world.routes if r.label == "Knowledge")
    window._router.setCurrentIndex(route.primary_index)
    if route.section_tabs is not None:
        route.section_tabs.setCurrentIndex(route.section_index)
    for _ in range(20):
        qt_app.processEvents()
        time.sleep(0.02)


def wait_rows(qt_app, table, timeout=120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        qt_app.processEvents()
        if table.rowCount() > 0:
            return
        time.sleep(0.1)
    raise AssertionError("the knowledge table never populated after Scan Save")


def row_status(table, row: int) -> str:
    item = table.item(row, 4)
    return item.text() if item else ""


def test_knowledge_learn_then_unlearn_round_trip(qt_app, window, editor) -> None:
    open_knowledge(window, qt_app)
    assert editor._know_table.rowCount() == 0, (
        "knowledge should wait for its explicit Scan Save step"
    )

    editor._know_scan()
    wait_rows(qt_app, editor._know_table)
    table = editor._know_table
    total = table.rowCount()
    assert total > 100, f"scan found implausibly few entries: {total}"

    # Status column values are "-" (unlearned) and "Learned".
    row = next(
        (r for r in range(table.rowCount()) if row_status(table, r).strip() == "-"),
        None,
    )
    assert row is not None, "no unlearned knowledge in the fixture to exercise"
    key = table.item(row, 0).text()

    table.clearSelection()
    table.selectRow(row)
    qt_app.processEvents()

    def row_of(wanted_key):
        return next(
            (
                r for r in range(table.rowCount())
                if table.item(r, 0) and table.item(r, 0).text() == wanted_key
            ),
            None,
        )

    def wait_status(wanted_key, expected, timeout=180.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            qt_app.processEvents()
            row_now = row_of(wanted_key)
            if row_now is not None and row_status(table, row_now).strip() == expected:
                return row_now
            time.sleep(0.1)
        row_now = row_of(wanted_key)
        raise AssertionError(
            f"status never became {expected!r}: "
            f"{row_status(table, row_now) if row_now is not None else 'row gone'!r}; "
            f"dialogs={conftest.DIALOGS[-3:]}"
        )

    conftest.DIALOGS.clear()
    editor._know_learn_selected()
    learned_row = wait_status(key, "Learned")
    assert editor._dirty, "learning did not mark the save dirty"

    table.clearSelection()
    table.selectRow(learned_row)
    qt_app.processEvents()
    editor._know_unlearn_selected()
    wait_status(key, "-")
