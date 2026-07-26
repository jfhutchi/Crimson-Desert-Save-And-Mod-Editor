"""Strict, idempotent Dragon-category patching for supported ReserveSlot data."""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, replace


ORIGINAL_SIGNATURE = b"\x02\x00\x00\x00\x4e\x51"
PATCHED_SIGNATURE = b"\x03\x00\x00\x00\x4e\x4f\x51"


class ReserveSlotCompatibilityError(ValueError):
    """Raised when ReserveSlot bytes do not match a supported exact schema."""


@dataclass(frozen=True)
class ReserveSlotProfile:
    schema_id: str
    entry_count: int
    target_key: int
    target_index: int
    target_name: bytes
    original_pabgh_sha256: str
    original_pabgb_sha256: str
    patched_pabgh_sha256: str
    patched_pabgb_sha256: str

    def with_hashes(self, **changes: str) -> "ReserveSlotProfile":
        return replace(self, **changes)


CURRENT_PROFILE = ReserveSlotProfile(
    schema_id="reserveslot-2026-07-11-30-entry-u8-categories",
    entry_count=30,
    target_key=1_000_006,
    target_index=26,
    target_name=b"VehicleSlot",
    original_pabgh_sha256=(
        "d4e041a8c744e4bc2585ff09b75df20e0d06d20d300ace25fb15ddac8baadcd3"
    ),
    original_pabgb_sha256=(
        "292b384a5a9e16d24c59a5cc44fe9b1aca835eec870c54e703f870b2a9682938"
    ),
    patched_pabgh_sha256=(
        "18304bd6b0692427a39775258f77900f498305f57e1a45662c175548d378fd64"
    ),
    patched_pabgb_sha256=(
        "09dbcf9ace932ca1df39d2c06d862958453e018f358fcc6cf987186746200d64"
    ),
)


@dataclass(frozen=True)
class DragonWheelReport:
    schema_id: str
    state: str
    source_pabgh_sha256: str
    source_pabgb_sha256: str
    output_pabgh_sha256: str
    output_pabgb_sha256: str
    before_categories: tuple[int, ...]
    after_categories: tuple[int, ...]
    adjusted_entry_indexes: tuple[int, ...]
    byte_growth: int
    already_enabled: bool


@dataclass(frozen=True)
class DragonWheelPatchResult:
    pabgh: bytes
    pabgb: bytes
    report: DragonWheelReport


def analyze_reserveslot(
    pabgh: bytes,
    pabgb: bytes,
    profile: ReserveSlotProfile = CURRENT_PROFILE,
) -> DragonWheelReport:
    state, _, _, _, _ = _inspect(pabgh, pabgb, profile)
    categories = (0x4E, 0x51) if state == "original" else (0x4E, 0x4F, 0x51)
    pabgh_hash, pabgb_hash = _sha(pabgh), _sha(pabgb)
    return DragonWheelReport(
        schema_id=profile.schema_id,
        state=state,
        source_pabgh_sha256=pabgh_hash,
        source_pabgb_sha256=pabgb_hash,
        output_pabgh_sha256=pabgh_hash,
        output_pabgb_sha256=pabgb_hash,
        before_categories=categories,
        after_categories=categories,
        adjusted_entry_indexes=(),
        byte_growth=0,
        already_enabled=state == "patched",
    )


def enable_dragon_category(
    pabgh: bytes,
    pabgb: bytes,
    profile: ReserveSlotProfile = CURRENT_PROFILE,
) -> DragonWheelPatchResult:
    before = analyze_reserveslot(pabgh, pabgb, profile)
    if before.already_enabled:
        return DragonWheelPatchResult(pabgh, pabgb, before)

    _, _, target_start, _, signature_offset = _inspect(pabgh, pabgb, profile)
    count_offset = target_start + signature_offset
    insert_offset = count_offset + 5
    candidate_h = bytearray(pabgh)
    candidate_b = bytearray(pabgb)
    struct.pack_into("<I", candidate_b, count_offset, 3)
    candidate_b[insert_offset:insert_offset] = b"\x4f"

    adjusted = tuple(range(profile.target_index + 1, profile.entry_count))
    for index in adjusted:
        offset_position = 2 + index * 8 + 4
        old_offset = struct.unpack_from("<I", candidate_h, offset_position)[0]
        struct.pack_into("<I", candidate_h, offset_position, old_offset + 1)

    output_h, output_b = bytes(candidate_h), bytes(candidate_b)
    candidate = analyze_reserveslot(output_h, output_b, profile)
    if candidate.state != "patched":
        raise ReserveSlotCompatibilityError("Candidate did not validate as patched")

    inverse_h = bytearray(output_h)
    inverse_b = bytearray(output_b)
    for index in adjusted:
        offset_position = 2 + index * 8 + 4
        old_offset = struct.unpack_from("<I", inverse_h, offset_position)[0]
        struct.pack_into("<I", inverse_h, offset_position, old_offset - 1)
    del inverse_b[insert_offset]
    struct.pack_into("<I", inverse_b, count_offset, 2)
    if bytes(inverse_h) != pabgh or bytes(inverse_b) != pabgb:
        raise ReserveSlotCompatibilityError("Candidate inverse check failed")

    report = DragonWheelReport(
        schema_id=profile.schema_id,
        state="patched",
        source_pabgh_sha256=before.source_pabgh_sha256,
        source_pabgb_sha256=before.source_pabgb_sha256,
        output_pabgh_sha256=candidate.output_pabgh_sha256,
        output_pabgb_sha256=candidate.output_pabgb_sha256,
        before_categories=(0x4E, 0x51),
        after_categories=(0x4E, 0x4F, 0x51),
        adjusted_entry_indexes=adjusted,
        byte_growth=1,
        already_enabled=False,
    )
    return DragonWheelPatchResult(output_h, output_b, report)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_index(
    pabgh: bytes,
    pabgb: bytes,
    profile: ReserveSlotProfile,
) -> tuple[tuple[int, int], ...]:
    expected_size = 2 + profile.entry_count * 8
    if len(pabgh) != expected_size:
        raise ReserveSlotCompatibilityError(
            f"PABGH size {len(pabgh)} does not match {expected_size}"
        )
    count = struct.unpack_from("<H", pabgh, 0)[0]
    if count != profile.entry_count:
        raise ReserveSlotCompatibilityError(f"Unexpected entry count {count}")
    if not 0 <= profile.target_index < count - 1:
        raise ReserveSlotCompatibilityError("Target index has no following boundary")

    entries = tuple(
        struct.unpack_from("<II", pabgh, 2 + index * 8)
        for index in range(count)
    )
    offsets = [offset for _, offset in entries]
    if not offsets or offsets[0] != 0:
        raise ReserveSlotCompatibilityError("PABGH offsets do not start at zero")
    if any(left >= right for left, right in zip(offsets, offsets[1:])):
        raise ReserveSlotCompatibilityError("PABGH offsets are not strictly ordered")
    if offsets[-1] >= len(pabgb):
        raise ReserveSlotCompatibilityError("PABGH offset is outside PABGB")
    if entries[profile.target_index][0] != profile.target_key:
        raise ReserveSlotCompatibilityError("Target key is not at the supported index")
    if sum(key == profile.target_key for key, _ in entries) != 1:
        raise ReserveSlotCompatibilityError("Target key is not unique")
    return entries


def _inspect(
    pabgh: bytes,
    pabgb: bytes,
    profile: ReserveSlotProfile,
) -> tuple[str, tuple[tuple[int, int], ...], int, int, int]:
    hashes = (_sha(pabgh), _sha(pabgb))
    original = (profile.original_pabgh_sha256, profile.original_pabgb_sha256)
    patched = (profile.patched_pabgh_sha256, profile.patched_pabgb_sha256)
    if hashes == original:
        state, signature = "original", ORIGINAL_SIGNATURE
    elif hashes == patched:
        state, signature = "patched", PATCHED_SIGNATURE
    else:
        raise ReserveSlotCompatibilityError(
            f"Unsupported ReserveSlot hashes: {hashes[0]} / {hashes[1]}"
        )

    entries = _parse_index(pabgh, pabgb, profile)
    target_start = entries[profile.target_index][1]
    target_end = entries[profile.target_index + 1][1]
    record = pabgb[target_start:target_end]
    if len(record) < 8:
        raise ReserveSlotCompatibilityError("Target record is truncated")
    embedded_key, name_size = struct.unpack_from("<II", record, 0)
    name_end = 8 + name_size
    if name_end > len(record):
        raise ReserveSlotCompatibilityError("Target record name is truncated")
    if embedded_key != profile.target_key or record[8:name_end] != profile.target_name:
        raise ReserveSlotCompatibilityError("Target record key or name changed")
    if record.count(signature) != 1:
        raise ReserveSlotCompatibilityError("Target category signature is not unique")
    other_signature = (
        PATCHED_SIGNATURE if state == "original" else ORIGINAL_SIGNATURE
    )
    if other_signature in record:
        raise ReserveSlotCompatibilityError(
            "Target contains conflicting category signatures"
        )
    return state, entries, target_start, target_end, record.index(signature)
