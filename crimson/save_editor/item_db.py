from __future__ import annotations

import json
import os
from typing import Dict, List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

from crimson.save_editor.models import ItemInfo


GITHUB_URL = (
    "https://raw.githubusercontent.com/"
    "NattKh/CrimsonDesertCommunityItemMapping/main/item_names.json"
)

import sys as _sys
_exe_dir = os.path.dirname(os.path.abspath(_sys.executable)) if getattr(_sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
_bundle_dir = getattr(_sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

SEARCH_PATHS = [
    os.path.join(_exe_dir, "item_names.json"),
    os.path.join(_bundle_dir, "item_names.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "item_names.json"),
    r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert\bin64\CrimsonMods\item_names.json",
]


class ItemNameDB:

    def __init__(self) -> None:
        self.items: Dict[int, ItemInfo] = {}
        self.loaded_path: str = ""
        self.version: int = 0
        self.load_auto()

    def load_auto(self) -> str:
        for path in SEARCH_PATHS:
            if os.path.isfile(path):
                self.load(path)
                if self.items:
                    return path

        save_path = os.path.join(_exe_dir, "item_names.json")
        try:
            ok, msg = self.sync_from_github(save_path)
            if ok and self.items:
                return save_path
        except Exception:
            pass
        return ""

    def load(self, path: str) -> None:
        self.items.clear()
        self.loaded_path = path
        self.version = 0

        if not os.path.isfile(path):
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        self.version = data.get("version", 0)
        for entry in data.get("items", []):
            key = entry.get("itemKey", 0)
            if key <= 0:
                continue
            self.items[key] = ItemInfo(
                item_key=key,
                name=entry.get("name", ""),
                internal_name=entry.get("internalName", ""),
                category=entry.get("category", "Misc"),
                max_stack=entry.get("maxStack", 0),
            )

    def apply_localization(self) -> int:
        try:
            from crimson.save_editor.localization import get_language, _names_data
            if get_language() == 'en' or not _names_data:
                return 0
            items_map = _names_data.get('items', {})
            if not items_map:
                return 0
            count = 0
            for key, info in self.items.items():
                localized = items_map.get(str(key), '')
                if localized:
                    info.name = localized
                    count += 1
            return count
        except Exception:
            return 0

    def save(self, path: str | None = None) -> None:
        path = path or self.loaded_path
        if not path:
            return

        items_list = []
        for key in sorted(self.items.keys()):
            info = self.items[key]
            entry: dict = {"itemKey": key, "name": info.name}
            if info.internal_name:
                entry["internalName"] = info.internal_name
            entry["category"] = info.category
            if info.max_stack:
                entry["maxStack"] = info.max_stack
            items_list.append(entry)

        data = {"version": self.version, "items": items_list}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_name(self, key: int) -> str:
        info = self.items.get(key)
        if info and info.name:
            return info.name
        return f"Unknown ({key})"

    def get_category(self, key: int) -> str:
        info = self.items.get(key)
        return info.category if info else "Misc"

    def rename_item(self, key: int, new_name: str) -> None:
        if key in self.items:
            self.items[key].name = new_name
        else:
            self.items[key] = ItemInfo(
                item_key=key,
                name=new_name,
                category="Misc",
            )

    def get_all_sorted(self) -> List[ItemInfo]:
        return [self.items[k] for k in sorted(self.items.keys())]

    def get_internal_name(self, key: int) -> str:
        info = self.items.get(key)
        return info.internal_name if info else ""

    def search(self, query: str) -> List[ItemInfo]:
        query_lower = query.lower().strip()
        if not query_lower:
            return self.get_all_sorted()

        results = []
        for info in self.items.values():
            if (query_lower in info.name.lower()
                    or query_lower in info.internal_name.lower()
                    or query_lower in str(info.item_key)):
                results.append(info)
        results.sort(key=lambda x: x.item_key)
        return results

    def sync_from_github(self, save_to: str = "") -> tuple[bool, str]:
        try:
            req = Request(GITHUB_URL, headers={"User-Agent": "CrimsonSaveEditor/1.0"})
            with urlopen(req, timeout=10) as resp:
                raw = resp.read()
                data = json.loads(raw.decode("utf-8"))
        except (URLError, json.JSONDecodeError, OSError) as e:
            return False, f"Download failed: {e}"

        remote_version = data.get("version", 0)
        remote_items = data.get("items", [])

        if remote_version <= self.version and self.items:
            return True, f"Already up to date (v{self.version})."

        added = 0
        updated = 0
        for entry in remote_items:
            key = entry.get("itemKey", 0)
            if key <= 0:
                continue
            name = entry.get("name", "")
            category = entry.get("category", "Misc")
            internal = entry.get("internalName", "")
            max_stack = entry.get("maxStack", 0)

            if key not in self.items:
                self.items[key] = ItemInfo(
                    item_key=key,
                    name=name,
                    internal_name=internal,
                    category=category,
                    max_stack=max_stack,
                )
                added += 1
            else:
                existing = self.items[key]
                if name and (not existing.name or existing.name.startswith("Unknown")):
                    existing.name = name
                    updated += 1
                if internal and not existing.internal_name:
                    existing.internal_name = internal
                if category != "Misc" and existing.category == "Misc":
                    existing.category = category
                if max_stack and not existing.max_stack:
                    existing.max_stack = max_stack

        if remote_version > self.version:
            self.version = remote_version

        if not self.loaded_path and save_to:
            self.loaded_path = save_to
        if self.loaded_path:
            self.save()

        return True, f"Synced v{remote_version}: {added} new, {updated} updated."
