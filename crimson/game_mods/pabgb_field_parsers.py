
import struct
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


@dataclass
class ParsedField:
    name: str
    offset: int
    size: int
    value: any
    field_type: str
    editable: bool = True
    display: str = ""
    category: str = ""


STAT_HASHES = {
    0x01: "Attack", 0x02: "Defense", 0x03: "Accuracy", 0x04: "HP",
    0x05: "MP", 0x0A: "HPRegen", 0x0B: "MPRegen",
    0x5A: "DDD", 0x5B: "DPV", 0x64: "AttackSpeed",
    0x65: "Invincible", 0x66: "CritRate", 0x67: "CritDamage",
    0x6E: "MoveSpeed", 0x6F: "CastSpeed",
}

RATE_RANGES = [(1, 100000)]


def _scan_item_keys(data: bytes, start: int, end: int, known_keys: set) -> List[ParsedField]:
    fields = []
    for off in range(start, min(end - 3, len(data) - 3), 4):
        v = struct.unpack_from('<I', data, off)[0]
        if v in known_keys:
            fields.append(ParsedField(
                name=f"item_ref", offset=off, size=4, value=v,
                field_type='item_key', category='Items',
                display=f"Item {v}",
            ))
    return fields


def _scan_stat_hashes(data: bytes, start: int, end: int) -> List[ParsedField]:
    fields = []
    for off in range(start, min(end - 7, len(data) - 7), 4):
        v = struct.unpack_from('<I', data, off)[0]
        if v in STAT_HASHES:
            val_after = struct.unpack_from('<i', data, off + 4)[0] if off + 8 <= len(data) else 0
            val_i64 = struct.unpack_from('<q', data, off + 4)[0] if off + 12 <= len(data) else 0
            fields.append(ParsedField(
                name=STAT_HASHES[v], offset=off + 4, size=4, value=val_after,
                field_type='i32', category='Stats',
                display=f"{STAT_HASHES[v]} = {val_after}",
            ))
    return fields


def _scan_rates(data: bytes, start: int, end: int) -> List[ParsedField]:
    fields = []
    seen = set()
    for off in range(start, min(end - 3, len(data) - 3), 4):
        v = struct.unpack_from('<I', data, off)[0]
        if 100 <= v <= 100000 and v % 100 == 0 and off not in seen:
            if v not in STAT_HASHES:
                pct = v / 100.0
                fields.append(ParsedField(
                    name=f"rate", offset=off, size=4, value=v,
                    field_type='rate', category='Rates',
                    display=f"{pct:.1f}%",
                ))
                seen.add(off)
    return fields


def _scan_ff_separated_entries(data: bytes, start: int, end: int,
                                known_keys: set) -> List[ParsedField]:
    fields = []
    pos = start
    idx = 0
    while pos < end - 5:
        if data[pos] == 0xFF and data[pos + 1] == 0xFF:
            item_key = struct.unpack_from('<I', data, pos + 2)[0]
            if item_key > 0 and item_key < 0x7FFFFFFF:
                fields.append(ParsedField(
                    name=f"drop_item_{idx}", offset=pos + 2, size=4, value=item_key,
                    field_type='item_key', category='Drop Items',
                    display=f"Drop Item {idx}: {item_key}",
                ))
                idx += 1
            pos += 6
        else:
            pos += 1
    return fields


def parse_dropset_record(data: bytes, rec_offset: int, rec_size: int,
                          known_item_keys: set) -> List[ParsedField]:
    end = rec_offset + rec_size
    fields = []

    ff_items = _scan_ff_separated_entries(data, rec_offset, end, known_item_keys)
    fields.extend(ff_items)

    rates = _scan_rates(data, rec_offset, end)
    fields.extend(rates)

    items = _scan_item_keys(data, rec_offset, end, known_item_keys)
    ff_offsets = {f.offset for f in ff_items}
    fields.extend(f for f in items if f.offset not in ff_offsets)

    return fields


def parse_skill_record(data: bytes, rec_offset: int, rec_size: int) -> List[ParsedField]:
    end = rec_offset + rec_size
    fields = []

    fields.extend(_scan_stat_hashes(data, rec_offset, end))

    for off in range(rec_offset, min(end - 3, len(data) - 3), 4):
        v = struct.unpack_from('<I', data, off)[0]
        if 500 <= v <= 120000 and v % 100 == 0:
            fields.append(ParsedField(
                name="cooldown_ms", offset=off, size=4, value=v,
                field_type='u32', category='Timing',
                display=f"{v}ms ({v/1000:.1f}s)",
            ))

    for off in range(rec_offset, min(end - 3, len(data) - 3), 4):
        v = struct.unpack_from('<f', data, off)[0]
        if 0.1 < v < 50.0 and v != 1.0 and abs(v - round(v, 1)) < 0.01:
            fields.append(ParsedField(
                name="multiplier", offset=off, size=4, value=v,
                field_type='f32', category='Values',
                display=f"{v:.2f}x",
            ))

    return fields


def parse_buff_record(data: bytes, rec_offset: int, rec_size: int) -> List[ParsedField]:
    end = rec_offset + rec_size
    fields = []

    fields.extend(_scan_stat_hashes(data, rec_offset, end))

    for off in range(rec_offset, min(end - 7, len(data) - 7), 4):
        v = struct.unpack_from('<I', data, off)[0]
        if 1000 <= v <= 3600000 and v % 1000 == 0:
            fields.append(ParsedField(
                name="duration_ms", offset=off, size=4, value=v,
                field_type='u32', category='Timing',
                display=f"{v}ms ({v/1000:.0f}s)",
            ))

    fields.extend(_scan_rates(data, rec_offset, end))

    return fields


def parse_character_record(data: bytes, rec_offset: int, rec_size: int) -> List[ParsedField]:
    end = rec_offset + rec_size
    fields = []

    stat_fields = _scan_stat_hashes(data, rec_offset, min(end, rec_offset + 2000))
    fields.extend(stat_fields)

    for off in range(rec_offset, min(rec_offset + 500, end - 3), 4):
        v = struct.unpack_from('<I', data, off)[0]
        if 100 <= v <= 10000000 and v not in STAT_HASHES:
            if off >= rec_offset + 4:
                prev = struct.unpack_from('<I', data, off - 4)[0]
                if prev in STAT_HASHES:
                    continue
            if v >= 1000:
                fields.append(ParsedField(
                    name="stat_value", offset=off, size=4, value=v,
                    field_type='u32', category='Stats',
                    display=f"{v:,}",
                ))

    return fields


def parse_condition_record(data: bytes, rec_offset: int, rec_size: int) -> List[ParsedField]:
    end = rec_offset + rec_size
    fields = []

    for off in range(rec_offset, min(end - 3, len(data) - 3), 4):
        v = struct.unpack_from('<I', data, off)[0]
        if 0 < v < 1000000 and v not in STAT_HASHES:
            fields.append(ParsedField(
                name="param", offset=off, size=4, value=v,
                field_type='u32', category='Parameters',
                display=str(v),
            ))

    return fields


def parse_faction_record(data: bytes, rec_offset: int, rec_size: int) -> List[ParsedField]:
    end = rec_offset + rec_size
    fields = []

    for off in range(rec_offset, min(end - 3, len(data) - 3), 4):
        v = struct.unpack_from('<I', data, off)[0]
        if 50 <= v <= 1000000:
            fields.append(ParsedField(
                name="reputation_threshold", offset=off, size=4, value=v,
                field_type='u32', category='Reputation',
                display=f"{v:,} rep",
            ))

    fields.extend(_scan_stat_hashes(data, rec_offset, end))

    return fields


PARSERS = {
    'dropsetinfo': parse_dropset_record,
    'skill': parse_skill_record,
    'buffinfo': parse_buff_record,
    'characterinfo': parse_character_record,
    'conditioninfo': parse_condition_record,
    'faction': parse_faction_record,
}


def get_parser(file_name: str):
    return PARSERS.get(file_name)
