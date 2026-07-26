"""Shared modding state — cross-tool overlay coordination.

Both CrimsonGameMods and DMM read/write `crimson_modding_state.json` in the
game directory.  This file tracks which overlays are active, who owns them,
and what they contain.  It lets each tool display the other's status and
avoid stepping on each other's overlays.

The file is always next to the game's PAZ groups (e.g., D:/Games/CrimsonDesert/).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

STATE_FILENAME = "crimson_modding_state.json"
STATE_VERSION = 1


@dataclass
class OverlayEntry:
    owner: str                   # "CrimsonGameMods" | "DMM" | "JMM" | "Unknown"
    content: str                 # human-readable: "iteminfo buffs", "stacker merge", etc.
    updated: str = ""            # ISO timestamp
    files: list[str] = field(default_factory=list)   # e.g. ["iteminfo.pabgb", "equipslotinfo.pabgb"]
    paz_size: int = 0
    pamt_size: int = 0


@dataclass
class DmmModEntry:
    """A single DMM mod — read from DMM's config + mods folder."""
    file_name: str
    title: str = ""
    author: str = ""
    version: str = ""
    patch_count: int = 0
    game_files: list[str] = field(default_factory=list)
    enabled: bool = True
    targets_iteminfo: bool = False


@dataclass
class ModdingState:
    version: int = STATE_VERSION
    last_updated: str = ""
    cgm_version: str = ""
    dmm_version: str = ""
    game_path: str = ""
    overlays: dict[str, OverlayEntry] = field(default_factory=dict)
    dmm_mods: list[DmmModEntry] = field(default_factory=list)
    dmm_asi_mods: list[str] = field(default_factory=list)
    dmm_texture_mods: list[str] = field(default_factory=list)
    dmm_browser_mods: list[str] = field(default_factory=list)


def _state_path(game_path: str) -> str:
    return os.path.join(game_path, STATE_FILENAME)


def load_state(game_path: str) -> ModdingState:
    """Load shared state from the game directory. Returns empty state if missing."""
    path = _state_path(game_path)
    if not os.path.isfile(path):
        return ModdingState(game_path=game_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        state = ModdingState(
            version=raw.get("version", STATE_VERSION),
            last_updated=raw.get("last_updated", ""),
            cgm_version=raw.get("cgm_version", ""),
            dmm_version=raw.get("dmm_version", ""),
            game_path=game_path,
        )
        for gname, odata in raw.get("overlays", {}).items():
            if isinstance(odata, dict):
                state.overlays[gname] = OverlayEntry(
                    owner=odata.get("owner", "Unknown"),
                    content=odata.get("content", ""),
                    updated=odata.get("updated", ""),
                    files=odata.get("files", []),
                    paz_size=odata.get("paz_size", 0),
                    pamt_size=odata.get("pamt_size", 0),
                )
        for m in raw.get("dmm_mods", []):
            if isinstance(m, dict):
                state.dmm_mods.append(DmmModEntry(
                    file_name=m.get("file_name", ""),
                    title=m.get("title", ""),
                    author=m.get("author", ""),
                    version=m.get("version", ""),
                    patch_count=m.get("patch_count", 0),
                    game_files=m.get("game_files", []),
                    enabled=m.get("enabled", True),
                    targets_iteminfo=m.get("targets_iteminfo", False),
                ))
        state.dmm_asi_mods = raw.get("dmm_asi_mods", [])
        state.dmm_texture_mods = raw.get("dmm_texture_mods", [])
        state.dmm_browser_mods = raw.get("dmm_browser_mods", [])
        return state
    except Exception as e:
        log.warning("Failed to load shared state: %s", e)
        return ModdingState(game_path=game_path)


def save_state(game_path: str, state: ModdingState) -> bool:
    """Atomically write shared state to the game directory."""
    path = _state_path(game_path)
    state.last_updated = time.strftime("%Y-%m-%dT%H:%M:%S")
    state.game_path = game_path
    try:
        overlays_dict = {}
        for gname, entry in state.overlays.items():
            overlays_dict[gname] = asdict(entry)
        out = {
            "version": state.version,
            "last_updated": state.last_updated,
            "cgm_version": state.cgm_version,
            "dmm_version": state.dmm_version,
            "game_path": state.game_path,
            "overlays": overlays_dict,
            "dmm_mods": [asdict(m) for m in state.dmm_mods],
            "dmm_asi_mods": state.dmm_asi_mods,
            "dmm_texture_mods": state.dmm_texture_mods,
            "dmm_browser_mods": state.dmm_browser_mods,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:
        log.error("Failed to save shared state: %s", e)
        return False


def record_overlay(game_path: str, group: str, content: str,
                   files: list[str] | None = None) -> None:
    """Record that we just wrote an overlay group.  Called after every
    successful PAZ overlay write.
    """
    if not game_path:
        return
    state = load_state(game_path)
    paz_path = os.path.join(game_path, group, "0.paz")
    pamt_path = os.path.join(game_path, group, "0.pamt")
    state.overlays[group] = OverlayEntry(
        owner="CrimsonGameMods",
        content=content,
        updated=time.strftime("%Y-%m-%dT%H:%M:%S"),
        files=files or [],
        paz_size=os.path.getsize(paz_path) if os.path.isfile(paz_path) else 0,
        pamt_size=os.path.getsize(pamt_path) if os.path.isfile(pamt_path) else 0,
    )
    from crimson.game_mods.updater import APP_VERSION
    state.cgm_version = APP_VERSION
    save_state(game_path, state)


def remove_overlay(game_path: str, group: str) -> None:
    """Remove an overlay record when we delete/revert it."""
    if not game_path:
        return
    state = load_state(game_path)
    state.overlays.pop(group, None)
    save_state(game_path, state)


def scan_dmm_into_state(game_path: str, dmm_exe_path: str) -> ModdingState:
    """Read DMM's config.json, scan its mods folder, populate shared state
    with DMM mod details. Returns the updated state.
    """
    state = load_state(game_path)

    config_path = Path(dmm_exe_path).parent / "config.json"
    if not config_path.is_file():
        return state

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            dmm_cfg = json.load(f)
    except Exception as e:
        log.warning("Failed to read DMM config: %s", e)
        return state

    state.dmm_version = "1.3.0"  # from tauri.conf.json
    state.dmm_asi_mods = dmm_cfg.get("activeAsiMods", [])
    state.dmm_texture_mods = dmm_cfg.get("activeTextures", [])
    state.dmm_browser_mods = dmm_cfg.get("activeBrowserMods", [])

    mods_path = dmm_cfg.get("modsPath", "")
    active_mods = dmm_cfg.get("activeMods", [])
    state.dmm_mods = []

    for mod_entry in active_mods:
        file_name = mod_entry.get("fileName", "") if isinstance(mod_entry, dict) else str(mod_entry)
        if not file_name:
            continue
        disabled = mod_entry.get("disabledIndices", []) if isinstance(mod_entry, dict) else []
        dmm_mod = DmmModEntry(file_name=file_name, enabled=True)

        if mods_path and os.path.isdir(mods_path):
            json_path = os.path.join(mods_path, file_name)
            if os.path.isfile(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        mod_data = json.load(f)
                    info = mod_data.get("modinfo") or mod_data
                    dmm_mod.title = info.get("title") or info.get("name") or file_name
                    dmm_mod.author = info.get("author", "")
                    dmm_mod.version = info.get("version", "")
                    patches = mod_data.get("patches", [])
                    total_changes = 0
                    game_files = set()
                    for patch in patches:
                        gf = patch.get("game_file", "")
                        game_files.add(gf)
                        changes = patch.get("changes", [])
                        active_changes = [
                            c for i, c in enumerate(changes)
                            if i not in disabled
                        ]
                        total_changes += len(active_changes)
                        if "iteminfo.pabgb" in gf.lower():
                            dmm_mod.targets_iteminfo = True
                    dmm_mod.patch_count = total_changes
                    dmm_mod.game_files = sorted(game_files)
                except Exception:
                    dmm_mod.title = file_name
        else:
            dmm_mod.title = file_name

        state.dmm_mods.append(dmm_mod)

    # Scan overlay groups owned by DMM
    _DMM_PREFIXES = ("dmmsa", "dmmgen", "dmmequ", "dmmlang")
    if os.path.isdir(game_path):
        for entry in os.listdir(game_path):
            entry_path = os.path.join(game_path, entry)
            if not os.path.isdir(entry_path):
                continue
            paz = os.path.join(entry_path, "0.paz")
            pamt = os.path.join(entry_path, "0.pamt")
            if not os.path.isfile(paz) and not os.path.isfile(pamt):
                continue
            if entry.startswith(_DMM_PREFIXES):
                state.overlays[entry] = OverlayEntry(
                    owner="DMM",
                    content=f"DMM overlay ({entry})",
                    updated=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    paz_size=os.path.getsize(paz) if os.path.isfile(paz) else 0,
                    pamt_size=os.path.getsize(pamt) if os.path.isfile(pamt) else 0,
                )

    save_state(game_path, state)
    return state


def get_dmm_iteminfo_mods(game_path: str, dmm_exe_path: str
                          ) -> list[tuple[str, str, dict]]:
    """Return DMM mods that target iteminfo.pabgb.

    Returns list of (file_name, mods_folder_path, parsed_json_doc).
    Used by Stacker's "Pull from DMM" and ModLoader's "Convert" feature.
    """
    config_path = Path(dmm_exe_path).parent / "config.json"
    if not config_path.is_file():
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            dmm_cfg = json.load(f)
    except Exception:
        return []

    mods_path = dmm_cfg.get("modsPath", "")
    active_mods = dmm_cfg.get("activeMods", [])
    if not mods_path or not os.path.isdir(mods_path):
        return []

    results = []
    for mod_entry in active_mods:
        file_name = mod_entry.get("fileName", "") if isinstance(mod_entry, dict) else str(mod_entry)
        if not file_name:
            continue
        json_path = os.path.join(mods_path, file_name)
        if not os.path.isfile(json_path):
            continue
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            patches = doc.get("patches", [])
            if any("iteminfo.pabgb" in (p.get("game_file") or "").lower() for p in patches):
                results.append((file_name, mods_path, doc))
        except Exception:
            continue
    return results


def get_dmm_all_mods(dmm_exe_path: str) -> list[tuple[str, str, dict]]:
    """Return ALL DMM mods with their parsed JSON docs.

    Returns list of (file_name, mods_folder_path, parsed_json_doc).
    """
    config_path = Path(dmm_exe_path).parent / "config.json"
    if not config_path.is_file():
        return []
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            dmm_cfg = json.load(f)
    except Exception:
        return []

    mods_path = dmm_cfg.get("modsPath", "")
    if not mods_path or not os.path.isdir(mods_path):
        return []

    results = []
    for fname in os.listdir(mods_path):
        if not fname.lower().endswith(".json"):
            continue
        json_path = os.path.join(mods_path, fname)
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if doc.get("patches"):
                results.append((fname, mods_path, doc))
        except Exception:
            continue
    return results
