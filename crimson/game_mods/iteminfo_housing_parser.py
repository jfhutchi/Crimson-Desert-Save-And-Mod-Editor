"""
Fallback parser for housing/furniture items that the Rust parser can't handle.

These items have 4 u32 fields after sharpness_data instead of 3.
The Rust parser's fixed struct can't switch between layouts.

This module provides field-name mapping for the 8 known housing items
so the Stacker can track every byte by field name.
"""

import struct
from crimson.game_mods import crimson_rs as crimson_rs


def parse_housing_item(data: bytes) -> dict | None:
    """Try parsing an item that failed the Rust parser.

    Returns a parsed dict with all field names, or None if this
    isn't a housing item / can't be parsed either.
    """
    # First try the standard Rust parser (might work if the item
    # was misidentified as housing)
    try:
        items = crimson_rs.parse_iteminfo_from_bytes(data)
        if items:
            return items[0]
    except Exception:
        pass

    # The housing items differ ONLY in having 4 u32s after sharpness
    # instead of 3. We parse by:
    # 1. Finding where the Rust parser would diverge (after sharpness)
    # 2. Reading the extra u32
    # 3. Continuing with the standard layout

    # For now, return None — the raw bytes are preserved by _rebuild_full_iteminfo.
    # Full Python field-by-field parser can be added here when needed.
    return None


HOUSING_ITEM_KEYS = {1003774, 1003823, 1003824, 1003825, 1003976, 1003977, 1003978, 1003979}


def is_housing_item(key: int) -> bool:
    return key in HOUSING_ITEM_KEYS
