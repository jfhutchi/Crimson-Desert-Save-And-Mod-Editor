"""Loading a real save must work inside the unified window.

The save editor runs hidden inside the shared window, so its pages are
reparented and its handlers fire from a different owner than before. This
drives the real load path end to end against a current-patch save.
"""
from __future__ import annotations

from conftest import requires_fixture

pytestmark = requires_fixture

EXPECTED_TABS = {
    "All": 1662, "Equipment": 19, "Inventory": 204, "Quest": 122,
    "Camp Warehouse": 253, "Warehouse": 23, "Bank": 50, "Kuku": 165,
    "Money": 15, "Vendor": 246, "Mercenary": 259,
}


def test_every_item_loads_in_the_unified_window(editor) -> None:
    assert len(editor._items) == 1662
    assert editor._inv_table.rowCount() == 1662


def test_every_bag_tab_reports_its_count(editor) -> None:
    labels = [
        editor._inv_subtabs.tabText(i) for i in range(editor._inv_subtabs.count())
    ]
    for name, count in EXPECTED_TABS.items():
        assert f"{name} ({count})" in labels, f"{name} should report {count}: {labels}"


def test_the_save_stays_writable(editor) -> None:
    assert editor._save_data.is_schema_supported
    assert editor._save_data.compatibility_profile_id == "community-2026-07-patch"
