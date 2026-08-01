"""Refutation probe: does the new `if not patches` guard block a same-key give?"""
from __future__ import annotations

import pytest

import conftest
from conftest import requires_fixture

pytestmark = requires_fixture


def blob_record(blob, offset):
    import struct
    return (
        struct.unpack_from("<I", blob, offset + 12)[0],
        struct.unpack_from("<q", blob, offset + 18)[0],
    )


def test_same_key_give_still_changes_the_stack(qt_app, editor) -> None:
    """Give 'more of what the donor already is' -- a top-up, not a swap."""
    from PySide6.QtWidgets import QDialog
    from crimson.save_editor import gui as gui_mod

    donor = next(
        i for i in editor._items
        if i.source == "Inventory" and i.parc_parsed and i.stack_count >= 1
    )
    offset = donor.offset
    want = donor.stack_count + 500

    class StubGiveItemDialog:
        target_key = donor.item_key          # SAME key: a top-up
        target_count = want
        donor_item = donor

        def __init__(self, *a, **k) -> None:
            pass

        def exec(self):
            return QDialog.Accepted

    keep = gui_mod.GiveItemDialog
    gui_mod.GiveItemDialog = StubGiveItemDialog
    try:
        before = len(conftest.DIALOGS)
        editor._give_item()
        qt_app.processEvents()
        shown = conftest.DIALOGS[before:]
    finally:
        gui_mod.GiveItemDialog = keep

    got = blob_record(editor._save_data.decompressed_blob, offset)
    print("\nDIALOGS SHOWN:", shown)
    print("BLOB AFTER:", got, "WANTED:", (donor.item_key, want))
    assert got == (StubGiveItemDialog.target_key, want), (
        f"same-key give did nothing; blob={got} wanted={(StubGiveItemDialog.target_key, want)}; "
        f"dialogs={shown}"
    )
