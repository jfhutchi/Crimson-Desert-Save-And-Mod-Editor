from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import zipfile
from typing import Callable, Dict, Optional
from urllib.request import urlopen, Request

from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import QObject, QSize, Qt, Signal

log = logging.getLogger(__name__)

ICON_SIZE = 32
_MAX_CACHED_EDGE = 48
_BUNDLE_NAME = "icons_bundle.zip"

_GITHUB_ICON_BASE = "https://raw.githubusercontent.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR/main/icons_local"


def _module_base() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _dir_has_icons(path: str) -> bool:
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.endswith('.webp'):
                    return True
    except OSError:
        pass
    return False


def _user_icons_dir() -> str:
    """Per-user icon store, so a reinstall never re-downloads 70MB."""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "CrimsonDesertEditor", "icons_local")


def _legacy_icon_dirs() -> list[str]:
    """Places earlier builds kept icons, newest layout first."""
    base = _module_base()
    return [
        os.path.join(base, "icons_local"),
        os.path.join(os.path.dirname(base), "icons_local"),
    ]


def _get_local_icons_dir():
    # Prefer the per-user store; fall back to an older next-to-the-exe set so
    # an existing download keeps working until it is migrated.
    preferred = _user_icons_dir()
    if _dir_has_icons(preferred):
        return preferred
    for legacy in _legacy_icon_dirs():
        if _dir_has_icons(legacy):
            return legacy
    return preferred


def _bundled_icons_zip() -> Optional[str]:
    base = getattr(sys, '_MEIPASS', _module_base())
    path = os.path.join(base, _BUNDLE_NAME)
    return path if os.path.isfile(path) else None


class IconCache(QObject):
    """Item icon store: local .webp files first, GitHub as a last resort.

    Every callback crosses back to the thread that created the cache (the GUI
    thread) through a queued signal, so worker threads never touch widgets.
    """

    _deliver = Signal(int, object, object)
    _seeded = Signal(int, object)
    _warm_batch = Signal(object)
    _warm_done = Signal(int, object)
    _bulk_progress = Signal(object, object)
    _bulk_done = Signal(object, object)

    def __init__(self, icon_urls_path: Optional[str] = None, local_dir: Optional[str] = None):
        super().__init__()
        self._pixmaps: Dict[object, QPixmap] = {}
        self._pending: set = set()
        # Keys whose download already failed; without this every table
        # repopulate re-spawns a thread and an HTTP attempt per missing icon.
        self._missing: set = set()
        self._lock = threading.Lock()
        self._warming = False
        self._bulk_running = False
        self._local_dir = local_dir or _get_local_icons_dir()
        os.makedirs(self._local_dir, exist_ok=True)
        self._deliver.connect(self._on_deliver)
        self._seeded.connect(self._on_seeded)
        self._warm_batch.connect(self._on_warm_batch)
        self._warm_done.connect(self._on_warm_done)
        self._bulk_progress.connect(self._on_bulk_progress)
        self._bulk_done.connect(self._on_bulk_done)

    def _on_deliver(self, item_key: int, px: object, callback) -> None:
        callback(item_key, px)

    def _on_seeded(self, count: int, callback) -> None:
        if callback is not None:
            callback(count)

    def _on_warm_batch(self, batch) -> None:
        for key, image in batch:
            if key not in self._pixmaps:
                self._pixmaps[key] = QPixmap.fromImage(image)

    def _on_warm_done(self, count: int, callback) -> None:
        self._warming = False
        queued = getattr(self, "_rewarm", None)
        if queued is not None:
            self._rewarm = None
            keys, queued_completed = queued
            self.warm_cache_async(keys, completed=queued_completed)
        if callback is not None:
            callback(count)

    def _on_bulk_progress(self, args, callback) -> None:
        callback(*args)

    def _on_bulk_done(self, stats, callback) -> None:
        self._bulk_running = False
        if callback is not None:
            callback(stats)

    def peek(self, item_key: int) -> Optional[QPixmap]:
        """Return the cached pixmap without any disk access."""
        return self._pixmaps.get(item_key)

    def warm_cache_async(
        self,
        keys,
        completed: Optional[Callable[[int], None]] = None,
        batch_size: int = 256,
    ) -> bool:
        """Decode local icons on a worker thread and cache them as pixmaps.

        QImage decoding is thread-safe; pixmap conversion happens on the GUI
        thread in small batches so the event loop never blocks. Returns True
        when a warm thread was started.
        """
        if self._warming:
            # A warm during the bulk download walks the keys while files are
            # still arriving and misses them; dropping this request would
            # leave those icons blank until restart. Run it when done.
            self._rewarm = (list(keys), completed)
            return False
        wanted = [k for k in keys if k not in self._pixmaps]
        if not wanted:
            if completed is not None:
                completed(0)
            return False
        self._warming = True
        local_dir = self._local_dir

        def _worker() -> None:
            batch = []
            loaded = 0
            for key in wanted:
                path = os.path.join(local_dir, f"{key}.webp")
                if not os.path.isfile(path):
                    continue
                image = QImage(path)
                if image.isNull():
                    continue
                if image.width() > _MAX_CACHED_EDGE or image.height() > _MAX_CACHED_EDGE:
                    image = image.scaled(
                        QSize(ICON_SIZE, ICON_SIZE),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                batch.append((key, image))
                loaded += 1
                if len(batch) >= batch_size:
                    self._warm_batch.emit(batch)
                    batch = []
            if batch:
                self._warm_batch.emit(batch)
            self._warm_done.emit(loaded, completed)

        threading.Thread(target=_worker, daemon=True).start()
        return True

    @staticmethod
    def _bound(px: QPixmap) -> QPixmap:
        if px.width() > _MAX_CACHED_EDGE or px.height() > _MAX_CACHED_EDGE:
            return px.scaled(
                QSize(ICON_SIZE, ICON_SIZE),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        return px

    def _load_local(self, item_key: int) -> Optional[QPixmap]:
        local_path = os.path.join(self._local_dir, f"{item_key}.webp")
        if os.path.isfile(local_path):
            px = QPixmap(local_path)
            if not px.isNull():
                px = self._bound(px)
                self._pixmaps[item_key] = px
                return px
        return None

    def has_icon(self, item_key: int) -> bool:
        if item_key in self._pixmaps:
            return True
        return os.path.isfile(os.path.join(self._local_dir, f"{item_key}.webp"))

    def get_pixmap(self, item_key: int) -> Optional[QPixmap]:
        if item_key in self._pixmaps:
            return self._pixmaps[item_key]
        return self._load_local(item_key)

    def request_icon(self, item_key: int, callback: Callable[[int, QPixmap], None]) -> None:
        if item_key in self._pixmaps:
            callback(item_key, self._pixmaps[item_key])
            return

        px = self._load_local(item_key)
        if px is not None:
            callback(item_key, px)
            return

        with self._lock:
            if item_key in self._pending or item_key in self._missing:
                return
            self._pending.add(item_key)

        url = f"{_GITHUB_ICON_BASE}/{item_key}.webp"
        thread = threading.Thread(
            target=self._download_icon,
            args=(item_key, url, callback),
            daemon=True,
        )
        thread.start()

    def download_icon_sync(self, item_key: int) -> Optional[QPixmap]:
        if item_key in self._pixmaps:
            return self._pixmaps[item_key]

        px = self._load_local(item_key)
        if px is not None:
            return px

        local_path = os.path.join(self._local_dir, f"{item_key}.webp")
        url = f"{_GITHUB_ICON_BASE}/{item_key}.webp"
        try:
            req = Request(url, headers={"User-Agent": "CrimsonSaveEditor"})
            with urlopen(req, timeout=15) as resp:
                img_data = resp.read()

            with open(local_path, 'wb') as f:
                f.write(img_data)

            return self._load_local(item_key)
        except Exception as e:
            log.debug("Icon download failed for key %d: %s", item_key, e)

        return None

    def _download_icon(self, item_key: int, url: str, callback) -> None:
        local_path = os.path.join(self._local_dir, f"{item_key}.webp")

        try:
            if not os.path.isfile(local_path):
                req = Request(url, headers={"User-Agent": "CrimsonSaveEditor"})
                with urlopen(req, timeout=15) as resp:
                    img_data = resp.read()

                if not img_data or len(img_data) < 100:
                    with self._lock:
                        self._missing.add(item_key)
                    return

                with open(local_path, 'wb') as f:
                    f.write(img_data)

            qimg = QImage(local_path)
            if qimg.isNull():
                return

            px = self._bound(QPixmap.fromImage(qimg))
            self._pixmaps[item_key] = px
            self._deliver.emit(item_key, px, callback)

        except Exception as e:
            log.debug("Icon download failed for key %d: %s", item_key, e)
            with self._lock:
                self._missing.add(item_key)
        finally:
            with self._lock:
                self._pending.discard(item_key)

    def seed_from_bundle_sync(
        self,
        bundle_path: Optional[str] = None,
        min_files: int = 100,
    ) -> int:
        """Extract the bundled icon archive next to the executable.

        Returns the number of files written; 0 when there is no bundle or the
        local set already looks complete.
        """
        path = bundle_path or _bundled_icons_zip()
        if path is None:
            return 0
        try:
            existing = len([
                f for f in os.listdir(self._local_dir) if f.endswith('.webp')
            ])
        except OSError:
            existing = 0
        if existing >= min_files:
            return 0

        parent = os.path.dirname(self._local_dir)
        count = 0
        try:
            with zipfile.ZipFile(path) as zf:
                for member in zf.namelist():
                    if '..' in member or not member.endswith('.webp'):
                        continue
                    if not member.startswith(('icons_local/', 'icons_mercenary/')):
                        continue
                    target = os.path.join(parent, *member.split('/'))
                    if os.path.isfile(target):
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(member) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    count += 1
        except (OSError, zipfile.BadZipFile) as e:
            log.warning("Icon bundle extraction failed: %s", e)
        return count

    def migrate_legacy_icons(self) -> int:
        """Copy an older next-to-the-exe icon set into the per-user store.

        Builds replace their own directory, so icons kept beside the
        executable disappear on every reinstall. Anything already downloaded
        is adopted once instead of being fetched again.
        """
        target = _user_icons_dir()
        if self._local_dir != target:
            return 0
        have = len([f for f in os.listdir(target)]) if os.path.isdir(target) else 0
        best, best_count = None, have
        for legacy in _legacy_icon_dirs():
            if not os.path.isdir(legacy) or os.path.abspath(legacy) == os.path.abspath(target):
                continue
            count = len([f for f in os.listdir(legacy) if f.endswith(".webp")])
            if count > best_count:
                best, best_count = legacy, count
        if best is None:
            return 0
        os.makedirs(target, exist_ok=True)
        copied = 0
        for name in os.listdir(best):
            if not name.endswith(".webp"):
                continue
            destination = os.path.join(target, name)
            if os.path.isfile(destination):
                continue
            try:
                shutil.copy2(os.path.join(best, name), destination)
                copied += 1
            except OSError:
                continue
        if copied:
            log.info("Adopted %d icons from %s", copied, best)
        return copied

    def ensure_seeded(self, completed: Optional[Callable[[int], None]] = None) -> bool:
        """Seed local icons from the bundled archive on a background thread.

        Returns True when a seeding thread was started. The completion
        callback runs on the GUI thread with the number of files written.
        """
        if _bundled_icons_zip() is None:
            return False
        try:
            existing = len([
                f for f in os.listdir(self._local_dir) if f.endswith('.webp')
            ])
        except OSError:
            existing = 0
        if existing >= 100:
            return False

        def _worker() -> None:
            count = self.seed_from_bundle_sync()
            self._seeded.emit(count, completed)

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def preload_keys(self, keys: list, callback: Callable[[int, QPixmap], None]) -> None:
        for key in keys:
            if key not in self._pixmaps:
                self.request_icon(key, callback)

    def bulk_download_all(
        self,
        progress_callback=None,
        cancel_event: Optional[threading.Event] = None,
        max_workers: int = 12,
    ) -> dict:
        """Download the icon set with a worker pool.

        Sequential downloads take ~15 minutes for 6,000 files; a small pool
        brings that to about a minute. Safe to call from a worker thread;
        ``progress_callback`` fires on that same thread (use
        ``bulk_download_async`` for GUI-marshaled callbacks).
        """
        import json as _json
        import urllib.request
        from concurrent.futures import ThreadPoolExecutor

        stats = {'downloaded': 0, 'skipped': 0, 'errors': 0, 'cancelled': False}

        seeded = self.seed_from_bundle_sync(min_files=1_000_000)
        if seeded:
            stats['downloaded'] = seeded
            return stats

        folders = [
            ("icons_local", self._local_dir),
            ("icons_mercenary", os.path.join(os.path.dirname(self._local_dir), "icons_mercenary")),
        ]
        stats_lock = threading.Lock()

        def _report(folder_name: str, total: int) -> None:
            if progress_callback is None:
                return
            with stats_lock:
                snapshot = (stats['downloaded'], stats['skipped'], stats['errors'])
            progress_callback(folder_name, *snapshot, total)

        for folder_name, local_dir in folders:
            if cancel_event is not None and cancel_event.is_set():
                stats['cancelled'] = True
                break
            os.makedirs(local_dir, exist_ok=True)
            base_url = f"https://raw.githubusercontent.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR/main/{folder_name}"

            api_url = f"https://api.github.com/repos/NattKh/CRIMSON-DESERT-SAVE-EDITOR/contents/{folder_name}"
            try:
                req = urllib.request.Request(api_url, headers={"User-Agent": "CrimsonSaveEditor"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    files = _json.loads(resp.read())
            except Exception as e:
                log.warning("Failed to list %s from GitHub: %s", folder_name, e)
                with stats_lock:
                    stats['errors'] += 1
                continue

            wanted = [
                entry.get('name', '')
                for entry in files
                if entry.get('name', '').endswith('.webp')
            ]
            total = len(wanted)

            def _fetch(fname, _dir=local_dir, _base=base_url, _folder=folder_name, _total=total):
                if cancel_event is not None and cancel_event.is_set():
                    return
                local_path = os.path.join(_dir, fname)
                if os.path.isfile(local_path):
                    with stats_lock:
                        stats['skipped'] += 1
                else:
                    try:
                        req = urllib.request.Request(
                            f"{_base}/{fname}",
                            headers={"User-Agent": "CrimsonSaveEditor"},
                        )
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            data = resp.read()
                        if data and len(data) > 100:
                            with open(local_path, 'wb') as f:
                                f.write(data)
                            with stats_lock:
                                stats['downloaded'] += 1
                        else:
                            with stats_lock:
                                stats['errors'] += 1
                    except Exception:
                        with stats_lock:
                            stats['errors'] += 1
                with stats_lock:
                    done = stats['downloaded'] + stats['skipped'] + stats['errors']
                if done % 25 == 0 or done >= _total:
                    _report(_folder, _total)

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                list(pool.map(_fetch, wanted))

            if cancel_event is not None and cancel_event.is_set():
                stats['cancelled'] = True
                break

        return stats

    def bulk_download_async(
        self,
        progress=None,
        completed=None,
        cancel_event: Optional[threading.Event] = None,
    ) -> bool:
        """Run bulk_download_all on a thread, callbacks on the GUI thread."""
        if self._bulk_running:
            return False
        self._bulk_running = True

        def _worker() -> None:
            def _cb(*args) -> None:
                if progress is not None:
                    self._bulk_progress.emit(args, progress)

            stats = self.bulk_download_all(
                progress_callback=_cb, cancel_event=cancel_event
            )
            self._bulk_done.emit(stats, completed)

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def get_merc_pixmap(self, char_key: int) -> Optional[QPixmap]:
        cache_key = f"merc_{char_key}"
        if cache_key in self._pixmaps:
            return self._pixmaps[cache_key]

        merc_dir = os.path.join(os.path.dirname(self._local_dir), "icons_mercenary")
        local_path = os.path.join(merc_dir, f"{char_key}.webp")
        if os.path.isfile(local_path):
            px = QPixmap(local_path)
            if not px.isNull():
                px = self._bound(px)
                self._pixmaps[cache_key] = px
                return px
        return None

    # The full GitHub icon set is ~6,000 files. Anything comfortably above
    # this floor counts as "fully downloaded"; a partial/aborted download or
    # the bundled seed stays below it.
    # ponytail: hardcoded floor - bump if the upstream icon set ever shrinks/grows past it
    ICON_SET_MIN = 5500

    @property
    def coverage(self) -> int:
        """Number of cached icon files. A COUNT, not a percentage."""
        try:
            return len([f for f in os.listdir(self._local_dir) if f.endswith('.webp')])
        except Exception:
            return 0

    @property
    def is_complete(self) -> bool:
        return self.coverage >= self.ICON_SET_MIN
