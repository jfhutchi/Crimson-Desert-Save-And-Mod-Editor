"""skilltreeinfo.pabgb parser / serializer.

Binary format (from IDA decompilation of sub_141066AF0):

pabgh index:
    u16 count
    per entry: u32 key + u32 offset (8 bytes)

pabgb record:
    u32  key
    u32  name_length
    char[name_length]  name (ASCII)
    ... variable-length fields (skill tree nodes, child arrays, etc.)

For the MVP we parse key+name and treat remaining bytes as an opaque blob.
Root package IDs are located by scanning for known u32 LE patterns.
"""

from __future__ import annotations
import struct
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── character / category metadata ─────────────────────────────────────
CHARACTER_MAP: dict[int, str] = {
    1: "Kliff", 2: "Kliff", 3: "Kliff", 4: "Kliff",
    8: "Kliff", 9: "Kliff", 10: "Kliff", 50: "Kliff",
    11: "Oongka", 12: "Oongka", 13: "Oongka",
    18: "Oongka", 20: "Oongka", 51: "Oongka",
    21: "Damiane", 22: "Damiane", 23: "Damiane",
    28: "Damiane", 30: "Damiane", 52: "Damiane",
    31: "Yahn", 38: "Yahn", 40: "Yahn", 53: "Yahn",
    101: "Faction", 102: "Faction", 103: "Faction",
    104: "Faction", 105: "Faction", 106: "Faction",
    201: "Craft",
}

MAIN_TREE_KEYS = {50, 51, 52}  # Skill_Kliff, Skill_Oongka, Skill_Damian

# Localized display names (from localizationstring_eng_items.tsv)
DISPLAY_NAMES: dict[int, str] = {
    1: "Weapon Skills: Sword",
    2: "Weapon Skills: Shield",
    3: "Weapon Skills: Bow",
    4: "Weapon Skills: Spear",
    8: "Martial Arts (Kliff)",
    9: "Special Skills",
    10: "Secondary Skills (Kliff)",
    50: "Kliff Skills",
    51: "Oongka Skills",
    11: "Weapon Skills: Greataxe",
    12: "Weapon Skills: Blaster",
    13: "Weapon Skills: Axe",
    18: "Martial Arts (Oongka)",
    20: "Secondary Skills (Oongka)",
    52: "Damiane Skills",
    21: "Weapon Skills: Rapier",
    22: "Weapon Skills: Pistol",
    23: "Weapon Skills: Longsword",
    28: "Martial Arts (Damiane)",
    30: "Secondary Skills (Damiane)",
    31: "Weapon Skills: Dagger",
    38: "Martial Arts (Yahn)",
    40: "Secondary Skills (Yahn)",
    53: "Yahn Skills",
    101: "Pororin",
    102: "Scholastone Institute",
    103: "Urdavah",
    104: "Ironflame Steelworks",
    105: "Delesyia Royal Institute",
    106: "Dewhaven Keep",
    201: "Kuku Pot",
}

# Known root melee package IDs (u32 LE)
ROOT_PACKAGES: dict[str, int] = {
    "Kliff Melee":   0x32C9,   # 13001
    "Oongka Melee":  0x3391,   # 13201
    "Damiane Melee": 0x332D,   # 13101
}

# Character key → their native melee root package
CHAR_MELEE_ROOT: dict[int, int] = {
    50: 0x32C9,  # Skill_Kliff  → Kliff Melee
    51: 0x3391,  # Skill_Oongka → Oongka Melee
    52: 0x332D,  # Skill_Damian → Damiane Melee
}


def _category_from_name(name: str) -> str:
    """Derive a human-readable category from the record name."""
    if name.startswith("WeaponSkill_"):
        return "Weapon"
    if name.startswith("MaterialArtSkill_"):
        return "MaterialArt"
    if name.startswith("SpecialSkill_"):
        return "Special"
    if name.startswith("SubSkill_"):
        return "Sub"
    if name.startswith("FactionTree_"):
        return "Faction"
    if name.startswith("CraftTree_"):
        return "Craft"
    if name.startswith("Skill_"):
        return "Main"
    return "Unknown"


# ── pabgh index ───────────────────────────────────────────────────────

def parse_pabgh(pabgh: bytes) -> list[tuple[int, int]]:
    """Parse pabgh index → list of (key, offset) tuples."""
    count = struct.unpack_from('<H', pabgh, 0)[0]
    entries: list[tuple[int, int]] = []
    p = 2
    for _ in range(count):
        k = struct.unpack_from('<I', pabgh, p)[0]; p += 4
        o = struct.unpack_from('<I', pabgh, p)[0]; p += 4
        entries.append((k, o))
    return entries


def serialize_pabgh(entries: list[tuple[int, int]]) -> bytes:
    """Serialize (key, offset) list back to pabgh bytes."""
    out = struct.pack('<H', len(entries))
    for k, o in entries:
        out += struct.pack('<II', k, o)
    return out


# ── record class ──────────────────────────────────────────────────────

class SkillTreeRecord:
    """One skill tree entry from skilltreeinfo.pabgb.

    Stores key + name parsed, with remaining data as an opaque blob
    to guarantee byte-exact round-trip serialization.
    """
    __slots__ = ('key', 'name', 'tail_data')

    def __init__(self, key: int = 0, name: str = '',
                 tail_data: bytes = b''):
        self.key = key
        self.name = name
        self.tail_data = tail_data  # everything after key+name_len+name

    @property
    def character(self) -> str:
        return CHARACTER_MAP.get(self.key, "Unknown")

    @property
    def category(self) -> str:
        return _category_from_name(self.name)

    @property
    def display_name(self) -> str:
        return DISPLAY_NAMES.get(self.key, self.name)

    @property
    def is_main_tree(self) -> bool:
        return self.key in MAIN_TREE_KEYS

    @staticmethod
    def from_bytes(data: bytes, start: int, end: int) -> 'SkillTreeRecord':
        """Parse one record from pabgb given its boundary offsets."""
        pos = start
        key = struct.unpack_from('<I', data, pos)[0]; pos += 4
        name_len = struct.unpack_from('<I', data, pos)[0]; pos += 4
        name = data[pos:pos + name_len].decode('ascii', errors='replace')
        pos += name_len
        tail_data = bytes(data[pos:end])
        return SkillTreeRecord(key, name, tail_data)

    def to_bytes(self) -> bytes:
        """Serialize record back to binary."""
        name_bytes = self.name.encode('ascii')
        out = struct.pack('<I', self.key)
        out += struct.pack('<I', len(name_bytes))
        out += name_bytes
        out += self.tail_data
        return out

    def find_root_packages(self) -> list[tuple[int, int]]:
        """Scan tail_data for known root package u32 values.

        Returns list of (rel_offset_in_tail, value) for each match.
        """
        results: list[tuple[int, int]] = []
        for label, pkg_id in ROOT_PACKAGES.items():
            target = struct.pack('<I', pkg_id)
            pos = 0
            while True:
                pos = self.tail_data.find(target, pos)
                if pos < 0:
                    break
                results.append((pos, pkg_id))
                pos += 1
        return sorted(results, key=lambda x: x[0])

    def patch_root_package(self, old_id: int, new_id: int) -> int:
        """Replace all occurrences of old_id with new_id in tail_data.

        Returns count of replacements made.
        """
        old_bytes = struct.pack('<I', old_id)
        new_bytes = struct.pack('<I', new_id)
        tail = bytearray(self.tail_data)
        count = 0
        pos = 0
        while True:
            pos = tail.find(old_bytes, pos)
            if pos < 0:
                break
            tail[pos:pos + 4] = new_bytes
            count += 1
            pos += 4
        if count > 0:
            self.tail_data = bytes(tail)
        return count

    def __repr__(self) -> str:
        return (f"SkillTreeRecord(key={self.key}, name={self.name!r}, "
                f"char={self.character}, cat={self.category}, "
                f"size={len(self.to_bytes())}B)")


# ── top-level API ─────────────────────────────────────────────────────

def parse_all(pabgh: bytes, pabgb: bytes) -> list[SkillTreeRecord]:
    """Parse pabgh index + pabgb body → list of records.

    Records are returned in the original pabgh key order (not sorted by offset).
    """
    index = parse_pabgh(pabgh)
    # Build offset → end mapping from sorted offsets
    sorted_by_off = sorted(index, key=lambda e: e[1])
    end_map: dict[int, int] = {}
    for i, (k, o) in enumerate(sorted_by_off):
        end_map[o] = sorted_by_off[i + 1][1] if i + 1 < len(sorted_by_off) else len(pabgb)

    records: list[SkillTreeRecord] = []
    for k, o in index:
        rec = SkillTreeRecord.from_bytes(pabgb, o, end_map[o])
        records.append(rec)
    return records


def serialize_all(records: list[SkillTreeRecord]) -> tuple[bytes, bytes]:
    """Serialize records → (pabgh_bytes, pabgb_bytes).

    Records are written in the order they appear in the list.
    pabgh entries are emitted in the same order.
    """
    pabgb = bytearray()
    offsets: list[tuple[int, int]] = []
    for rec in records:
        offsets.append((rec.key, len(pabgb)))
        pabgb.extend(rec.to_bytes())
    pabgh = serialize_pabgh(offsets)
    return bytes(pabgh), bytes(pabgb)


def roundtrip_test(pabgh: bytes, pabgb: bytes) -> bool:
    """Parse then serialize and verify byte-exact match."""
    records = parse_all(pabgh, pabgb)
    new_pabgh, new_pabgb = serialize_all(records)
    ok = True
    if new_pabgh != pabgh:
        log.error("pabgh mismatch: orig=%d new=%d", len(pabgh), len(new_pabgh))
        ok = False
    if new_pabgb != pabgb:
        for i in range(min(len(new_pabgb), len(pabgb))):
            if new_pabgb[i] != pabgb[i]:
                log.error("pabgb first diff at 0x%X: orig=0x%02X new=0x%02X",
                          i, pabgb[i], new_pabgb[i])
                break
        log.error("pabgb mismatch: orig=%d new=%d", len(pabgb), len(new_pabgb))
        ok = False
    return ok


# ======================================================================
# skilltreegroupinfo.pabgb — maps characters to skill tree keys
# ======================================================================

# Vanilla group → tree key mappings (for reference / reset)
VANILLA_GROUP_KEYS: dict[int, list[int]] = {
    1000000: [50],           # Skill_TreeGroup_Kliff
    1000001: [51],           # Skill_TreeGroup_Oongka
    1000002: [52],           # Skill_TreeGroup_Damian
    1000007: [1, 2, 3, 4],  # WeaponSkill_TreeGroup_Kliff
    1000011: [11, 12, 13],  # WeaponSkill_TreeGroup_Oongka
    1000014: [21, 22, 23],  # WeaponSkill_TreeGroup_Damian
}

# Character name → (main_group_key, weapon_group_key)
CHAR_GROUPS: dict[str, tuple[int, int]] = {
    "Kliff":   (1000000, 1000007),
    "Oongka":  (1000001, 1000011),
    "Damiane": (1000002, 1000014),
}


class TreeGroupRecord:
    """One entry from skilltreegroupinfo.pabgb.

    Structure: key + name + flag(u8) + tree_keys(u32[]) + footer(70B).
    """
    __slots__ = ('key', 'name', 'flag', 'tree_keys', 'footer')

    def __init__(self, key: int = 0, name: str = '', flag: int = 0,
                 tree_keys: Optional[list[int]] = None,
                 footer: bytes = b''):
        self.key = key
        self.name = name
        self.flag = flag
        self.tree_keys = tree_keys or []
        self.footer = footer

    @staticmethod
    def from_bytes(data: bytes, start: int, end: int) -> 'TreeGroupRecord':
        pos = start
        key = struct.unpack_from('<I', data, pos)[0]; pos += 4
        name_len = struct.unpack_from('<I', data, pos)[0]; pos += 4
        name = data[pos:pos + name_len].decode('ascii', errors='replace')
        pos += name_len
        flag = data[pos]; pos += 1
        count = struct.unpack_from('<I', data, pos)[0]; pos += 4
        tree_keys = []
        for _ in range(count):
            tk = struct.unpack_from('<I', data, pos)[0]; pos += 4
            tree_keys.append(tk)
        footer = bytes(data[pos:end])
        return TreeGroupRecord(key, name, flag, tree_keys, footer)

    def to_bytes(self) -> bytes:
        name_bytes = self.name.encode('ascii')
        out = struct.pack('<I', self.key)
        out += struct.pack('<I', len(name_bytes))
        out += name_bytes
        out += struct.pack('<B', self.flag)
        out += struct.pack('<I', len(self.tree_keys))
        for tk in self.tree_keys:
            out += struct.pack('<I', tk)
        out += self.footer
        return out

    def __repr__(self) -> str:
        return f"TreeGroupRecord(key={self.key}, name={self.name!r}, tree_keys={self.tree_keys})"


def parse_groups(pabgh: bytes, pabgb: bytes) -> list[TreeGroupRecord]:
    """Parse skilltreegroupinfo pabgh+pabgb."""
    index = parse_pabgh(pabgh)
    sorted_by_off = sorted(index, key=lambda e: e[1])
    end_map: dict[int, int] = {}
    for i, (k, o) in enumerate(sorted_by_off):
        end_map[o] = sorted_by_off[i + 1][1] if i + 1 < len(sorted_by_off) else len(pabgb)
    records: list[TreeGroupRecord] = []
    for k, o in index:
        rec = TreeGroupRecord.from_bytes(pabgb, o, end_map[o])
        records.append(rec)
    return records


def serialize_groups(records: list[TreeGroupRecord]) -> tuple[bytes, bytes]:
    """Serialize TreeGroupRecords → (pabgh, pabgb)."""
    pabgb = bytearray()
    offsets: list[tuple[int, int]] = []
    for rec in records:
        offsets.append((rec.key, len(pabgb)))
        pabgb.extend(rec.to_bytes())
    pabgh = serialize_pabgh(offsets)
    return bytes(pabgh), bytes(pabgb)
