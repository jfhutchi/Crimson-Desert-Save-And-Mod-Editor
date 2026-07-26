
import struct
from typing import List, Dict, Optional, Tuple, Any

RECORD_TERMINATOR = bytes([0x0B, 0x73, 0xE1, 0xC5, 0xEA, 0x00])
SUB_RECORD_SEP    = bytes([0x07, 0x73, 0xE1, 0xC5, 0xEA, 0x00])
BASE_BLOCK_SIZE   = 89


class StatBlock:

    __slots__ = (
        "reserved", "sub_level_count", "base_experience", "max_experience",
        "exp_scale", "level_cap", "unknown_i32", "hash_1", "hash_2",
        "growth_ref_1", "growth_ref_2", "tail_raw", "offset",
    )

    def __init__(self, buf: bytes, offset: int = 0):
        self.offset = offset
        self.reserved       = struct.unpack_from("<i", buf, 0)[0]
        self.sub_level_count = struct.unpack_from("<I", buf, 4)[0]
        self.base_experience = struct.unpack_from("<Q", buf, 8)[0]
        self.max_experience  = struct.unpack_from("<Q", buf, 16)[0]
        self.exp_scale       = struct.unpack_from("<Q", buf, 24)[0]
        self.level_cap       = struct.unpack_from("<I", buf, 32)[0]
        self.unknown_i32     = struct.unpack_from("<i", buf, 36)[0]
        self.hash_1          = struct.unpack_from("<I", buf, 40)[0]
        self.hash_2          = struct.unpack_from("<I", buf, 44)[0]
        self.growth_ref_1    = struct.unpack_from("<I", buf, 48)[0]
        self.growth_ref_2    = struct.unpack_from("<I", buf, 52)[0]
        self.tail_raw        = buf[56:89]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reserved": self.reserved,
            "sub_level_count": self.sub_level_count,
            "base_experience": self.base_experience,
            "max_experience": self.max_experience,
            "exp_scale": self.exp_scale,
            "level_cap": self.level_cap,
            "unknown_i32": self.unknown_i32,
            "hash_1": f"0x{self.hash_1:08X}",
            "hash_2": f"0x{self.hash_2:08X}",
            "growth_ref_1": self.growth_ref_1,
            "growth_ref_2": self.growth_ref_2,
        }

    def pack(self) -> bytes:
        out = bytearray(89)
        struct.pack_into("<i", out, 0,  self.reserved)
        struct.pack_into("<I", out, 4,  self.sub_level_count)
        struct.pack_into("<Q", out, 8,  self.base_experience)
        struct.pack_into("<Q", out, 16, self.max_experience)
        struct.pack_into("<Q", out, 24, self.exp_scale)
        struct.pack_into("<I", out, 32, self.level_cap)
        struct.pack_into("<i", out, 36, self.unknown_i32)
        struct.pack_into("<I", out, 40, self.hash_1)
        struct.pack_into("<I", out, 44, self.hash_2)
        struct.pack_into("<I", out, 48, self.growth_ref_1)
        struct.pack_into("<I", out, 52, self.growth_ref_2)
        out[56:89] = self.tail_raw
        return bytes(out)


class PabgbRecord:

    __slots__ = (
        "record_id", "name", "stat", "extra_data", "sub_records",
        "file_offset", "data_offset", "raw_data",
    )

    def __init__(self):
        self.record_id: int = 0
        self.name: str = ""
        self.stat: Optional[StatBlock] = None
        self.extra_data: bytes = b""
        self.sub_records: List["PabgbRecord"] = []
        self.file_offset: int = 0
        self.data_offset: int = 0
        self.raw_data: bytes = b""

    @property
    def header_size(self) -> int:
        return 4 + 4 + len(self.name) + 1

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "record_id": self.record_id,
            "name": self.name,
            "file_offset": f"0x{self.file_offset:X}",
            "data_offset": f"0x{self.data_offset:X}",
            "data_size": len(self.raw_data),
        }
        if self.stat:
            d["stat"] = self.stat.to_dict()
        if self.sub_records:
            d["sub_records"] = [sr.to_dict() for sr in self.sub_records]
        return d


def _parse_record_header(data: bytes, pos: int) -> Tuple[int, str, int]:
    rec_id = struct.unpack_from("<I", data, pos)[0]
    name_len = struct.unpack_from("<I", data, pos + 4)[0]
    name = data[pos + 8 : pos + 8 + name_len].decode("ascii", errors="replace")
    data_start = pos + 8 + name_len + 1
    return rec_id, name, data_start


def _parse_sub_records(raw_data: bytes, file_data_offset: int) -> List[PabgbRecord]:
    subs = []
    extra = raw_data[BASE_BLOCK_SIZE:]
    sep_positions = []
    p = 0
    while p < len(extra):
        idx = extra.find(SUB_RECORD_SEP, p)
        if idx == -1:
            break
        sep_positions.append(idx)
        p = idx + 6

    for i, sep_pos in enumerate(sep_positions):
        after = sep_pos + 6
        if after + 8 >= len(extra):
            break
        sub_rec = PabgbRecord()
        sub_rec.file_offset = file_data_offset + BASE_BLOCK_SIZE + after
        sub_id = struct.unpack_from("<I", extra, after)[0]
        sub_name_len = struct.unpack_from("<I", extra, after + 4)[0]
        if sub_name_len > 200:
            break
        sub_name = extra[after + 8 : after + 8 + sub_name_len].decode("ascii", errors="replace")
        sub_data_start = after + 8 + sub_name_len + 1

        if i + 1 < len(sep_positions):
            sub_data_end = sep_positions[i + 1]
        else:
            sub_data_end = len(extra)

        sub_rec.record_id = sub_id
        sub_rec.name = sub_name
        sub_rec.data_offset = file_data_offset + BASE_BLOCK_SIZE + sub_data_start
        sub_raw = extra[sub_data_start:sub_data_end]
        sub_rec.raw_data = sub_raw

        if len(sub_raw) >= BASE_BLOCK_SIZE:
            sub_rec.stat = StatBlock(sub_raw, sub_rec.data_offset)
            sub_rec.extra_data = sub_raw[BASE_BLOCK_SIZE:]
        elif len(sub_raw) >= 36:
            sub_rec.stat = StatBlock(sub_raw + b"\x00" * (BASE_BLOCK_SIZE - len(sub_raw)), sub_rec.data_offset)

        subs.append(sub_rec)

    return subs


def parse_pabgb(data: bytes) -> List[PabgbRecord]:
    records = []
    pos = 0

    while pos < len(data):
        term_idx = data.find(RECORD_TERMINATOR, pos)
        if term_idx == -1:
            break

        rec = PabgbRecord()
        rec.file_offset = pos
        rec.record_id, rec.name, data_start = _parse_record_header(data, pos)
        rec.data_offset = data_start
        rec.raw_data = data[data_start:term_idx]

        if len(rec.raw_data) >= BASE_BLOCK_SIZE:
            rec.stat = StatBlock(rec.raw_data[:BASE_BLOCK_SIZE], data_start)
            rec.extra_data = rec.raw_data[BASE_BLOCK_SIZE:]

            if SUB_RECORD_SEP in rec.raw_data:
                rec.sub_records = _parse_sub_records(rec.raw_data, data_start)
        elif len(rec.raw_data) >= 36:
            padded = rec.raw_data + b"\x00" * (BASE_BLOCK_SIZE - len(rec.raw_data))
            rec.stat = StatBlock(padded, data_start)

        records.append(rec)
        pos = term_idx + len(RECORD_TERMINATOR)

    return records


def parse_pabgb_file(filepath: str) -> List[PabgbRecord]:
    with open(filepath, "rb") as f:
        return parse_pabgb(f.read())


def parse_pabgh_index(pabgh_data: bytes, count_size: int = 2) -> Tuple[int, List[Tuple[int, int]]]:
    """Return ``(key_size, [(key, pabgb_offset), ...])`` from a PABGH index.

    Inventory uses a 2-byte entry count and 2-byte keys in current builds, but
    key size is derived so this also handles wider-key tables.
    """
    if len(pabgh_data) < count_size:
        raise ValueError("PABGH data is too short")

    if count_size == 2:
        entry_count = struct.unpack_from("<H", pabgh_data, 0)[0]
    elif count_size == 4:
        entry_count = struct.unpack_from("<I", pabgh_data, 0)[0]
    else:
        raise ValueError("count_size must be 2 or 4")

    if entry_count <= 0:
        return 0, []

    key_bytes_total = len(pabgh_data) - count_size - entry_count * 4
    if key_bytes_total < 0 or key_bytes_total % entry_count:
        raise ValueError(
            f"Cannot derive PABGH key size from {len(pabgh_data)} bytes "
            f"and {entry_count} entries"
        )

    key_size = key_bytes_total // entry_count
    entries: List[Tuple[int, int]] = []
    pos = count_size
    for _ in range(entry_count):
        key = int.from_bytes(pabgh_data[pos:pos + key_size], "little")
        offset = struct.unpack_from("<I", pabgh_data, pos + key_size)[0]
        entries.append((key, offset))
        pos += key_size + 4

    return key_size, entries


def _parse_inventory_header(data: bytes, offset: int) -> Tuple[int, str, int]:
    if offset + 6 > len(data):
        raise ValueError(f"Inventory record at 0x{offset:X} is truncated")
    rec_id = struct.unpack_from("<H", data, offset)[0]
    name_len = struct.unpack_from("<I", data, offset + 2)[0]
    name_start = offset + 6
    name_end = name_start + name_len
    if name_len <= 0 or name_len > 200 or name_end >= len(data):
        raise ValueError(f"Bad inventory record name length at 0x{offset:X}: {name_len}")
    if data[name_end] != 0:
        raise ValueError(f"Inventory record name at 0x{offset:X} is not null-terminated")
    name = data[name_start:name_end].decode("utf-8", errors="replace")
    return rec_id, name, name_end + 1


def _find_inventory_slot_offsets(data: bytes, payload_start: int, payload_end: int,
                                 key: int) -> Optional[Tuple[int, int]]:
    """Find default/max slot fields in old and current InventoryInfo payloads.

    Older inventory.pabgb builds stored ``marker, default_u16, max_u16`` at the
    start of the payload. Current builds moved the slot pair near the tail,
    directly before the record's own string-key reader block:

        default_u16 max_u16 28 80 02 00 00 <key:u32> ...
    """
    if payload_end - payload_start < 4:
        return None

    # Legacy layout: first byte is a small flag/marker, followed by slots.
    if payload_start + 5 <= payload_end and data[payload_start] in (0, 1, 2):
        default_slots = struct.unpack_from("<H", data, payload_start + 1)[0]
        max_slots = struct.unpack_from("<H", data, payload_start + 3)[0]
        if 0 < default_slots <= max_slots <= 10000:
            return payload_start + 1, payload_start + 3

    key_bytes = struct.pack("<I", key)
    reader_marker = b"\x28\x80\x02\x00\x00" + key_bytes
    search_from = payload_start
    matches: List[int] = []
    while True:
        marker_pos = data.find(reader_marker, search_from, payload_end)
        if marker_pos < 0:
            break
        if marker_pos - 4 >= payload_start:
            default_off = marker_pos - 4
            max_off = marker_pos - 2
            default_slots = struct.unpack_from("<H", data, default_off)[0]
            max_slots = struct.unpack_from("<H", data, max_off)[0]
            if 0 < default_slots <= 10000 and 0 < max_slots <= 10000:
                matches.append(default_off)
        search_from = marker_pos + 1

    if matches:
        default_off = matches[-1]
        return default_off, default_off + 2

    return None


def _parse_inventory_from_index(data: bytes, index_entries: List[Tuple[int, int]]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    sorted_entries = sorted(index_entries, key=lambda item: item[1])

    for i, (key, rec_offset) in enumerate(sorted_entries):
        rec_end = sorted_entries[i + 1][1] if i + 1 < len(sorted_entries) else len(data)
        if rec_offset >= len(data) or rec_end > len(data) or rec_offset >= rec_end:
            continue

        rec_id, name, payload_start = _parse_inventory_header(data, rec_offset)
        slot_offsets = _find_inventory_slot_offsets(data, payload_start, rec_end, key)
        entry: Dict[str, Any] = {
            "key": key,
            "record_id": rec_id,
            "name": name,
            "offset": rec_offset,
            "data_offset": payload_start,
            "data_end": rec_end,
            "data_size": rec_end - payload_start,
        }

        if slot_offsets:
            default_off, max_off = slot_offsets
            entry.update({
                "default_slots": struct.unpack_from("<H", data, default_off)[0],
                "max_slots": struct.unpack_from("<H", data, max_off)[0],
                "default_offset": default_off,
                "max_offset": max_off,
                "slot_pair_offset": default_off,
            })

        entries.append(entry)

    return entries


def _scan_inventory_records(data: bytes) -> List[Tuple[int, int]]:
    """Best-effort inventory record scan used when the companion PABGH is absent."""
    entries: List[Tuple[int, int]] = []
    offset = 0
    while offset < len(data) - 8:
        try:
            key = struct.unpack_from("<H", data, offset)[0]
            name_len = struct.unpack_from("<I", data, offset + 2)[0]
            name_end = offset + 6 + name_len
            if 0 < name_len <= 100 and name_end < len(data) and data[name_end] == 0:
                name_bytes = data[offset + 6:name_end]
                if all(32 <= b < 127 for b in name_bytes):
                    entries.append((key, offset))
                    offset = name_end + 1
                    continue
        except struct.error:
            break
        offset += 1
    return entries


def parse_inventory_pabgb(data: bytes, pabgh_data: Optional[bytes] = None) -> List[Dict[str, Any]]:
    if pabgh_data:
        _, index_entries = parse_pabgh_index(pabgh_data)
    else:
        index_entries = _scan_inventory_records(data)
    return _parse_inventory_from_index(data, index_entries)


def parse_inventory_pabgb_file(filepath: str, pabgh_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with open(filepath, "rb") as f:
        data = f.read()
    pabgh_data = None
    if pabgh_path:
        with open(pabgh_path, "rb") as f:
            pabgh_data = f.read()
    return parse_inventory_pabgb(data, pabgh_data)


def parse_iteminfo_records(data: bytes) -> List[PabgbRecord]:
    return parse_pabgb(data)


def summarize_records(records: List[PabgbRecord]) -> str:
    lines = []
    lines.append(f"{'ID':>6s}  {'Name':<30s}  {'SubLvl':>6s}  {'BaseXP':>12s}  {'MaxXP':>12s}  {'Cap':>4s}  {'Bonus':>6s}  {'Subs':>4s}")
    lines.append("-" * 110)
    for r in records:
        s = r.stat
        if s:
            bonus_str = ""
            if r.extra_data and len(r.extra_data) >= 2 and not r.sub_records:
                bonus = struct.unpack_from("<H", r.extra_data, 0)[0]
                if bonus > 0:
                    bonus_str = f"{bonus:6d}"
            lines.append(
                f"{r.record_id:6d}  {r.name:<30s}  {s.sub_level_count:6d}  "
                f"{s.base_experience:12,d}  {s.max_experience:12,d}  {s.level_cap:4d}  "
                f"{bonus_str:>6s}  {len(r.sub_records):4d}"
            )
            for sub in r.sub_records:
                ss = sub.stat
                if ss:
                    sub_bonus = ""
                    if sub.extra_data and len(sub.extra_data) >= 2:
                        b = struct.unpack_from("<H", sub.extra_data, 0)[0]
                        if b > 0:
                            sub_bonus = f"{b:6d}"
                    lines.append(
                        f"  {'':>4s}  +- {sub.name:<26s}  {ss.sub_level_count:6d}  "
                        f"{ss.base_experience:12,d}  {ss.max_experience:12,d}  {ss.level_cap:4d}  "
                        f"{sub_bonus:>6s}"
                    )
        else:
            lines.append(f"{r.record_id:6d}  {r.name:<30s}  {'?':>6s}  {'?':>12s}  data={len(r.raw_data)}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python pabgb_parser.py <file.pabgb>")
        sys.exit(0)

    filepath = sys.argv[1]
    records = parse_pabgb_file(filepath)
    print(f"\nParsed {len(records)} records from {filepath}\n")
    print(summarize_records(records))
