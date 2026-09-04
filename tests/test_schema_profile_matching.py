"""A profile must cover every save sharing the game's structural layout.

``schema_sha256`` and ``type_count`` are computed over the save's schema
section, which only lists the types that save actually uses - so both vary
with the player's progress, not with the game version. Comparing them made
a profile match exactly the one save it was generated from: every other
save, including ones from the same patch, was refused as "unknown schema"
and the editor disabled Ctrl+S with no visible explanation.
"""
from __future__ import annotations

import dataclasses

import pytest

import conftest
from conftest import requires_fixture

pytestmark = requires_fixture


@pytest.fixture(scope="module")
def fixture_identity():
    from crimson.save_editor import save_crypto
    from crimson.save_editor.save_compat import compute_schema_identity

    sd = save_crypto.load_save_file(str(conftest.SAVE_FIXTURE))
    return compute_schema_identity(bytes(sd.decompressed_blob), sd.raw_header)


def test_fixture_matches_an_enrolled_profile(fixture_identity):
    from crimson.save_editor.save_compat import load_profiles, match_profile

    assert match_profile(fixture_identity, load_profiles()) is not None


@pytest.mark.parametrize(
    "field, value",
    [
        ("schema_sha256", "0" * 64),
        ("type_count", 999),
    ],
)
def test_content_dependent_fields_do_not_break_matching(fixture_identity, field, value):
    """Two saves of the same patch differ in these; they must not gate writes."""
    from crimson.save_editor.save_compat import load_profiles, match_profile

    altered = dataclasses.replace(fixture_identity, **{field: value})
    assert match_profile(altered, load_profiles()) is not None, (
        f"changing {field} alone made the save unwritable"
    )


@pytest.mark.parametrize(
    "field, value",
    [
        ("container_version", 99),
        ("root_entry_count", 3),
        ("required_type_signatures", {"MercenaryClanSaveData": "deadbeef"}),
    ],
)
def test_structural_fields_still_gate(fixture_identity, field, value):
    """The genuinely structural fields must still refuse an unknown layout."""
    from crimson.save_editor.save_compat import load_profiles, match_profile

    altered = dataclasses.replace(fixture_identity, **{field: value})
    assert match_profile(altered, load_profiles()) is None, (
        f"{field} changed but the save was still accepted for writing"
    )


def test_mount_insertion_is_gated_separately(fixture_identity):
    """Ordinary writes must not depend on owning a particular mount."""
    from crimson.save_editor.save_compat import (
        load_profiles,
        match_profile,
        mount_insertion_supported,
    )

    profiles = load_profiles()
    encodings = dict(fixture_identity.observed_encodings)
    encodings["MercenaryClanSaveData._mercenaryDataList.element_mask"] = "0501002b0800"
    altered = dataclasses.replace(fixture_identity, observed_encodings=encodings)

    assert match_profile(altered, profiles) is not None, "writes blocked by mount content"
    assert mount_insertion_supported(altered, profiles) is False
    assert mount_insertion_supported(fixture_identity, profiles) is True
