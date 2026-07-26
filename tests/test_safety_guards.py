"""The guards that stop a bad write reaching a character.

These are the checks that matter most: an unknown save layout must be
read-only, a write must refuse to run without a verified backup, and a
Blackstar preview must not touch bytes.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import requires_fixture

pytestmark = requires_fixture


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_an_unknown_layout_is_refused_rather_than_guessed() -> None:
    from crimson.save_editor.save_compat import (
        UnknownSaveSchemaError, compute_schema_identity, load_profiles,
        require_supported_identity,
    )
    from crimson.save_editor import save_crypto
    from conftest import SAVE_FIXTURE

    save = save_crypto.load_save_file(str(SAVE_FIXTURE))
    identity = compute_schema_identity(bytes(save.decompressed_blob), save.raw_header)
    unknown = replace(identity, schema_sha256="0" * 64)
    with pytest.raises(UnknownSaveSchemaError):
        require_supported_identity(unknown, load_profiles())


def test_a_save_with_an_unknown_schema_loads_read_only(qt_app, editor, save_copy) -> None:
    before = digest(save_copy)
    editor._save_data.is_schema_supported = False
    editor._dirty = True
    editor._do_save(str(save_copy))
    qt_app.processEvents()
    assert digest(save_copy) == before, (
        "a save whose layout is not recognised was written anyway"
    )


def test_writing_refuses_without_a_verified_backup(save_copy) -> None:
    from crimson.save_editor.save_crypto import write_save_file

    with pytest.raises(FileExistsError):
        write_save_file(str(save_copy), b"\x00" * 64, b"\x00" * 64)


def test_blackstar_preview_changes_nothing(qt_app, editor, save_copy) -> None:
    from crimson.save_editor.blackstar_unlock import _classify
    from crimson.save_editor.parc_inserter3 import build_insert_context

    before_file = digest(save_copy)
    before_blob = bytes(editor._save_data.decompressed_blob)

    context = build_insert_context(bytearray(editor._save_data.decompressed_blob))
    status, _found = _classify(context)

    assert status in {"absent", "legitimate_idle", "legitimate_active", "legacy"}
    assert bytes(editor._save_data.decompressed_blob) == before_blob, (
        "classifying Blackstar mutated the loaded save"
    )
    assert digest(save_copy) == before_file, "classifying Blackstar touched the file"


def test_the_editor_never_writes_the_file_it_did_not_load(qt_app, editor, tmp_path) -> None:
    """Guard against a stray path landing a write on the wrong save."""
    other = tmp_path / "someone_elses.save"
    other.write_bytes(b"untouched")
    before = digest(other)

    editor._dirty = True
    editor._do_save(str(editor._loaded_path))
    qt_app.processEvents()

    assert digest(other) == before, "an unrelated file was modified by a save"


def test_a_write_is_atomic_enough_to_leave_a_loadable_file(
    qt_app, editor, save_copy
) -> None:
    from crimson.save_editor import save_crypto
    from crimson.save_editor.item_scanner import apply_stack_edit

    item = next(i for i in editor._items if i.stack_count >= 1)
    apply_stack_edit(editor._save_data.decompressed_blob, item, item.stack_count + 6)
    editor._dirty = True
    editor._do_save(str(save_copy))
    qt_app.processEvents()

    # No half-written file: the result decrypts, decompresses and parses.
    reloaded = save_crypto.load_save_file(str(save_copy))
    assert len(reloaded.decompressed_blob) == len(editor._save_data.decompressed_blob)
    assert reloaded.is_schema_supported
