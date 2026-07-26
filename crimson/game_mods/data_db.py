from __future__ import annotations

import gzip
import os
import sqlite3
import sys

DB_FILENAME  = "crimson_data.db"
DBZ_FILENAME = "crimson_data.db.gz"

_connection: sqlite3.Connection | None = None


def _exe_dir() -> str:
    return (
        os.path.dirname(os.path.abspath(sys.executable))
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__))
    )


def _resolve() -> tuple[str, bool]:
    exe    = _exe_dir()
    meipass = getattr(sys, "_MEIPASS", exe)
    dev    = os.path.dirname(os.path.abspath(__file__))

    candidates = [
        (os.path.join(exe,     DB_FILENAME),  False),
        (os.path.join(meipass, DBZ_FILENAME), True),
        (os.path.join(meipass, DB_FILENAME),  False),
        (os.path.join(dev,     DB_FILENAME),  False),
    ]
    for path, compressed in candidates:
        if os.path.isfile(path):
            return path, compressed
    return candidates[-1][0], False


def get_db_path() -> str:
    path, _ = _resolve()
    return path


def _open_compressed(path: str) -> sqlite3.Connection:
    with gzip.open(path, "rb") as f:
        data = f.read()
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.deserialize(data)
    conn.row_factory = sqlite3.Row
    return conn


def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        path, compressed = _resolve()
        if not os.path.isfile(path):
            _connection = sqlite3.connect(":memory:", check_same_thread=False)
            _connection.row_factory = sqlite3.Row
        elif compressed:
            _connection = _open_compressed(path)
        else:
            _connection = sqlite3.connect(
                f"file:{path}?mode=ro", uri=True, check_same_thread=False
            )
            _connection.row_factory = sqlite3.Row
    return _connection


def open_writable() -> sqlite3.Connection:
    import shutil

    target = os.path.join(_exe_dir(), DB_FILENAME)
    if not os.path.isfile(target):
        path, compressed = _resolve()
        if os.path.isfile(path) and path != target:
            if compressed:
                with gzip.open(path, "rb") as f:
                    data = f.read()
                with open(target, "wb") as out:
                    out.write(data)
            else:
                shutil.copy2(path, target)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_TABLE_TO_JSON: dict[str, str] = {
    "characters":           "character_catalog.json",
    "equipment_slots":      "equip_type_hash_map.json",
    "item_icons":           "icon_urls.json",
    "world_locations":      "all_world_locations.json",
    "faction_nodes":        "faction_node_locations.json",
    "quest_chains":         "quest_chains.json",
    "quest_chain_members":  "quest_chains.json",
    "dye_slots":            "dye_slot_db.json",
}

USER_EDITABLE_NEVER_DB = frozenset({
    "knowledge_packs", "equipment_sets", "editor_config",
    "localization", "localization_eng", "locale",
})

import logging
_log = logging.getLogger(__name__)
_FALLBACK_WARNED: set[str] = set()


def _data_dir_candidates() -> list[str]:
    exe = _exe_dir()
    meipass = getattr(sys, "_MEIPASS", "") or ""
    here = os.path.dirname(os.path.abspath(__file__))
    out: list[str] = []
    for base in (exe, meipass, here):
        if base:
            out.append(os.path.join(base, "data"))
            out.append(base)
    return out


def _load_json_fallback(filename: str):
    import json
    for cand in _data_dir_candidates():
        p = os.path.join(cand, filename)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                _log.warning("data_db: JSON fallback %s failed: %s", filename, e)
    return None


def query_with_fallback(sql: str, params: tuple = (),
                        table: str | None = None,
                        json_to_rows=None) -> list:
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    except sqlite3.Error as e:
        rows = []
        _log.warning("data_db: SQL error on %s — %s", table, e)

    if rows:
        return rows

    if not table or table not in _TABLE_TO_JSON:
        return rows
    if table in USER_EDITABLE_NEVER_DB:
        return rows

    fname = _TABLE_TO_JSON[table]
    data = _load_json_fallback(fname)
    if data is None:
        return rows

    if table not in _FALLBACK_WARNED:
        _log.warning(
            "data_db: table %r empty in SQLite — falling back to %s",
            table, fname,
        )
        _FALLBACK_WARNED.add(table)

    if json_to_rows is not None:
        try:
            return json_to_rows(data)
        except Exception as e:
            _log.warning("data_db: json_to_rows for %s failed: %s", table, e)
            return []
    return [data]


def reset_connection() -> None:
    global _connection
    if _connection is not None:
        try:
            _connection.close()
        except Exception:
            pass
        _connection = None
