"""Every edit must land on the right bytes and survive a reload.

The editors write by fixed offset into a decrypted blob, so a mistake here
does not raise - it silently corrupts a character. Each test performs a real
edit, writes the save, reloads it from disk, and checks the value came back.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import conftest
from conftest import requires_fixture, settle

pytestmark = requires_fixture


def reload_items(path: Path):
    from crimson.save_editor import save_crypto
    from crimson.save_editor.item_scanner import scan_items_smart

    return scan_items_smart(save_crypto.load_save_file(str(path)).decompressed_blob)


def find_at(items, offset: int):
    return next((i for i in items if i.offset == offset), None)


def test_stack_edit_round_trips_through_disk(qt_app, editor, save_copy) -> None:
    from crimson.save_editor.item_scanner import apply_stack_edit

    item = next(i for i in editor._items if i.bag == "CampWarehouse" and i.stack_count >= 1)
    target = item.stack_count + 21
    apply_stack_edit(editor._save_data.decompressed_blob, item, target)
    editor._dirty = True
    editor._do_save(str(save_copy))
    qt_app.processEvents()

    written = find_at(reload_items(save_copy), item.offset)
    assert written is not None, "the edited item vanished from the save"
    assert written.stack_count == target, (
        f"edit did not reach disk; dialogs shown: {conftest.DIALOGS[-4:]}"
    )
    assert written.item_key == item.item_key, "the edit moved onto another field"


def test_editing_one_item_leaves_every_other_item_untouched(
    qt_app, editor, save_copy
) -> None:
    before = {i.offset: (i.item_key, i.stack_count) for i in editor._items}
    from crimson.save_editor.item_scanner import apply_stack_edit

    item = next(i for i in editor._items if i.bag == "Bank")
    apply_stack_edit(editor._save_data.decompressed_blob, item, item.stack_count + 4)
    editor._dirty = True
    editor._do_save(str(save_copy))
    qt_app.processEvents()

    after = reload_items(save_copy)
    assert len(after) == len(before), "the item count changed"
    changed = [
        i.offset for i in after
        if before.get(i.offset) != (i.item_key, i.stack_count)
    ]
    assert changed == [item.offset], (
        f"unexpected items changed: {changed[:5]}; "
        f"dialogs shown: {conftest.DIALOGS[-4:]}"
    )


def test_undo_restores_the_previous_bytes(qt_app, editor) -> None:
    from crimson.save_editor.item_scanner import apply_stack_edit

    blob = editor._save_data.decompressed_blob
    original = bytes(blob)
    item = next(i for i in editor._items if i.stack_count >= 1)

    old_bytes = apply_stack_edit(blob, item, item.stack_count + 9)
    from crimson.save_editor.models import UndoEntry

    editor._undo_stack.append(
        UndoEntry(description="test stack", patches=[(item.offset + 18, old_bytes,
                  bytes(blob[item.offset + 18:item.offset + 26]))])
    )
    assert bytes(blob) != original

    editor._undo()
    qt_app.processEvents()
    assert bytes(editor._save_data.decompressed_blob) == original, (
        "undo did not put the original bytes back"
    )


def test_saving_without_changes_preserves_the_file(qt_app, editor, save_copy) -> None:
    digest = hashlib.sha256(save_copy.read_bytes()).hexdigest()
    editor._do_save(str(save_copy))
    qt_app.processEvents()
    reloaded = reload_items(save_copy)
    assert len(reloaded) == len(editor._items), (
        "a no-op save changed how many items the file holds"
    )


def test_written_save_still_matches_its_schema(qt_app, editor, save_copy) -> None:
    from crimson.save_editor import save_crypto
    from crimson.save_editor.item_scanner import apply_stack_edit

    item = next(i for i in editor._items if i.stack_count >= 1)
    apply_stack_edit(editor._save_data.decompressed_blob, item, item.stack_count + 2)
    editor._dirty = True
    editor._do_save(str(save_copy))
    qt_app.processEvents()

    written = save_crypto.load_save_file(str(save_copy))
    assert written.is_schema_supported, (
        "the written save no longer matches a known schema; the game may reject it"
    )
    assert written.compatibility_profile_id == "community-2026-07-patch"


def test_reload_after_write_keeps_every_bag_populated(qt_app, editor, save_copy) -> None:
    from collections import Counter
    from crimson.save_editor.item_scanner import apply_stack_edit

    before = Counter(i.bag for i in editor._items if i.bag)
    item = next(i for i in editor._items if i.bag == "Kuku")
    apply_stack_edit(editor._save_data.decompressed_blob, item, item.stack_count + 1)
    editor._dirty = True
    editor._do_save(str(save_copy))
    qt_app.processEvents()

    after = Counter(i.bag for i in reload_items(save_copy) if i.bag)
    assert after == before, f"bag contents shifted after a write: {after - before}"
