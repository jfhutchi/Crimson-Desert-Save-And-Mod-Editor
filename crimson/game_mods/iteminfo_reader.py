from __future__ import annotations

import logging
import struct
from typing import Dict, Optional

log = logging.getLogger(__name__)


class ItemInfoCache:

    def __init__(self) -> None:
        self._game_path: str = ""
        self._data: Dict[int, dict] = {}
        self._loaded: bool = False
        self._load_attempted: bool = False


    def set_game_path(self, path: str) -> None:
        if path != self._game_path:
            self._game_path = path
            self._invalidate()

    def update_from_lookup(self, rust_lookup: Dict[int, dict]) -> None:
        if rust_lookup is self._data:
            return
        self._data = rust_lookup
        self._loaded = True
        log.debug("ItemInfoCache: updated from lookup (%d items)", len(self._data))

    def _invalidate(self) -> None:
        self._data = {}
        self._loaded = False
        self._load_attempted = False

    def _try_load(self) -> None:
        if self._loaded or self._load_attempted:
            return
        if not self._game_path:
            return
        self._load_attempted = True
        raw: Optional[bytes] = None
        try:
            from crimson.game_mods import crimson_rs as crimson_rs
            from crimson.game_mods.paz_patcher import ItemBuffPatcher
            patcher = ItemBuffPatcher(self._game_path)
            raw = bytes(patcher.extract_iteminfo())
            items = crimson_rs.parse_iteminfo_from_bytes(raw)
            self._data = {it['key']: it for it in items}
            self._loaded = True
            log.debug("ItemInfoCache: loaded %d items via crimson_rs", len(self._data))
        except ImportError:
            log.debug("ItemInfoCache: crimson_rs unavailable, using Python parser")
            self._try_load_python(raw)
        except Exception as e:
            log.debug("ItemInfoCache: crimson_rs failed (%s), using Python parser", e)
            self._try_load_python(raw)

    def _try_load_python(self, raw: Optional[bytes] = None) -> None:
        try:
            from crimson.game_mods import iteminfo_parser as _ip
            if raw is None:
                from crimson.game_mods.paz_patcher import ItemBuffPatcher
                patcher = ItemBuffPatcher(self._game_path)
                raw = bytes(patcher.extract_iteminfo())
            all_items = _ip.find_all_items(raw)
            data: Dict[int, dict] = {}
            for i, (off, key, _name) in enumerate(all_items):
                nxt = all_items[i + 1][0] if i + 1 < len(all_items) else len(raw)
                try:
                    rec = _ip.parse_item(raw, off, nxt)
                    if rec and len(rec.tail_raw) >= 6:
                        max_end = struct.unpack_from('<H', rec.tail_raw, len(rec.tail_raw) - 6)[0]
                        data[key] = {'key': key, 'max_endurance': max_end}
                except Exception:
                    pass
            self._data = data
            self._loaded = True
            log.debug("ItemInfoCache: loaded %d items via Python parser", len(data))
        except Exception as e:
            log.debug("ItemInfoCache: Python parser failed (%s)", e)


    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def get_item(self, item_key: int) -> dict:
        self._try_load()
        return self._data.get(item_key, {})

    def get_max_endurance(self, item_key: int) -> Optional[int]:
        v = self.get_item(item_key).get('max_endurance')
        return int(v) if v is not None else None

    def is_durability_gem(self, item_key: int) -> bool:
        v = self.get_max_endurance(item_key)
        return v is not None and 0 < v < 65535
