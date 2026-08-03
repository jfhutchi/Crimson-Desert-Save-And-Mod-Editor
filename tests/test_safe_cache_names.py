"""Remote pack/set filenames must never escape the cache directory.

The community index is fetched from GitHub and its ``filename`` field is
attacker-controlled; it used to be joined straight onto the cache dir.
"""
from __future__ import annotations

import os

import pytest

from crimson.common.safe_names import safe_cache_filename, safe_cache_path

HOSTILE = [
    "../../config_save_editor.json",
    "..\\..\\..\\Windows\\System32\\evil.json",
    "/etc/passwd",
    "C:\\Windows\\System32\\drivers\\etc\\hosts",
    "....//....//escape.json",
    "pack:stream.json",
    "",
    "...",
    ".hidden",
]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_filename_stays_inside_cache_dir(tmp_path, hostile):
    cache = tmp_path / "packs"
    cache.mkdir()
    base = os.path.abspath(str(cache))

    path = os.path.abspath(safe_cache_path(str(cache), hostile))

    assert os.path.commonpath([base, path]) == base, f"{hostile!r} escaped to {path}"
    assert os.path.dirname(path) == base
    assert os.path.basename(path).endswith(".json")


def test_ordinary_name_is_preserved():
    assert safe_cache_filename("starter_pack.json") == "starter_pack.json"
    assert safe_cache_filename("starter_pack") == "starter_pack.json"


def test_separators_never_survive():
    for hostile in HOSTILE:
        name = safe_cache_filename(hostile)
        assert "/" not in name and "\\" not in name and ".." not in name
