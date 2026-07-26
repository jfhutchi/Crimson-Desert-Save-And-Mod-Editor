"""
reserveslot_parser.py — 100% roundtrip parser for reserveslot.pabgb/pabgh.

Controls the F1/F2 action wheel: which items/mounts/skills appear in radial menus.
27 entries as of game version 1.0.0.4.

Key modding use case: _enableVehicleList on VehicleSlot entries controls which
mount categories appear in the mount wheel. Adding all vehicle hashes to
VehicleSlot makes all mounts (dragon, ATAG, etc.) available from the main wheel.

Public API:
    parse_pabgh(pabgh_bytes) -> list[(key, offset)]
    parse_all(pabgh_bytes, pabgb_bytes) -> list[ReserveSlotEntry]
    serialize_entry(entry) -> bytes
    serialize_all(entries) -> (pabgh_bytes, pabgb_bytes)
    roundtrip_test(pabgh_bytes, pabgb_bytes) -> bool
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Tuple

__all__ = [
    "ReserveSlotEntry",
    "parse_pabgh",
    "parse_all",
    "serialize_entry",
    "serialize_all",
    "roundtrip_test",
    "VEHICLE_HASHES",
    "VEHICLE_NAMES",
    "USING_TYPE_NAMES",
    "ALL_KNOWN_VEHICLE_HASHES",
]


VEHICLE_NAMES = {
    0x4240: "Horse",
    0x4242: "Donkey",
    0x4246: "Wolf",
    0x4249: "Boar",
    0x424A: "Bear",
    0x424B: "Deer",
    0x424C: "Cucubird",
    0x424D: "Iguana",
    0x424E: "Birdsaurus",
    0x424F: "AlpineIbex",
    0x4253: "Camel",
    0x4254: "DragonRide",
    0x4258: "Dragon",
    0x425C: "WarMachine/ATAG",
    0x4261: "Elephant",
    0x4263: "Ox",
    0x4264: "MuleCart",
    0x4265: "HorseCart",
    0x4267: "MachineBird",
}

VEHICLE_HASHES = {v: k for k, v in VEHICLE_NAMES.items()}

ALL_KNOWN_VEHICLE_HASHES = sorted(VEHICLE_NAMES.keys())

USING_TYPE_NAMES = {
    0: "Item/Ammo",
    1: "Vehicle",
    2: "Skill",
    4: "FoodGroup",
    5: "Special",
    6: "Elemental",
    7: "FakeEquip",
    8: "SpecialAction",
}


@dataclass
class FillDataEntry:
    hash_key: int
    value: int
    extra: int


@dataclass
class ReserveSlotEntry:
    key: int
    string_key: bytes
    is_blocked: int
    time_limit: int
    cool_time: int
    auto_use_item_info: int
    convert_item_info: int
    fill_data_list: List[FillDataEntry] = field(default_factory=list)
    memo: bytes = b""
    reserve_slot_type: int = 0
    using_type: int = 0
    enable_tribe_list: List[int] = field(default_factory=list)
    enable_vehicle_list: List[int] = field(default_factory=list)
    enable_special_name_hash_list: List[bytes] = field(default_factory=list)
    target_item_group_list: List[int] = field(default_factory=list)
    send_gimmick_event_key: int = 0
    is_self_player_only: int = 0

    @property
    def name(self) -> str:
        return self.string_key.decode("utf-8", errors="replace")

    @property
    def using_type_name(self) -> str:
        return USING_TYPE_NAMES.get(self.using_type, f"Unknown({self.using_type})")

    @property
    def vehicle_names(self) -> List[str]:
        return [VEHICLE_NAMES.get(h, f"0x{h:04X}") for h in self.enable_vehicle_list]


def parse_pabgh(pabgh_bytes: bytes) -> List[Tuple[int, int]]:
    count = struct.unpack_from("<H", pabgh_bytes, 0)[0]
    entries = []
    for i in range(count):
        base = 2 + i * 8
        key = struct.unpack_from("<I", pabgh_bytes, base)[0]
        off = struct.unpack_from("<I", pabgh_bytes, base + 4)[0]
        entries.append((key, off))
    return entries


def _parse_entry(data: bytes, offset: int, end: int) -> ReserveSlotEntry:
    p = offset

    key = struct.unpack_from("<I", data, p)[0]; p += 4
    nl = struct.unpack_from("<I", data, p)[0]; p += 4
    string_key = data[p:p + nl]; p += nl
    is_blocked = data[p]; p += 1
    time_limit = struct.unpack_from("<q", data, p)[0]; p += 8
    cool_time = struct.unpack_from("<I", data, p)[0]; p += 4
    auto_use = struct.unpack_from("<I", data, p)[0]; p += 4
    convert = struct.unpack_from("<I", data, p)[0]; p += 4

    fill_cnt = struct.unpack_from("<I", data, p)[0]; p += 4
    fills = []
    for _ in range(fill_cnt):
        a, b, c = struct.unpack_from("<iii", data, p); p += 12
        fills.append(FillDataEntry(a, b, c))

    memo_len = struct.unpack_from("<I", data, p)[0]; p += 4
    memo = data[p:p + memo_len]; p += memo_len

    slot_type = data[p]; p += 1
    using_type = data[p]; p += 1

    tribe_cnt = struct.unpack_from("<I", data, p)[0]; p += 4
    tribes = [struct.unpack_from("<H", data, p + j * 2)[0] for j in range(tribe_cnt)]
    p += tribe_cnt * 2

    vehicle_cnt = struct.unpack_from("<I", data, p)[0]; p += 4
    vehicles = [struct.unpack_from("<H", data, p + j * 2)[0] for j in range(vehicle_cnt)]
    p += vehicle_cnt * 2

    target_cnt = struct.unpack_from("<I", data, p)[0]; p += 4
    targets = [struct.unpack_from("<H", data, p + j * 2)[0] for j in range(target_cnt)]
    p += target_cnt * 2

    gimmick = struct.unpack_from("<I", data, p)[0]; p += 4
    self_only = data[p]; p += 1
    specials = []

    return ReserveSlotEntry(
        key=key,
        string_key=string_key,
        is_blocked=is_blocked,
        time_limit=time_limit,
        cool_time=cool_time,
        auto_use_item_info=auto_use,
        convert_item_info=convert,
        fill_data_list=fills,
        memo=memo,
        reserve_slot_type=slot_type,
        using_type=using_type,
        enable_tribe_list=tribes,
        enable_vehicle_list=vehicles,
        enable_special_name_hash_list=specials,
        target_item_group_list=targets,
        send_gimmick_event_key=gimmick,
        is_self_player_only=self_only,
    )


def parse_all(pabgh_bytes: bytes, pabgb_bytes: bytes) -> List[ReserveSlotEntry]:
    index = parse_pabgh(pabgh_bytes)
    entries = []
    for i, (key, off) in enumerate(index):
        end = index[i + 1][1] if i + 1 < len(index) else len(pabgb_bytes)
        entries.append(_parse_entry(pabgb_bytes, off, end))
    return entries


def serialize_entry(entry: ReserveSlotEntry) -> bytes:
    parts = []
    parts.append(struct.pack("<I", entry.key))
    parts.append(struct.pack("<I", len(entry.string_key)))
    parts.append(entry.string_key)
    parts.append(struct.pack("<B", entry.is_blocked))
    parts.append(struct.pack("<q", entry.time_limit))
    parts.append(struct.pack("<I", entry.cool_time))
    parts.append(struct.pack("<I", entry.auto_use_item_info))
    parts.append(struct.pack("<I", entry.convert_item_info))
    parts.append(struct.pack("<I", len(entry.fill_data_list)))
    for f in entry.fill_data_list:
        parts.append(struct.pack("<iii", f.hash_key, f.value, f.extra))
    parts.append(struct.pack("<I", len(entry.memo)))
    parts.append(entry.memo)
    parts.append(struct.pack("<B", entry.reserve_slot_type))
    parts.append(struct.pack("<B", entry.using_type))
    parts.append(struct.pack("<I", len(entry.enable_tribe_list)))
    for v in entry.enable_tribe_list:
        parts.append(struct.pack("<H", v))
    parts.append(struct.pack("<I", len(entry.enable_vehicle_list)))
    for v in entry.enable_vehicle_list:
        parts.append(struct.pack("<H", v))
    parts.append(struct.pack("<I", len(entry.enable_special_name_hash_list)))
    for chunk in entry.enable_special_name_hash_list:
        parts.append(chunk)
    parts.append(struct.pack("<I", len(entry.target_item_group_list)))
    for v in entry.target_item_group_list:
        parts.append(struct.pack("<H", v))
    parts.append(struct.pack("<I", entry.send_gimmick_event_key))
    parts.append(struct.pack("<B", entry.is_self_player_only))
    return b"".join(parts)


def serialize_all(entries: List[ReserveSlotEntry]) -> Tuple[bytes, bytes]:
    body_parts = []
    offsets = []
    pos = 0
    for e in entries:
        offsets.append(pos)
        chunk = serialize_entry(e)
        body_parts.append(chunk)
        pos += len(chunk)

    header = struct.pack("<H", len(entries))
    for i, e in enumerate(entries):
        header += struct.pack("<II", e.key, offsets[i])

    return header, b"".join(body_parts)


def roundtrip_test(pabgh_bytes: bytes, pabgb_bytes: bytes) -> bool:
    try:
        entries = parse_all(pabgh_bytes, pabgb_bytes)
        new_h, new_b = serialize_all(entries)
        return new_h == pabgh_bytes and new_b == pabgb_bytes
    except Exception:
        return False
