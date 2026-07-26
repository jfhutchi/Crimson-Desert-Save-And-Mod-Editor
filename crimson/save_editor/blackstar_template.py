from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from crimson.save_editor.parc_inserter3 import ParsedInsertContext, iter_sentinels

NORMALIZED_BLACKSTAR_TEMPLATE = bytes.fromhex(
    "06000d19003e0a003b0000ffffffffffffffff110d2600000000005f450f00000000000000000000"
    "00000001001c3c0000ffffffffffffffff370d2600000000000100002f0000ffffffffffffffff4d"
    "0d260000000000040000000100002f0000ffffffffffffffff670d26000000000004000000010000"
    "2f0000ffffffffffffffff810d260000000000040000005200000000000000000000000000000000"
    "00000000000000000000000000000000000000000000000101000100000000000000000000000000"
    "00000004009f288d00120000ffffffffffffffffda0d260000000000010000000000000000000000"
    "1d4b0f0000000100000000000000ffff050005000000000000000000000000000000000100001a00"
    "00ffffffffffffffff1f0e260000000000040000000100001a0000ffffffffffffffff390e260000"
    "000000040000000100001a0000ffffffffffffffff530e260000000000040000000100001a0000ff"
    "ffffffffffffff6d0e260000000000040000000100001a0000ffffffffffffffff870e2600000000"
    "000400000001011d4b0f0001000000010000000000000001c800000001000101019a010000"
)
NORMALIZED_TEMPLATE_SHA256 = "a65b7d3bc22b1c83dc1eb30be85263def6dd682af8023851f1056ec7ea5841b2"
DYNAMIC_SLICES = {
    "mercenary_no": (31, 39), "owner_key": (39, 43),
    "last_paid_time": (147, 155), "last_breeding_time": (155, 163),
    "spawn_position": (163, 175), "spawn_yaw": (175, 179),
    "spawn_field_key": (179, 183), "item_no": (232, 240),
}
SOURCE_TYPE_OFFSETS = {
    8: "MercenarySaveData", 46: "ExperienceLevelSaveData",
    68: "FriendlyDailyCountSaveData", 94: "FriendlyDailyCountSaveData",
    120: "FriendlyDailyCountSaveData", 209: "ItemSaveData",
    278: "ItemSocketSaveData", 304: "ItemSocketSaveData",
    330: "ItemSocketSaveData", 356: "ItemSocketSaveData",
    382: "ItemSocketSaveData",
}


class BlackstarTemplateError(ValueError):
    pass


@dataclass(frozen=True)
class BlackstarDynamicValues:
    mercenary_no: int
    owner_key: int
    last_paid_time: int
    last_breeding_time: int
    spawn_position: bytes
    spawn_yaw: bytes
    spawn_field_key: int
    item_no: int


def _patch_int(data: bytearray, span: tuple[int, int], value: int) -> None:
    start, end = span
    if not 0 <= value < 1 << ((end - start) * 8):
        raise BlackstarTemplateError(f"Value {value} does not fit template field")
    data[start:end] = value.to_bytes(end - start, "little")


def _patch_bytes(data: bytearray, span: tuple[int, int], value: bytes) -> None:
    start, end = span
    if len(value) != end - start:
        raise BlackstarTemplateError("Template byte field has the wrong width")
    data[start:end] = value


def materialize_blackstar_template(
    context: ParsedInsertContext,
    insert_offset: int,
    values: BlackstarDynamicValues,
) -> bytes:
    if hashlib.sha256(NORMALIZED_BLACKSTAR_TEMPLATE).hexdigest() != NORMALIZED_TEMPLATE_SHA256:
        raise BlackstarTemplateError("Normalized template hash mismatch")
    record = bytearray(NORMALIZED_BLACKSTAR_TEMPLATE)
    for name in ("mercenary_no", "owner_key", "last_paid_time", "last_breeding_time", "spawn_field_key", "item_no"):
        _patch_int(record, DYNAMIC_SLICES[name], getattr(values, name))
    _patch_bytes(record, DYNAMIC_SLICES["spawn_position"], values.spawn_position)
    _patch_bytes(record, DYNAMIC_SLICES["spawn_yaw"], values.spawn_yaw)
    target_types = {item.name: item.index for item in context.parc.types}
    for offset, type_name in SOURCE_TYPE_OFFSETS.items():
        if type_name not in target_types:
            raise BlackstarTemplateError(f"Missing target type {type_name}")
        struct.pack_into("<H", record, offset, target_types[type_name])
    for sentinel in iter_sentinels(record, 0, len(record) - 4):
        struct.pack_into("<I", record, sentinel + 8, insert_offset + sentinel + 12)
    struct.pack_into("<I", record, len(record) - 4, len(record) - 4 - 23)
    if struct.unpack_from("<I", record, 27)[0] != 1000799:
        raise BlackstarTemplateError("Blackstar character key changed")
    if struct.unpack_from("<I", record, 240)[0] != 1002269:
        raise BlackstarTemplateError("Blackstar equipment key changed")
    return bytes(record)
