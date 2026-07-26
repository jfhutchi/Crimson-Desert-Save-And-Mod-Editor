#!/usr/bin/env python3
"""
Universal PABGB/PABGH Parser — Crimson Desert game data tables.

Combines:
  - Potter's pycrimson pabgh index approach (correct entry boundaries)
  - pabgb_parser.py entry header scanning (string extraction, field analysis)
  - crimson_rs PAZ extraction (get raw bytes from game archives)

Usage as CLI:
  # Parse a single file (needs both .pabgb and .pabgh)
  python universal_pabgb_parser.py skill.pabgb --filter JiJeongTa

  # Extract from game PAZ archives directly
  python universal_pabgb_parser.py --game "C:/Program Files/Steam/.../Crimson Desert" --table skill --filter JiJeongTa

  # Dump to JSON
  python universal_pabgb_parser.py skill.pabgb --json output.json

  # Full field analysis with float/hash/string decode
  python universal_pabgb_parser.py skill.pabgb --filter JiJeongTa --deep

Usage as library:
  from tools.universal_pabgb_parser import PabgbParser, parse_from_game
  parser = parse_from_game(game_dir, "skill")
  for entry in parser.find("JiJeongTa"):
      print(entry.name, entry.fields)
"""

import io
import json
import os
import struct
import sys
from dataclasses import dataclass, field
from typing import Optional

# ─── Tables that use u32 count instead of u16 in pabgh ───────────────────────
# From Potter's pycrimson: these specific tables have 4-byte count prefix
UINT_COUNT_TABLES = frozenset({
    "characterappearanceindexinfo", "globalstagesequencerinfo",
    "sequencerspawninfo", "sheetmusicinfo", "spawningpoolautospawninfo",
    "itemuseinfo", "terrainregionautospawninfo", "textguideinfo",
    "validscheduleaction", "stageinfo", "questinfo", "gimmickeventtableinfo",
    "reviepointinfo", "aidialogstringinfo", "dialogsetinfo",
    "vibratepatterninfo", "platformachievementinfo",
    "levelgimmicksceneobjectinfo", "fieldlevelnametableinfo", "levelinfo",
    "board", "gameplaytrigger", "characterchange", "materialrelationinfo",
})


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class DecodedField:
    """A single decoded value from a payload."""
    offset: int
    size: int
    type: str       # "u8", "u16", "u32", "i32", "f32", "string", "hash", "raw"
    raw: bytes
    value: object   # int, float, str, or bytes
    label: str = "" # human-readable hint

    def __repr__(self):
        if self.type == "string":
            return f'+0x{self.offset:04X} STRING[{self.size}] = "{self.value}"'
        elif self.type == "f32":
            return f'+0x{self.offset:04X} FLOAT = {self.value:.6f}'
        elif self.type == "hash":
            return f'+0x{self.offset:04X} HASH = 0x{self.value:08X}{f" ({self.label})" if self.label else ""}'
        elif self.type in ("u32", "u16", "u8", "i32"):
            hint = f' ({self.label})' if self.label else ''
            return f'+0x{self.offset:04X} {self.type} = {self.value}{hint}'
        else:
            return f'+0x{self.offset:04X} [{self.size}B] {self.raw.hex()}'


@dataclass
class PabgbEntry:
    """A single entry in a PABGB table."""
    key: int                  # Entry key from pabgh index
    entry_id: int             # Entry ID from the entry header (usually == key)
    name: str                 # Entry name (UTF-8 string after header)
    payload: bytes            # Raw payload bytes (after name + null)
    file_offset: int          # Absolute offset in the .pabgb file
    entry_size: int           # Total entry size (from pabgh boundaries)
    strings: list[str] = field(default_factory=list)
    fields: list[DecodedField] = field(default_factory=list)

    def to_dict(self, include_hex: bool = False) -> dict:
        d = {
            "key": self.key,
            "entry_id": self.entry_id,
            "name": self.name,
            "file_offset": f"0x{self.file_offset:X}",
            "entry_size": self.entry_size,
            "payload_size": len(self.payload),
        }
        if self.strings:
            d["strings"] = self.strings
        if self.fields:
            d["fields"] = [
                {"offset": f.offset, "type": f.type, "value": f.value,
                 "label": f.label} if f.type != "raw"
                else {"offset": f.offset, "type": "raw", "hex": f.raw.hex()}
                for f in self.fields
            ]
        if include_hex:
            d["payload_hex"] = self.payload.hex()
        return d


@dataclass
class PabgbParser:
    """Parsed PABGB table with pabgh index."""
    table_name: str
    entries: list[PabgbEntry]
    entry_count: int
    key_size: int
    data_size: int
    schema_size: int

    def find(self, pattern: str) -> list[PabgbEntry]:
        """Find entries by name substring (case-insensitive)."""
        pat = pattern.lower()
        return [e for e in self.entries
                if pat in e.name.lower()
                or any(pat in s.lower() for s in e.strings)]

    def get(self, key: int) -> Optional[PabgbEntry]:
        """Get entry by key."""
        for e in self.entries:
            if e.key == key:
                return e
        return None

    def get_by_name(self, name: str) -> Optional[PabgbEntry]:
        """Get entry by exact name."""
        for e in self.entries:
            if e.name == name:
                return e
        return None

    def summary(self) -> str:
        lines = [
            f"Table: {self.table_name}",
            f"Entries: {self.entry_count} (key_size={self.key_size}B)",
            f"Data: {self.data_size:,}B  Schema: {self.schema_size:,}B",
            "",
        ]
        # Size distribution
        sizes = [e.entry_size for e in self.entries]
        if sizes:
            lines.append(f"Entry sizes: min={min(sizes)}, max={max(sizes)}, "
                         f"avg={sum(sizes)//len(sizes)}, median={sorted(sizes)[len(sizes)//2]}")
        return "\n".join(lines)


# ─── Parsing ──────────────────────────────────────────────────────────────────

def _parse_pabgh_index(schema_bytes: bytes, table_name: str) -> tuple[int, dict[int, int]]:
    """Parse pabgh index file. Returns (key_size, {key: offset})."""
    name_lower = table_name.lower()
    count_size = 4 if name_lower in UINT_COUNT_TABLES else 2

    if count_size == 4:
        count = struct.unpack_from('<I', schema_bytes, 0)[0]
    else:
        count = struct.unpack_from('<H', schema_bytes, 0)[0]

    total_key_size = len(schema_bytes) - count_size - count * 4
    key_size = total_key_size // count
    assert key_size * count == total_key_size, \
        f"key_size calc failed: {total_key_size} / {count} = {total_key_size/count}"

    offsets = {}
    pos = count_size
    for _ in range(count):
        key = int.from_bytes(schema_bytes[pos:pos + key_size], 'little')
        offset = struct.unpack_from('<I', schema_bytes, pos + key_size)[0]
        offsets[key] = offset
        pos += key_size + 4

    return key_size, offsets


def _parse_entry_header(data: bytes, offset: int) -> tuple[int, str, int]:
    """Parse entry header at offset. Returns (entry_id, name, payload_start)."""
    # Try u32 ID first (most common)
    for id_width in (4, 2, 1):
        if offset + id_width + 4 > len(data):
            continue

        if id_width == 4:
            eid = struct.unpack_from('<I', data, offset)[0]
        elif id_width == 2:
            eid = struct.unpack_from('<H', data, offset)[0]
        else:
            eid = data[offset]

        nlen_off = offset + id_width
        nlen = struct.unpack_from('<I', data, nlen_off)[0]

        if nlen > 500:
            continue

        name_start = nlen_off + 4
        name_end = name_start + nlen

        if name_end >= len(data):
            continue

        if data[name_end] != 0x00:
            continue

        if nlen > 0:
            try:
                name = data[name_start:name_end].decode('utf-8')
                if any(ord(c) < 0x20 for c in name):
                    continue
            except UnicodeDecodeError:
                continue
        else:
            name = ""

        payload_start = name_end + 1  # after null terminator
        return eid, name, payload_start

    return 0, "", offset


def _extract_strings(payload: bytes) -> list[str]:
    """Extract u32-length-prefixed strings from payload."""
    strings = []
    pos = 0
    while pos + 4 < len(payload):
        slen = struct.unpack_from('<I', payload, pos)[0]
        if slen == 0 or slen > 500:
            pos += 1
            continue
        str_end = pos + 4 + slen
        if str_end > len(payload):
            pos += 1
            continue
        try:
            s = payload[pos + 4:str_end].decode('utf-8')
        except UnicodeDecodeError:
            pos += 1
            continue
        alnum = sum(1 for c in s if c.isalnum())
        if alnum >= 2 and alnum * 10 >= len(s) * 6 and s.isprintable():
            strings.append(s)
            pos = str_end
        else:
            pos += 1
    return strings


# ─── Known field schemas (from game memory RTTI) ─────────────────────────────

# SkillInfo fields discovered at 0x144934CE0 in CrimsonDesert.exe
# These are the actual readEntryFields names from the game's deserializer.
SKILLINFO_FIELDS = [
    "_key", "_stringKey", "_needUpgradeItemInfo", "_needUpgradeItemCountGraph",
    "_needUpgradeExperienceGraph", "_usableCharacterInfoList", "_usableCondition",
    "_learnKnowledgeInfo", "_factionInfo", "_useResourceStatList", "_isBlocked",
    "_cooltime", "_buffLevelList", "_skillGroupKey", "_parentSkill", "_learnLevel",
    "_applyType", "_iconPath", "_uiType", "_reserveSlotInfoList", "_maxLevel",
    "_skillGroupKeyList", "_buffSustainFlag", "_devSkillName", "_devSkillDesc",
    "_videoPath", "_useResourceItemList", "_useDriverResourceStatList",
    "_useBatteryStat", "_isUiUseAllowed", "_isLearnUseArtifact",
    "_allowSkillWithLowResource", "_isUseChildPatternDescriptionBuffData",
    "_damageType",
]

# Known field schemas per table type — maps table_name -> field name list
# Used by deep_decode to provide better labels
TABLE_FIELD_SCHEMAS: dict[str, list[str]] = {
    "skill": SKILLINFO_FIELDS,
}


def _deep_decode_payload(payload: bytes, table_name: str = "") -> list[DecodedField]:
    """Decode payload into typed fields (best-effort heuristic analysis).

    If table_name matches a known schema, field labels use real game field names.
    """
    schema_fields = TABLE_FIELD_SCHEMAS.get(table_name, [])
    fields = []
    string_idx = 0  # track which string we've seen (for schema mapping)
    non_zero_idx = 0  # track non-zero field index

    HASH_NAMES = {
        0x000F4240: "Hp", 0x000F4242: "DDD", 0x000F4243: "DPV",
        0x000F4247: "CritRate", 0x000F424A: "AtkSpeed",
        0x000F424B: "MoveSpeed", 0x000F4265: "StaminaReduce",
        0x000F4288: "MoreLumberDrop", 0x000F4287: "MoreOreDrop",
        0x000F4289: "CollectDrop_Plant", 0x000F429D: "CollectDrop_Log",
        0x000F429B: "ReduceCraftMaterial", 0x000F42A7: "FireResist",
        0x000F42A8: "IceResist", 0x000F42C7: "LightningResist",
    }

    # For skill table, label known offsets.
    # CONFIRMED 2026-04-02 by cross-referencing pabgb payload vs in-memory struct dump.
    # In-memory struct is 640B. _cooltime at struct+0x1D8 = pabgb+0x7C.
    # use_count at struct+0x240 = pabgb+0xD8.
    skill_offset_labels = {}
    if table_name == "skill":
        skill_offset_labels = {
            0x0004: "level/variant (CONFIRMED)",
            0x0008: "skill_tier",
            0x007C: "_cooltime (ms) (CONFIRMED)",
            0x0084: "_cooltime2 (ms) (CONFIRMED)",
            0x0094: "_flag",
            0x00B0: "_flag",
            0x00CC: "_flag",
            0x00D0: "_key_ref (CONFIRMED: hash)",
            0x00D8: "use_count/_comboCount (CONFIRMED)",
        }

    i = 0
    while i < len(payload):
        # Try string first (u32 length prefix)
        if i + 4 < len(payload):
            slen = struct.unpack_from('<I', payload, i)[0]
            if 2 < slen < 300 and i + 4 + slen <= len(payload):
                try:
                    s = payload[i + 4:i + 4 + slen].decode('utf-8')
                    if s.isprintable() and sum(1 for c in s if c.isalnum()) > len(s) * 0.4:
                        fields.append(DecodedField(
                            offset=i, size=4 + slen, type="string",
                            raw=payload[i:i + 4 + slen], value=s))
                        i += 4 + slen
                        continue
                except UnicodeDecodeError:
                    pass

        if i + 4 > len(payload):
            break

        u32 = struct.unpack_from('<I', payload, i)[0]
        f32 = struct.unpack_from('<f', payload, i)[0]
        raw = payload[i:i + 4]

        # Check for skill-specific offset label BEFORE skipping zeros
        label = skill_offset_labels.get(i, "")

        # Skip zero runs — BUT keep zeros at known labeled offsets
        if u32 == 0:
            if label:
                fields.append(DecodedField(
                    offset=i, size=4, type="u32", raw=raw, value=0, label=label))
            i += 4
            continue

        # Float range (reasonable game values)
        if 0x3D000000 <= u32 <= 0x44800000:
            fields.append(DecodedField(
                offset=i, size=4, type="f32", raw=raw, value=round(f32, 6),
                label=label))
            i += 4
            continue

        # Stat/skill hash range (0x000F4xxx)
        if 0x000F4200 <= u32 <= 0x000F4FFF:
            hash_label = HASH_NAMES.get(u32, "")
            if label and hash_label:
                label = f"{label} ({hash_label})"
            elif hash_label:
                label = hash_label
            fields.append(DecodedField(
                offset=i, size=4, type="hash", raw=raw, value=u32, label=label))
            i += 4
            continue

        # Small meaningful integers
        if u32 < 100000:
            if not label:
                if u32 == 1000:
                    label = "_cooltime?"
                elif u32 < 20:
                    label = "small_int"
                elif u32 == 10000:
                    label = "10000_value"
                elif u32 == 1000000:
                    label = "1M_ref"
            fields.append(DecodedField(
                offset=i, size=4, type="u32", raw=raw, value=u32, label=label))
            i += 4
            continue

        # Large int / hash / unknown — still record it
        if not label:
            label = f"0x{u32:08X}"
        fields.append(DecodedField(
            offset=i, size=4, type="u32", raw=raw, value=u32, label=label))
        i += 4

    return fields


def parse_pabgb(data_bytes: bytes, schema_bytes: bytes,
                table_name: str = "unknown",
                deep: bool = False) -> PabgbParser:
    """Parse a PABGB table using its PABGH schema index.

    Args:
        data_bytes: Raw .pabgb file contents
        schema_bytes: Raw .pabgh file contents
        table_name: Base name (e.g. "skill", "iteminfo") for count-size detection
        deep: If True, decode payload fields (floats, hashes, strings)
    """
    key_size, offsets = _parse_pabgh_index(schema_bytes, table_name)

    # Sort offsets to compute entry boundaries
    sorted_offs = sorted(set(offsets.values())) + [len(data_bytes)]

    entries = []
    for key, entry_off in sorted(offsets.items(), key=lambda x: x[1]):
        next_idx = sorted_offs.index(entry_off) + 1
        entry_end = sorted_offs[next_idx]
        entry_data = data_bytes[entry_off:entry_end]
        entry_size = entry_end - entry_off

        # Parse entry header
        eid, name, payload_start_rel = _parse_entry_header(entry_data, 0)
        payload = entry_data[payload_start_rel:]

        # Extract strings
        strings = _extract_strings(payload)

        entry = PabgbEntry(
            key=key,
            entry_id=eid,
            name=name,
            payload=payload,
            file_offset=entry_off,
            entry_size=entry_size,
            strings=strings,
        )

        if deep:
            entry.fields = _deep_decode_payload(payload, table_name)

        entries.append(entry)

    return PabgbParser(
        table_name=table_name,
        entries=entries,
        entry_count=len(entries),
        key_size=key_size,
        data_size=len(data_bytes),
        schema_size=len(schema_bytes),
    )


def parse_from_files(pabgb_path: str, pabgh_path: str = None,
                     deep: bool = False) -> PabgbParser:
    """Parse from file paths. If pabgh_path not given, derives from pabgb_path."""
    if pabgh_path is None:
        pabgh_path = pabgb_path.rsplit('.', 1)[0] + '.pabgh'

    table_name = os.path.splitext(os.path.basename(pabgb_path))[0]

    with open(pabgb_path, 'rb') as f:
        data_bytes = f.read()
    with open(pabgh_path, 'rb') as f:
        schema_bytes = f.read()

    return parse_pabgb(data_bytes, schema_bytes, table_name, deep)


def parse_from_game(game_dir: str, table_name: str, group: str = "0008",
                    deep: bool = False) -> PabgbParser:
    """Extract and parse a PABGB table directly from game PAZ archives.

    Requires crimson_rs to be installed.
    """
    from crimson.game_mods import crimson_rs as crimson_rs

    dir_path = "gamedata/binary__/client/bin"
    data_bytes = crimson_rs.extract_file(game_dir, group, dir_path, f"{table_name}.pabgb")
    schema_bytes = crimson_rs.extract_file(game_dir, group, dir_path, f"{table_name}.pabgh")

    return parse_pabgb(data_bytes, schema_bytes, table_name, deep)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse

    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser(
        description="Universal PABGB/PABGH parser for Crimson Desert game data")
    ap.add_argument("path", nargs="?", help="Path to .pabgb file (pabgh auto-detected)")
    ap.add_argument("--game", help="Game directory (extracts from PAZ archives)")
    ap.add_argument("--table", help="Table name (with --game, e.g. 'skill', 'iteminfo')")
    ap.add_argument("--group", default="0008", help="PAZ group (default: 0008)")
    ap.add_argument("--filter", "-f", help="Filter entries by name (substring)")
    ap.add_argument("--key", "-k", type=int, help="Get entry by key")
    ap.add_argument("--deep", "-d", action="store_true",
                    help="Deep decode payload (floats, hashes, strings)")
    ap.add_argument("--json", "-j", help="Output JSON to file")
    ap.add_argument("--hex", action="store_true", help="Include hex payload in JSON")
    ap.add_argument("--summary", "-s", action="store_true", help="Summary only")
    args = ap.parse_args()

    # Parse
    if args.game and args.table:
        parser = parse_from_game(args.game, args.table, args.group, deep=args.deep)
    elif args.path:
        parser = parse_from_files(args.path, deep=args.deep)
    else:
        ap.error("Provide a .pabgb file path or --game + --table")
        return

    # Filter
    if args.key is not None:
        entry = parser.get(args.key)
        entries = [entry] if entry else []
    elif args.filter:
        entries = parser.find(args.filter)
    else:
        entries = parser.entries

    # Output
    if args.summary:
        print(parser.summary())
        print(f"\nShowing {len(entries)}/{parser.entry_count} entries")
        print(f"{'Key':>10}  {'Name':<45}  {'Size':>6}  {'Strings':>4}")
        print("-" * 75)
        for e in entries:
            print(f"{e.key:>10}  {e.name:<45}  {e.entry_size:>6}  {len(e.strings):>4}")
        return

    if args.json:
        out = {
            "table": parser.table_name,
            "entry_count": parser.entry_count,
            "key_size": parser.key_size,
            "entries": [e.to_dict(include_hex=args.hex) for e in entries],
        }
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=2, ensure_ascii=False, default=str)
        print(f"Wrote {len(entries)} entries to {args.json}")
        return

    # Default: detailed output
    print(parser.summary())
    print(f"\nShowing {len(entries)}/{parser.entry_count} entries\n")

    for e in entries:
        print(f"{'=' * 60}")
        print(f"{e.name} (key={e.key}, id={e.entry_id}, {e.entry_size}B, "
              f"payload={len(e.payload)}B, offset=0x{e.file_offset:X})")
        if e.strings:
            print(f"  Strings: {e.strings}")

        if args.deep and e.fields:
            print(f"  Fields ({len(e.fields)}):")
            for fld in e.fields:
                print(f"    {fld}")
        elif not args.deep:
            # Quick hex dump of first 100 bytes
            for i in range(0, min(len(e.payload), 100), 16):
                chunk = e.payload[i:i + 16]
                hex_part = ' '.join(f'{b:02X}' for b in chunk)
                ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                print(f"    +{i:04X}: {hex_part:<48} {ascii_part}")
            if len(e.payload) > 100:
                print(f"    ... ({len(e.payload) - 100} more bytes)")
        print()


if __name__ == "__main__":
    main()
