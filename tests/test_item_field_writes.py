"""Every item-field editor must write its own field and nothing else.

apply_endurance_edit and apply_sharpness_edit used hardcoded relative
offsets that were one field too high on the current schema: endurance
wrote _sharpness, and sharpness wrote _maxSocketCount/_validSocketCount
(1-byte socket counts the game can crash on). Both now resolve the
offset from the parsed record.

The whole-blob byte diff is the point of these tests - asserting the
value round-trips is not enough, because writing the neighbouring field
can still read back fine through a matching wrong-offset reader.
"""
from __future__ import annotations

import pytest

import conftest
from conftest import requires_fixture

pytestmark = requires_fixture


@pytest.fixture(scope="module")
def blob_and_parse():
    from crimson.save_editor import save_crypto
    from crimson.save_editor.native_parse import parse_any

    sd = save_crypto.load_save_file(str(conftest.SAVE_FIXTURE))
    raw = bytes(sd.decompressed_blob)
    return raw, parse_any(raw, {"input_kind": "raw_blob"})


@pytest.mark.parametrize(
    "field, value",
    [("_endurance", 1234), ("_sharpness", 500)],
)
def test_edit_touches_only_its_own_field(blob_and_parse, field, value):
    from crimson.save_editor.item_scanner import (
        apply_endurance_edit,
        apply_sharpness_edit,
        scan_items_smart,
    )

    raw, parse_result = blob_and_parse
    apply = {"_endurance": apply_endurance_edit, "_sharpness": apply_sharpness_edit}[field]

    item = next(
        i
        for i in scan_items_smart(bytearray(raw), parse_result)
        if i.field_offsets and field in i.field_offsets
    )
    expected = item.field_offsets[field]

    buf = bytearray(raw)
    apply(buf, item, value)

    changed = [i for i in range(len(raw)) if raw[i] != buf[i]]
    assert changed == [expected, expected + 1], (
        f"{field} edit changed {[c - item.offset for c in changed]} "
        f"(relative), expected {[expected - item.offset, expected - item.offset + 1]}"
    )
    assert getattr(item, field.lstrip("_")) == value
