"""Icons that download during a warm pass must still get warmed.

The bulk download and the warm pass start together, so the first warm walks
the key list while files are still arriving. The follow-up warm used to be
silently dropped because one was already running - leaving every icon that
downloaded after the walk blank on all tabs until restart.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from conftest import ROOT


def test_warm_requested_mid_warm_runs_afterwards(qt_app, tmp_path) -> None:
    from crimson.common.icon_cache import IconCache

    source = Path(__file__).resolve()
    store = tmp_path / "icons"
    store.mkdir()
    seed = next(iter(
        p for p in (Path(__import__("os").environ["LOCALAPPDATA"]) /
                    "CrimsonDesertEditor" / "icons_local").glob("*.webp")
    ), None)
    if seed is None:
        import pytest
        pytest.skip("no local icon available to copy")

    for key in (1, 2):
        shutil.copy2(seed, store / f"{key}.webp")

    cache = IconCache(local_dir=str(store))
    done: list[int] = []

    assert cache.warm_cache_async([1, 2, 3], completed=done.append)

    # Mid-warm: key 3 "finishes downloading" and a second warm is requested,
    # exactly what the bulk download's completion callback does.
    shutil.copy2(seed, store / "3.webp")
    assert cache.warm_cache_async([1, 2, 3], completed=done.append) is False

    deadline = time.time() + 30
    while time.time() < deadline and len(done) < 2:
        qt_app.processEvents()
        time.sleep(0.02)

    assert len(done) == 2, "the queued warm never ran"
    assert cache.peek(3) is not None, (
        "an icon that arrived during the first warm stayed cold"
    )
