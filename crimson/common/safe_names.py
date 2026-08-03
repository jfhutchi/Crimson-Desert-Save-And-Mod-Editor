"""Filename sanitising for content fetched from remote indexes.

Community pack / equipment-set indexes are downloaded from GitHub and the
``filename`` field is attacker-controlled. Joining it straight onto the
cache directory lets an entry like ``../../config_save_editor.json`` (or an
absolute path, or a Windows ADS suffix) write wherever it likes.
"""
from __future__ import annotations

import os
import re
import time

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def safe_cache_filename(filename: str, *, suffix: str = ".json") -> str:
    """Reduce ``filename`` to a bare, safe basename ending in ``suffix``.

    Strips any directory component (both separators, on every platform),
    rejects ``..``, drops anything that is not alphanumeric/dot/dash/
    underscore, and always returns a non-empty name.
    """
    name = str(filename or "").strip()
    # Take the last path component under either separator, and defeat
    # NTFS alternate-data-stream / drive-letter tricks.
    name = name.replace("\\", "/").split("/")[-1]
    name = name.split(":")[-1]
    name = _SAFE_CHARS.sub("_", name)
    name = name.lstrip(".") or ""

    if suffix and not name.lower().endswith(suffix):
        name += suffix
    if name in ("", suffix):
        name = f"pack_{int(time.time())}{suffix}"
    return name


def safe_cache_path(directory: str, filename: str, *, suffix: str = ".json") -> str:
    """Join ``filename`` into ``directory``, guaranteed to stay inside it."""
    safe = safe_cache_filename(filename, suffix=suffix)
    base = os.path.abspath(directory)
    path = os.path.abspath(os.path.join(base, safe))
    if os.path.commonpath([base, path]) != base:
        raise ValueError(f"refusing to write outside the cache dir: {filename!r}")
    return path
