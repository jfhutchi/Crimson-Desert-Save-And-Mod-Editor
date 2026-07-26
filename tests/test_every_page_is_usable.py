"""Visit every page with a save loaded and catch what only shows up in use.

A page that raises, logs an error, or pops a critical dialog looks fine in a
structural test and broken in the user's hands. This walks the whole
application the way a person would.
"""
from __future__ import annotations

import logging

import pytest

import conftest
from conftest import requires_fixture

pytestmark = requires_fixture


def test_no_page_raises_logs_or_reports_an_error(qt_app, editor, window, caplog) -> None:
    conftest.DIALOGS.clear()
    caplog.set_level(logging.ERROR)

    visited = 0
    failures: list[str] = []
    for index, destination in enumerate(window._shell._destinations):
        window._shell.activate_destination(index)
        qt_app.processEvents()
        for route in destination.routes:
            where = f"{destination.label}/{route.label}"
            try:
                window._router.setCurrentIndex(route.primary_index)
                if route.section_tabs is not None:
                    route.section_tabs.setCurrentIndex(route.section_index)
                qt_app.processEvents()
            except Exception as exc:  # noqa: BLE001 - reporting, not handling
                failures.append(f"{where} raised {type(exc).__name__}: {exc}")
                continue
            visited += 1
            page = window._router.widget(route.primary_index)
            if page is None or not page.children():
                failures.append(f"{where} rendered nothing")

    assert visited >= 30, f"only {visited} routes were reachable"

    critical = [
        f"{title}: {body[:120]}"
        for severity, title, body in conftest.DIALOGS
        if severity == "critical"
    ]
    errors = [
        r.getMessage()[:160] for r in caplog.records
        if r.levelno >= logging.ERROR
    ]

    assert not failures, "pages failed to open: " + "; ".join(failures[:6])
    assert not critical, "pages reported errors: " + "; ".join(critical[:6])
    assert not errors, "pages logged errors: " + "; ".join(errors[:6])


def test_switching_sections_repeatedly_stays_stable(qt_app, editor, window) -> None:
    """Guard against state that only breaks on the second visit."""
    conftest.DIALOGS.clear()
    for _ in range(3):
        for index in range(len(window._shell._destinations)):
            window._shell.activate_destination(index)
            qt_app.processEvents()

    critical = [t for s, t, _ in conftest.DIALOGS if s == "critical"]
    assert not critical, f"revisiting sections reported errors: {critical[:4]}"
    assert len(editor._items) == 1662, "the loaded save changed while navigating"
