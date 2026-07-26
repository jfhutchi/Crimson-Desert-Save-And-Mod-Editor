"""INVENTORY section: each route holds real data or explains itself.

Item Swap offers the full item database next to a selector fed from the
save, Sockets reads the equipped weapon's sockets, and Dye - which only
lists items after its explicit Load Dye Data step - must say so rather than
sit silently empty.
"""
from __future__ import annotations

import time

from PySide6.QtWidgets import QLabel, QTableView, QTableWidget

from conftest import requires_fixture

pytestmark = requires_fixture


def open_route(window, qt_app, section: str, label: str):
    destination = next(
        d for d in window._shell._destinations if d.label == section
    )
    route = next(r for r in destination.routes if r.label == label)
    window._router.setCurrentIndex(route.primary_index)
    if route.section_tabs is not None:
        route.section_tabs.setCurrentIndex(route.section_index)
    for _ in range(30):
        qt_app.processEvents()
        time.sleep(0.02)
    if route.section_tabs is not None:
        return route.section_tabs.widget(route.section_index)
    return window._router.widget(route.primary_index)


def test_item_swap_offers_the_full_database(qt_app, window, editor) -> None:
    from PySide6.QtWidgets import QAbstractItemView

    page = open_route(window, qt_app, "INVENTORY", "Item Swap")
    # The database table fills in asynchronously after the name db warms.
    deadline = time.time() + 60
    db_rows, selector_rows = 0, 0
    while time.time() < deadline:
        qt_app.processEvents()
        db_rows = max(
            (t.rowCount() for t in page.findChildren(QTableWidget)), default=0
        )
        selector_rows = max(
            (
                v.model().rowCount()
                for v in page.findChildren(QAbstractItemView)
                if not isinstance(v, QTableWidget) and v.model() is not None
            ),
            default=0,
        )
        if db_rows > 6000 and selector_rows > 0:
            break
        time.sleep(0.1)
    assert db_rows > 6000, f"the item database side never filled: {db_rows}"
    assert selector_rows > 0, "the save-side selector is empty"


def test_sockets_reads_the_equipped_item(qt_app, window, editor) -> None:
    page = open_route(window, qt_app, "INVENTORY", "Sockets")
    labels = " | ".join(
        l.text() for l in page.findChildren(QLabel) if l.text().strip()
    )
    assert "Sockets:" in labels, f"no socket summary rendered: {labels[:200]}"
    assert "filled" in labels, "socket fill state missing from the summary"


def test_dye_explains_its_load_step(qt_app, window, editor) -> None:
    page = open_route(window, qt_app, "INVENTORY", "Dye")
    text = " ".join(
        l.text() for l in page.findChildren(QLabel) if l.text().strip()
    )
    assert "Load Dye Data" in text, (
        "the dye page is empty without saying how to fill it"
    )
