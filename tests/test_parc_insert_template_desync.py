"""Characterization of the PARC-insert template desync (2026-08-02).

add_item_to_inventory builds the new record from the item-template DB.
On the community-2026-07-patch schema the template's field layout no
longer matches how the parser (and presumably the game) decodes that
mask: the written _itemKey at rel 36 reads back as garbage, the fresh
scan cannot see the inserted item, and ~44 Mercenary rows drop out of
the scan.

The GUI now verifies-and-rolls-back (see _add_new_item), so users fall
back to the donor-swap path. This test pins the underlying inserter
bug: when the template DB is regenerated for the current patch, the
xfail flips to xpass and the strict marker fails the suite - delete
this file and the GUI fallback comment then.
"""
from __future__ import annotations

import pytest

import conftest
from conftest import requires_fixture

pytestmark = requires_fixture

NARIMA_KEY = 1000578


@pytest.mark.xfail(
    strict=True,
    reason="item-template DB predates the 2026-07 patch schema; "
    "inserted records misdecode (see docs/ITEMINFO_PARSER_HANDOFF.md)",
)
def test_parc_inserted_item_survives_a_rescan():
    from crimson.save_editor import save_crypto
    from crimson.save_editor.item_scanner import scan_items_smart
    from crimson.save_editor.parc_inserter2 import add_item_to_inventory

    sd = save_crypto.load_save_file(str(conftest.SAVE_FIXTURE))
    raw = bytes(sd.decompressed_blob)
    before = scan_items_smart(bytearray(raw))
    max_no = max((it.item_no for it in before), default=100)

    new_blob = add_item_to_inventory(
        raw, new_item_key=NARIMA_KEY, new_item_no=max_no + 100, new_stack=5
    )
    after = scan_items_smart(bytearray(new_blob))

    assert any(
        it.item_key == NARIMA_KEY and it.item_no == max_no + 100 for it in after
    ), "inserted item is invisible to the scanner"
    merc_before = sum(1 for i in before if i.source == "Mercenary")
    merc_after = sum(1 for i in after if i.source == "Mercenary")
    assert merc_after == merc_before, (
        f"insert cost the scan {merc_before - merc_after} Mercenary rows"
    )
