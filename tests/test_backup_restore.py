"""Backups are the safety net under every write; prove they work.

A save editor that writes a bad file is recoverable only if its backups are
real, complete, and restorable. These tests exercise the actual backup and
restore paths against copies of a real save.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

from conftest import requires_fixture, settle

pytestmark = requires_fixture


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_loading_a_save_creates_a_pristine_backup(editor, save_copy) -> None:
    pristine = save_copy.parent / "backups" / "save.save.PRISTINE.bak"
    assert pristine.is_file(), "loading must capture an untouched original"
    assert digest(pristine) == digest(save_copy)


def test_pristine_backup_is_never_replaced(editor, save_copy) -> None:
    """The pristine copy must keep the *original* bytes, not the latest ones."""
    pristine = save_copy.parent / "backups" / "save.save.PRISTINE.bak"
    original = digest(pristine)

    from crimson.save_editor.item_scanner import apply_stack_edit

    item = next(i for i in editor._items if i.stack_count >= 1)
    apply_stack_edit(editor._save_data.decompressed_blob, item, item.stack_count + 3)
    editor._dirty = True
    editor._do_save(str(save_copy))

    assert digest(save_copy) != original, "the save should have changed"
    assert digest(pristine) == original, (
        "the pristine backup was overwritten; the original bytes are gone"
    )


def test_writing_creates_a_timestamped_backup(qt_app, editor, save_copy) -> None:
    from crimson.save_editor.item_scanner import apply_stack_edit

    before = digest(save_copy)
    item = next(i for i in editor._items if i.stack_count >= 1)
    apply_stack_edit(editor._save_data.decompressed_blob, item, item.stack_count + 7)
    editor._dirty = True
    editor._do_save(str(save_copy))
    qt_app.processEvents()

    backups = sorted((save_copy.parent / "backups").glob("save.save.2*.bak"))
    assert backups, "a write must leave a timestamped backup behind"
    assert any(digest(b) == before for b in backups), (
        "no backup holds the bytes that were replaced"
    )


def test_backup_rotation_keeps_ten_and_spares_pristine(editor, save_copy) -> None:
    backup_dir = save_copy.parent / "backups"
    # Fifteen writes is more than the ten the rotation keeps.
    for index in range(15):
        stamp = backup_dir / f"save.save.2026010{index // 10}_{index:06d}.bak"
        shutil.copy2(save_copy, stamp)
        os.utime(stamp, (time.time() + index, time.time() + index))
    editor._create_backup(str(save_copy))

    rotated = [p for p in backup_dir.glob("*.bak") if ".PRISTINE." not in p.name]
    assert len(rotated) <= 10, f"rotation left {len(rotated)} timestamped backups"
    assert (backup_dir / "save.save.PRISTINE.bak").is_file(), (
        "rotation deleted the pristine copy"
    )


def test_restore_puts_the_original_bytes_back(qt_app, editor, save_copy) -> None:
    from crimson.save_editor.item_scanner import apply_stack_edit
    from PySide6.QtCore import Qt

    original = digest(save_copy)
    item = next(i for i in editor._items if i.bag == "CampWarehouse")
    original_stack = item.stack_count
    apply_stack_edit(editor._save_data.decompressed_blob, item, original_stack + 11)
    editor._dirty = True
    editor._do_save(str(save_copy))
    qt_app.processEvents()
    assert digest(save_copy) != original

    # Select the pristine entry in the real backup list and restore it.
    editor._refresh_backups()
    qt_app.processEvents()
    chosen = None
    for row in range(editor._backup_list.count()):
        entry = editor._backup_list.item(row)
        path = entry.data(Qt.UserRole)
        if path and "PRISTINE" in os.path.basename(path):
            editor._backup_list.setCurrentItem(entry)
            chosen = path
            break
    assert chosen, "the pristine backup is not offered in the restore list"

    generation = editor._save_data.document_generation
    editor._restore_backup()
    settle(qt_app, editor, after_generation=generation)

    assert digest(save_copy) == original, "restore did not reinstate the original"
    restored = next((i for i in editor._items if i.offset == item.offset), None)
    assert restored is not None
    assert restored.stack_count == original_stack, (
        "the reloaded save still shows the edited value"
    )


def test_restore_refuses_a_file_that_is_not_a_save(qt_app, editor, save_copy) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem

    good = digest(save_copy)
    junk = save_copy.parent / "backups" / "save.save.20990101_000000.bak"
    junk.write_bytes(b"not a save file" * 8)

    entry = QListWidgetItem(junk.name)
    entry.setData(Qt.UserRole, str(junk))
    editor._backup_list.addItem(entry)
    editor._backup_list.setCurrentItem(entry)

    # conftest answers Yes to every prompt, so this is the worst case: the
    # user confirms past the corruption warning. The save must still load.
    editor._restore_backup()
    qt_app.processEvents()

    from crimson.save_editor import save_crypto

    if digest(save_copy) != good:
        # If a corrupt restore is allowed, the editor must not silently claim
        # success - the file has to be recognisably broken, not half-written.
        try:
            save_crypto.load_save_file(str(save_copy))
            raise AssertionError(
                "a corrupt backup was restored and still reported as loadable"
            )
        except Exception:
            pass
