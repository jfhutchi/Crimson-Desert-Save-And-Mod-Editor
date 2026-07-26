"""MOUNTS section: the Blackstar Unlock write gate.

Blackstar Unlock rewrites mount records inside the save, so its safety
contract matters more than its feature set: Apply must be impossible without
a dry-run preview of this exact save, and refusing must leave every byte
untouched.
"""
from __future__ import annotations

import hashlib

import conftest
from conftest import requires_fixture

pytestmark = requires_fixture


def digest_blob(editor) -> str:
    return hashlib.sha256(bytes(editor._save_data.decompressed_blob)).hexdigest()


def test_apply_without_preview_is_refused_and_writes_nothing(
    qt_app, editor, save_copy
) -> None:
    before_blob = digest_blob(editor)
    before_file = hashlib.sha256(save_copy.read_bytes()).hexdigest()
    assert editor._blackstar_preview_token is None, (
        "a fresh load must not carry a preview token"
    )

    editor._blackstar_dry_run.setChecked(False)
    conftest.DIALOGS.clear()
    editor._start_blackstar_unlock()
    qt_app.processEvents()

    assert any(
        title == "Blackstar Preview Required" for _s, title, _b in conftest.DIALOGS
    ), f"apply without preview was not refused: {conftest.DIALOGS}"
    assert editor._blackstar_thread is None, "a worker started despite the refusal"
    assert digest_blob(editor) == before_blob
    assert hashlib.sha256(save_copy.read_bytes()).hexdigest() == before_file


def test_loading_a_save_clears_any_stale_preview_token(qt_app, editor) -> None:
    # A token minted for one save must never authorize writing another.
    editor._blackstar_preview_token = "stale-token-from-another-save"
    editor._load_save(str(editor._loaded_path))
    qt_app.processEvents()
    assert editor._blackstar_preview_token is None
